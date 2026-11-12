// Private incident archive. Separate from price proxy and scheduler.
const FEED = 'https://hamed-zeidabadi.github.io/iran-market-feed/api/prices.json';
const RUNS = 'https://api.github.com/repos/hamed-zeidabadi/iran-market-feed/actions/workflows/update.yml/runs?per_page=5';
const HEADERS = {'User-Agent':'IranMarketFeed/0.1 incident monitor','Cache-Control':'no-cache'};
const MAX_BYTES = 1_500_000;
const DAYS = 210;
const PAGE_SIZE = 40;
const stores = env => [...new Set([env.DB,env.DB1,env.DB2].filter(Boolean))];
const json = (body, status=200) => Response.json(body,{status,headers:{'Cache-Control':'no-store'}});

export async function bounded(stream, limit=MAX_BYTES) {
  if (!stream) throw new Error('empty body');
  const reader = stream.getReader(), parts=[]; let length=0;
  try {
    for (;;) {
      const {value,done}=await reader.read(); if(done)break;
      length+=value.byteLength;
      if(length>limit){await reader.cancel();throw new Error('body too large');}
      parts.push(value);
    }
  } finally {reader.releaseLock();}
  const result=new Uint8Array(length);let offset=0;
  for(const part of parts){result.set(part,offset);offset+=part.length;}
  return result;
}
const compress = async value => bounded(new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream('gzip')));
const expand = async bytes => JSON.parse(new TextDecoder().decode(await bounded(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')))));

async function authorized(request, secret) {
  if (!secret) return false;
  const hash = async s=>new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)));
  const [a,b]=await Promise.all([hash(secret),hash(request.headers.get('X-Audit-Secret')||'')]);
  let mismatch=0;for(let i=0;i<a.length;i++)mismatch|=a[i]^b[i];
  return mismatch===0;
}

export function validate(batch, now=Date.now()) {
  if (!batch || typeof batch !== 'object' || !/^[a-zA-Z0-9_.:-]{1,160}$/.test(batch.id||'') ||
      !['collection','publication','workflow','watchdog'].includes(batch.kind) ||
      !Number.isFinite(Date.parse(batch.at)) || Date.parse(batch.at)>now+300000 || Date.parse(batch.at)<now-DAYS*86400000 ||
      !Array.isArray(batch.records) || !batch.records.length || batch.records.length>2000)
    throw new Error('invalid batch');
  for (const r of batch.records) {
    if (!r || typeof r!=='object' || !/^[a-z_]{1,80}$/.test(r.event||'') ||
        !['info','warning','error'].includes(r.severity) || !Number.isFinite(Date.parse(r.at)))
      throw new Error('invalid record');
  }
  if (batch.state && (batch.kind!=='collection' || batch.state.version!==1 || typeof batch.state.sources!=='object'))
    throw new Error('invalid operational state');
}

export async function save(env, batch, now=Date.now()) {
  validate(batch,now);
  const packed = await compress(batch.records);
  if(packed.length>64000)throw new Error('packed journal too large');
  const databases=stores(env);
  const target=databases[new Date(batch.at).getUTCMonth()%databases.length];
  // Preserve original run time for export/replay; receipt time detects silence.
  await target.prepare('INSERT OR IGNORE INTO batches(id,at_ms,received_ms,kind,payload) VALUES(?,?,?,?,?)')
    .bind(batch.id,Date.parse(batch.at),now,batch.kind,packed.buffer).run();
  if(batch.state){
    const state = await compress(batch.state);
    await env.DB.prepare('INSERT INTO operational_state(id,at_ms,payload) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET at_ms=excluded.at_ms,payload=excluded.payload WHERE excluded.at_ms>=operational_state.at_ms')
      .bind(Date.parse(batch.at),state.buffer).run();
  }
  return batch.id;
}

export async function handle(request, env) {
  if(!await authorized(request,env.AUDIT_SECRET))return new Response('Not found',{status:404});
  const url=new URL(request.url);
  try {
    if(url.pathname==='/ingest' && request.method==='POST' && !url.search){
      const compressed=await bounded(request.body);
      let batch;
      try{batch=await expand(compressed);validate(batch);}catch{return json({error:'invalid batch'},400);}
      return json({stored:await save(env,batch)},201);
    }
    if(url.pathname==='/state' && request.method==='GET' && !url.search){
      const row=await env.DB.prepare('SELECT payload FROM operational_state WHERE id=1').first();
      if(!row)return new Response(null,{status:204,headers:{'Cache-Control':'no-store'}});
      return new Response(new Uint8Array(row.payload),{headers:{'Content-Type':'application/gzip','Cache-Control':'no-store'}});
    }
    if(url.pathname==='/export' && request.method==='GET'){
      for(const name of url.searchParams.keys())if(!['from','to','cursor','upper'].includes(name))return json({error:'invalid query'},400);
      const from=url.searchParams.get('from'),to=url.searchParams.get('to');
      if(!/^\d{4}-\d{2}-\d{2}$/.test(from||'') || !/^\d{4}-\d{2}-\d{2}$/.test(to||''))return json({error:'date required'},400);
      const start=Date.parse(from+'T00:00:00Z'),end=Date.parse(to+'T00:00:00Z')+86400000;
      const databases=stores(env);
      const cursor=(url.searchParams.get('cursor')||databases.map(()=>0).join(',')).split(',').map(Number);
      if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start||cursor.length!==databases.length||cursor.some(n=>!Number.isSafeInteger(n)||n<0))return json({error:'invalid range'},400);
      const upper=url.searchParams.has('upper')?url.searchParams.get('upper').split(',').map(Number):await Promise.all(databases.map(async db=>(await db.prepare('SELECT COALESCE(MAX(seq),0) AS n FROM batches').first()).n));
      if(upper.length!==databases.length||upper.some(n=>!Number.isSafeInteger(n)||n<0))return json({error:'invalid cursor'},400);
      const pages=await Promise.all(databases.map((db,i)=>db.prepare('SELECT seq,id,kind,at_ms,received_ms,hex(payload) AS packed_hex FROM batches WHERE at_ms>=? AND at_ms<? AND seq>? AND seq<=? ORDER BY seq LIMIT '+PAGE_SIZE)
        .bind(start,end,cursor[i],upper[i]).all()));
      const rows=pages.flatMap((page,i)=>page.results.map(r=>({...r,store:i})));
      const next=pages.map((page,i)=>page.results.at(-1)?.seq??cursor[i]);
      return json({rows,upper:upper.join(','),next_cursor:pages.some(p=>p.results.length===PAGE_SIZE)?next.join(','):null});
    }
    if(url.pathname==='/health' && request.method==='GET' && !url.search){
      const rows=await Promise.all(stores(env).map(db=>db.prepare('SELECT COUNT(*) AS batches,MIN(at_ms) AS first_at_ms,MAX(at_ms) AS last_at_ms,COALESCE(SUM(LENGTH(payload)),0) AS packed_bytes FROM batches').first()));
      return json({retention_days:DAYS,batches:rows.reduce((n,r)=>n+r.batches,0),packed_bytes:rows.reduce((n,r)=>n+r.packed_bytes,0),stores:rows});
    }
    if(url.pathname==='/check' && request.method==='POST' && !url.search){
      const records=await watch(env);
      return json({checked:true,events:records.map(r=>r.event)});
    }
    return new Response('Not found',{status:404});
  }catch(error){
    console.error(JSON.stringify({event:'archive_request_failed',error_type:error?.name||'Error'}));
    return json({error:'archive request failed'},500);
  }
}

function event(name,severity,fields={},now=Date.now()){
  return {log_version:1,event:name,severity,at:new Date(now).toISOString(),
    at_tehran:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tehran',dateStyle:'short',timeStyle:'medium'}).format(new Date(now)),
    phase:'watchdog',...fields};
}

export async function watch(env, fetchImpl=fetch, now=Date.now()){
  const records=[];
  try{
    const response=await fetchImpl(FEED+'?watchdog='+now,{headers:HEADERS,signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('HTTP_'+response.status);
    const data=JSON.parse(new TextDecoder().decode(await bounded(response.body,2_000_000)));
    const stamp=Date.parse(data.datetime),age=(now-stamp)/1000;
    if(!Number.isFinite(stamp))throw new Error('invalid_feed_time');
    if(age < -120)records.push(event('public_output_future','error',{report_at:data.datetime},now));
    else if(age>1200)records.push(event('public_output_stale','error',{report_at:data.datetime,age_seconds:age},now));
    records.push(event('watchdog_heartbeat','info',{report_at:data.datetime,age_seconds:age},now));
  }catch(error){records.push(event('public_output_unreachable','error',{error_type:error.name,reason:/^HTTP_\d+$/.test(error.message)?error.message:'network_or_invalid_response'},now));}
  let check='archive_last_collection';
  try{
    const latest=await Promise.all(stores(env).map(async db=>(await db.prepare("SELECT MAX(received_ms) AS n FROM batches WHERE kind='collection'").first()).n||0));
    const last=Math.max(...latest);
    if(!last || now-last>1200000)records.push(event('collector_archive_silent','error',{last_received_at_ms:last||null},now));
    if(env.CHECK_GITHUB_STATUS==='true'){
    check='github_workflow_status';
    const response=await fetchImpl(RUNS,{headers:{...HEADERS,Accept:'application/vnd.github+json'},signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('HTTP_'+response.status);
    const runs=JSON.parse(new TextDecoder().decode(await bounded(response.body,500000))).workflow_runs;
    for(const run of runs||[]){
      if(run.status==='completed' && !['success','neutral','skipped'].includes(run.conclusion) && now-Date.parse(run.updated_at)<3600000){
        const record=event('workflow_failed',run.conclusion==='cancelled'?'warning':'error',{
          run_id:String(run.id),run_attempt:String(run.run_attempt),run_url:run.html_url,
          conclusion:run.conclusion,workflow_updated_at:run.updated_at},now);
        await save(env,{id:`workflow-failure:${run.id}:${run.run_attempt}`,kind:'workflow',at:new Date(now).toISOString(),records:[record]},now);
      }
    }
    }
  }catch(error){records.push(event('watchdog_check_failed','error',{check,error_type:error.name,
    http_status:/^HTTP_\d+$/.test(error.message)?Number(error.message.slice(5)):null},now));}
  await save(env,{id:'watchdog:'+now,kind:'watchdog',at:new Date(now).toISOString(),records},now);
  // Once daily, indexed bounded retention cleanup and storage-pressure record.
  if(new Date(now).getUTCHours()===0 && new Date(now).getUTCMinutes()<10){
    for(const [index,db] of stores(env).entries()){
      await db.prepare('DELETE FROM batches WHERE seq IN (SELECT seq FROM batches WHERE at_ms<? LIMIT 1000)').bind(now-DAYS*86400000).run();
      const size=await db.prepare('SELECT COALESCE(SUM(LENGTH(payload)),0) AS n FROM batches').first();
      if(size.n>350000000)await save(env,{id:'storage:'+index+':'+now,kind:'watchdog',at:new Date(now).toISOString(),records:[event('archive_storage_pressure','error',{store:index,packed_bytes:size.n},now)]},now);
    }
  }
  return records;
}

export default {
  fetch:handle,
  async scheduled(controller,env,ctx){ctx.waitUntil(watch(env));},
};
