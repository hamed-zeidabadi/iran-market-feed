import test from 'node:test';
import assert from 'node:assert/strict';
import {dispatch} from '../scheduler/cloudflare/worker.mjs';
const env={GITHUB_TOKEN:'test-secret',GITHUB_REPOSITORY:'owner/repo',WORKFLOW_FILE:'update.yml',WORKFLOW_REF:'main'};
const now=Date.now();
test('a stale queued run cannot permanently block the scheduler',async()=>{
  let calls=0;
  const result=await dispatch(env,async(url,options)=>{
    calls++;
    if(calls===1){assert.ok(url.includes('branch=main'));return Response.json({workflow_runs:[{id:1,head_branch:'main',created_at:new Date(now-86400000).toISOString()}]});}
    assert.equal(options.method,'POST');return new Response(null,{status:204});
  },now);
  assert.equal(calls,2);assert.equal(result.dispatched,true);assert.deepEqual(result.ignored_stale_queued_runs,[1]);
});
test('a recent queued run still suppresses duplicate ticks',async()=>{
  let calls=0;
  const result=await dispatch(env,async()=>{calls++;return Response.json({workflow_runs:[{id:2,head_branch:'main',created_at:new Date(now-60000).toISOString()}]});},now);
  assert.equal(calls,1);assert.equal(result.skipped,'recent run already queued');
});
test('another branch and an invalid timestamp cannot block main',async()=>{
  let calls=0;
  const result=await dispatch(env,async()=>{calls++;return calls===1?Response.json({workflow_runs:[{id:3,head_branch:'other',created_at:new Date(now).toISOString()},{id:4,head_branch:'main',created_at:'invalid'}]}):new Response(null,{status:204});},now);
  assert.equal(result.dispatched,true);
});
