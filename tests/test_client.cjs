const test=require('node:test'), assert=require('node:assert/strict');
const {createApi,changedFields,draftStore}=require('../static/journal-client.js');
const response=(status,body)=>({status,ok:status>=200&&status<300,json:async()=>body});
test('stale session is renewed once and retries the unchanged draft',async()=>{
  let token='old';const calls=[],body={thesis:'保留我的文字'};
  const api=createApi(async(path,options)=>{calls.push({path,options});return calls.length===1?response(403,{code:'SESSION_EXPIRED'}):calls.length===2?response(200,{csrf:'new'}):response(200,{saved:true});},()=>token,v=>token=v);
  assert.deepEqual(await api('/api/journals',body),{saved:true});
  assert.equal(calls.length,3);assert.equal(calls[2].options.headers['X-Journal-Token'],'new');
  assert.equal(calls[0].options.body,calls[2].options.body);
});
test('origin rejection, uncertain server errors and conflicts are never replayed',async()=>{
  for(const [status,code] of [[403,'ORIGIN_REJECTED'],[500,'ERROR'],[409,'JOURNAL_CONFLICT']]){
    let calls=0;const api=createApi(async()=>{calls++;return response(status,{code,error:'rejected'});},()=>'',()=>{});
    await assert.rejects(api('/api/journals',{}));assert.equal(calls,1);
  }
});
test('a second stale rejection does not loop',async()=>{
  let calls=0;const api=createApi(async()=>{calls++;return calls===2?response(200,{csrf:'new'}):response(403,{code:'SESSION_EXPIRED'});},()=>'',()=>{});
  await assert.rejects(api('/api/journals',{}));assert.equal(calls,3);
});
test('expired login redirects once without retrying a write',async()=>{
  let calls=0,redirects=0;const api=createApi(async()=>{calls++;return response(401,{code:'LOGIN_REQUIRED'});},()=>'',()=>{},()=>redirects++);
  await assert.rejects(api('/api/journals',{}));assert.equal(calls,1);assert.equal(redirects,1);
});
test('session recovery redirects when authentication expired',async()=>{
  let calls=0,redirects=0;const api=createApi(async()=>{calls++;return calls===1?response(403,{code:'SESSION_EXPIRED'}):response(401,{code:'LOGIN_REQUIRED'});},()=>'',()=>{},()=>redirects++);
  await assert.rejects(api('/api/journals',{}));assert.equal(calls,2);assert.equal(redirects,1);
});
test('draft survives a refresh without overwriting untouched server fields',()=>{
  const map=new Map(),storage={getItem:k=>map.get(k),setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};
  const store=draftStore(storage),base={thesis:'old',stop_loss:''},values={thesis:'new',stop_loss:''};
  assert.equal(store.write('a',base,values),true);
  assert.deepEqual(store.restore({thesis:'old',stop_loss:'2400'},store.read('a')),{thesis:'new',stop_loss:'2400'});
  assert.deepEqual(changedFields(base,values),{thesis:'new'});assert.equal(store.read('b'),null);
  store.remove('a');assert.equal(store.read('a'),null);
});
