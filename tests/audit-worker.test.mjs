import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {handle,save,watch,validate} from '../scheduler/audit/worker.mjs';

function database(){
  const db=new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../scheduler/audit/schema.sql',import.meta.url),'utf8'));
  const prepare=sql=>{
    let args=[];
    const params=()=>args.map(v=>v instanceof ArrayBuffer?new Uint8Array(v):v);
    const command={
      bind(...values){args=values;return command;},
      async first(){return db.prepare(sql).get(...params())||null;},
      async all(){return {results:db.prepare(sql).all(...params())};},
      async run(){return db.prepare(sql).run(...params());},
    };return command;
  };
  return {db,env:{AUDIT_SECRET:'test-secret',DB:{prepare,async batch(commands){db.exec('BEGIN');try{const result=[];for(const c of commands)result.push(await c.run());db.exec('COMMIT');return result;}catch(e){db.exec('ROLLBACK');throw e;}}}}};
}
const now=Date.now();
const batch=(id='run:1:collection')=>({id,kind:'collection',at:new Date(now).toISOString(),records:[{at:new Date(now).toISOString(),event:'source_result',severity:'error',source:'test',http_status:403}]});
const gzip=async value=>new Uint8Array(await new Response(new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
const request=(path,method='GET',body)=>new Request('https://audit.test'+path,{method,headers:{'X-Audit-Secret':'test-secret'},body});

test('unauthenticated callers cannot read, write or inspect archive',async()=>{
  for(const path of ['/ingest','/state','/export','/health','/check']){
    const response=await handle(new Request('https://audit.test'+path),{AUDIT_SECRET:'secret'});
    assert.equal(response.status,404);
  }
});
test('compressed ingest is durable, idempotent, exportable and restores state',async()=>{
  const {db,env}=database();
  try{
    const data={...batch(),state:{version:1,sources:{sample:{price:100}}}};
    for(let i=0;i<2;i++)assert.equal((await handle(request('/ingest','POST',await gzip(data)),env)).status,201);
    assert.equal(db.prepare('SELECT COUNT(*) AS n FROM batches').get().n,1);
    const restored=await handle(request('/state'),env);
    const plain=await new Response(restored.body.pipeThrough(new DecompressionStream('gzip'))).json();
    assert.deepEqual(plain,data.state);
    const day=new Date(now).toISOString().slice(0,10);
    const page=await (await handle(request(`/export?from=${day}&to=${day}`),env)).json();
    assert.equal(page.rows.length,1);
    const records=JSON.parse(await new Response(new Blob([Buffer.from(page.rows[0].packed_hex,'hex')]).stream().pipeThrough(new DecompressionStream('gzip'))).text());
    assert.deepEqual(records,data.records);
  }finally{db.close();}
});
test('older operational states cannot overwrite a newer baseline',async()=>{
  const {db,env}=database();
  try{
    await save(env,{...batch('new'),state:{version:1,sources:{new:1}}},now);
    await save(env,{...batch('old'),at:new Date(now-1000).toISOString(),state:{version:1,sources:{old:1}}},now);
    const response=await handle(request('/state'),env);
    const state=await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
    assert.deepEqual(state.sources,{new:1});
  }finally{db.close();}
});
test('invalid IDs, invalid phases, record shape and time are rejected',()=>{
  for(const changes of [{id:'../../escape'},{kind:'other'},{records:[]},{at:'bad'},{records:[{event:'x',severity:'unknown',at:new Date(now).toISOString()}]}])
    assert.throws(()=>validate({...batch(),...changes},now));
});
test('export rejects SQL injection and unknown query parameters',async()=>{
  const {db,env}=database();
  try{
    for(const query of ['from=2026-01-01&to=2026-01-02&cursor=1%20OR%201=1','from=x&to=y','from=2026-01-01&to=2026-01-02&sql=select'])
      assert.equal((await handle(request('/export?'+query),env)).status,400);
  }finally{db.close();}
});
test('watchdog archives stale publication, collector silence and failed Actions',async()=>{
  const {db,env}=database();
  env.CHECK_GITHUB_STATUS='true';
  try{
    const fetcher=async url=>url.includes('api.github.com')?Response.json({workflow_runs:[{id:1,run_attempt:1,status:'completed',conclusion:'failure',updated_at:new Date(now-1000).toISOString(),html_url:'https://github.com/test'}]}):Response.json({datetime:new Date(now-1800000).toISOString()});
    const records=await watch(env,fetcher,now);
    assert.ok(records.some(r=>r.event==='public_output_stale'));
    assert.ok(records.some(r=>r.event==='collector_archive_silent'));
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM batches WHERE kind='workflow'").get().n,1);
    await watch(env,fetcher,now+1000);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM batches WHERE kind='workflow'").get().n,1);
  }finally{db.close();}
});
test('network failures are logged without saving arbitrary error messages',async()=>{
  const {db,env}=database();
  try{
    const records=await watch(env,async()=>{throw new Error('secret-value-in-error');},now);
    assert.ok(records.some(r=>r.event==='public_output_unreachable'));
    assert.ok(!JSON.stringify(records).includes('secret-value'));
  }finally{db.close();}
});
test('monthly storage shards export through one archive without losing rows',async()=>{
  const entries=[database(),database(),database()];
  const env={...entries[0].env,DB1:entries[1].env.DB,DB2:entries[2].env.DB};
  try{
    for(let i=0;i<3;i++){
      const date=new Date(now);date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()-i);
      await save(env,{...batch('month-'+i),at:date.toISOString()},now);
    }
    for(const entry of entries)assert.equal(entry.db.prepare('SELECT COUNT(*) AS n FROM batches').get().n,1);
    const end=new Date(now).toISOString().slice(0,10);
    const page=await (await handle(request(`/export?from=2025-01-01&to=${end}`),env)).json();
    assert.equal(page.rows.length,3);
    assert.equal(page.upper.split(',').length,3);
  }finally{entries.forEach(e=>e.db.close());}
});
test('export cursor is stable if newer rows arrive while exporting',async()=>{
  const {db,env}=database();
  try{
    for(let i=0;i<41;i++)await save(env,batch('row-'+i),now);
    const day=new Date(now).toISOString().slice(0,10);
    const page=await (await handle(request(`/export?from=${day}&to=${day}`),env)).json();
    assert.equal(page.rows.length,40);
    await save(env,batch('later-row'),now);
    const next=await (await handle(request(`/export?from=${day}&to=${day}&cursor=${page.next_cursor}&upper=${page.upper}`),env)).json();
    assert.equal(next.rows.length,1);
    assert.equal(next.next_cursor,null);
  }finally{db.close();}
});
