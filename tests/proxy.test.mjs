import test from 'node:test';
import assert from 'node:assert/strict';
import worker, {handle, TARGETS} from '../scheduler/proxy/worker.mjs';

const env = {PROXY_SECRET:'test-private-secret'};
const request = (path, method='GET') => new Request('https://proxy.test'+path, {method,headers:{'X-Market-Secret':env.PROXY_SECRET}});
test('Cloudflare entrypoint does not use execution context as fetch function',async()=>{
  const original = globalThis.fetch;
  globalThis.fetch = async()=>new Response('{"status":"ok"}');
  try {
    const response = await worker.fetch(request('/nobitex_usdt'),env,{waitUntil(){}});
    assert.equal(response.status,200);
  } finally { globalThis.fetch=original; }
});
test('unauthenticated and unconfigured requests never fetch',async()=>{
  const noFetch=()=>assert.fail('unexpected fetch');
  assert.equal((await handle(new Request('https://proxy.test/nobitex_usdt'),env,noFetch)).status,404);
  assert.equal((await handle(request('/nobitex_usdt'),{},noFetch)).status,503);
});
test('rejects URLs, unknown IDs, queries and non-GET',async()=>{
  for(const req of [request('/https://evil.test'),request('/constructor'),request('/nobitex_usdt?url=x'),request('/nobitex_usdt','POST')])
    assert.equal((await handle(req,env,()=>assert.fail('unexpected fetch'))).status,404);
});
test('fixed target, no redirect, no forwarded credential, no cookies',async()=>{
  const response=await handle(request('/nobitex_usdt'),env,async(url,options)=>{
    assert.equal(url,TARGETS.nobitex_usdt);
    assert.equal(options.redirect,'manual');
    assert.equal(options.headers['X-Market-Secret'],undefined);
    return new Response('{"status":"ok"}',{headers:{'Set-Cookie':'secret'}});
  });
  assert.equal(response.status,200);
  assert.equal(response.headers.get('set-cookie'),null);
  assert.equal(response.headers.get('cache-control'),'no-store');
});
test('upstream errors and redirects cannot return a success',async()=>{
  for(const status of [302,403,500]){
    const res=await handle(request('/ramzinex_usdt'),env,async()=>new Response('',{status}));
    assert.equal(res.status,status===302?502:status);
  }
});
test('invalid JSON and excessive body rejected',async()=>{
  for(const body of ['html', 'x'.repeat(2_000_001)])
    assert.equal((await handle(request('/nobitex_usdt'),env,async()=>new Response(body))).status,502);
});
