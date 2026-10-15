const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../static/training.js');
const fs=require('node:fs');

test('an edited or offline plan cannot be confirmed, even after prior AI support',()=>{
  const ready={status:'awaiting_confirmation',blockers:[],cooldown_until:null};
  assert.equal(ui.canConfirm(ready,false,true),true);
  assert.equal(ui.canConfirm(ready,true,true),false);
  assert.equal(ui.canConfirm(ready,false,false),false);
  assert.equal(ui.canConfirm({...ready,blockers:[{code:'PRICE_STALE'}]},false,true),false);
  assert.equal(ui.canConfirm({...ready,cooldown_until:2000},false,true,1000),false);
  for(const status of ['reviewing','review_failed','rejected','cooldown','permitted','matched','expired','cancelled','invalidated'])assert.equal(ui.canConfirm({...ready,status},false,true),false,status);
});
test('risk estimate uses quantity and absolute stop distance, while missing inputs stay unknown',()=>{
  assert.equal(ui.estimatedRisk({entry_price:'100',stop_loss:'95',quantity:'2'}),10);
  assert.equal(ui.estimatedRisk({entry_price:'100',stop_loss:'105',quantity:'2'}),10);
  assert.equal(ui.estimatedRisk({entry_price:'100',stop_loss:'',quantity:'2'}),null);
  assert.equal(ui.estimatedRisk({entry_price:'100',stop_loss:'95',quantity:'0'}),null);
});
test('draft identity and exact decimal values survive reload and storage failure is explicit',()=>{
  const m=new Map(),s={getItem:k=>m.get(k),setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)},d=ui.draftStore(s);
  const p={...ui.emptyIntent(),id:'saved-plan',version:4,entry_price:'0.00000117',thesis:'保留我的判断'};
  assert.equal(d.write(p),true);assert.deepEqual(d.latest().intent,p);assert.deepEqual(ui.draftStore(s).read('saved-plan').intent,p);
  assert.equal(ui.draftStore({setItem(){throw Error('quota')},getItem(){throw Error('privacy')}}).write(p),false);
  d.remove('saved-plan');assert.equal(d.read('saved-plan'),null);
});
test('all financially meaningful plan changes invalidate the local approval view',()=>{
  const p={...ui.emptyIntent(),quantity:'1',stop_loss:'90',entry_price:'100'};
  assert.equal(ui.changed(p,{...p}),false);
  for(const field of ['symbol','direction','quantity','entry_price','stop_loss','take_profit','thesis','invalidation'])assert.equal(ui.changed(p,{...p,[field]:'changed'}),true,field);
});
test('absence of behavioral samples and financial limits remains explicit',()=>{
  assert.equal(ui.ratio({numerator:0,denominator:0,ratio:null}),'尚无样本');
  assert.equal(ui.ratio({numerator:1,denominator:4,ratio:'0.25'}),'25%');
  assert.equal(ui.emptyIntent().entry_price,'');assert.equal(ui.emptyIntent().quantity,'');
});
test('evidence images cannot inject external requests or executable attributes',()=>{
  assert.equal(ui.safeImage('/assets/hypothesis/a.png'),'/assets/hypothesis/a.png');
  for(const p of ['https://example.org/a.png','//example.org/a.png','/assets/../api/state','/assets/a.png" onerror="bad()','javascript:alert(1)'])assert.equal(ui.safeImage(p),'');
});
test('login upgrade purges former offline pages and never serves cached private routes',async()=>{
  const vm=require('node:vm'),listeners={},deleted=[];let completed;
  const context={self:{addEventListener:(name,fn)=>listeners[name]=fn,skipWaiting:async()=>{},clients:{claim:async()=>{}}},caches:{keys:async()=>['trade-training-shell-v1','other-app'],delete:async name=>deleted.push(name)}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/sw.js'),'utf8'),context);
  listeners.activate({waitUntil:p=>completed=p});await completed;
  assert.deepEqual(deleted,['trade-training-shell-v1']);assert.equal(listeners.fetch,undefined);
  assert.ok(JSON.parse(fs.readFileSync(require.resolve('../static/manifest.webmanifest'),'utf8')).icons.some(i=>i.sizes==='512x512'));
});

test('remaining training screens render unconfigured and supported server states without invented defaults',async()=>{
  const vm=require('node:vm'),listeners={},storage=new Map();
  const context={module:{exports:{}},console,Date,Intl,URL,setTimeout,clearTimeout,setInterval(){},navigator:{onLine:true},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},addEventListener(){},document:{addEventListener:(name,fn)=>listeners[name]=fn,querySelector:()=>null}};
  context.globalThis=context;vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../static/training.js'),'utf8'),context);
  const app=context.module.exports;
  const requests=[];
  const today={policy:{configured:false,version:0,max_trade_risk_usdt:null,max_daily_loss_usdt:null,max_entries_per_day:null,cooldown_seconds:null,permit_seconds:300,price_tolerance_pct:'0.2'},intents:[],tasks:[],activities:[],metrics:{pretrade_coverage:{numerator:0,denominator:0,ratio:null}},risk_budget:{daily_realized_pnl_usdt:null},audits:[]};
  app.init({accountState:()=>({ai:{available:true}}),openAISettings(){},api:async path=>{requests.push(path);return path.endsWith('today')?today:{items:[]};},render(){},toast(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(app.render('training'),/风险政策尚未启用/);
  listeners.click({target:{closest:()=>({dataset:{training:'policy-open'}})}});
  await new Promise(resolve=>setImmediate(resolve));
  const html=app.render('training');
  assert.match(html,/name="max_trade_risk_usdt" value=""/);assert.match(html,/name="max_daily_loss_usdt" value=""/);assert.match(html,/name="cooldown_seconds" value=""/);assert.match(app.render('progress'),/尚无样本/);
  assert.deepEqual(Array.from(app.routes),['training','progress']);assert.doesNotMatch(app.render('training'),/策略实验|历史模拟证据|沿用策略/);assert.match(app.render('progress'),/上方本人复盘正常统计/);
  listeners.click({target:{closest:()=>({dataset:{training:'new'}})}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(app.render('training'),/training-intent-form/);assert.match(app.render('training'),/保存草稿/);assert.deepEqual(requests,['/api/v1/training/today']);
  listeners.click({target:{closest:()=>({dataset:{training:'step',step:'2'}})}});
  await new Promise(resolve=>setImmediate(resolve));assert.match(app.render('training'),/核对计划，再开始审核/);assert.doesNotMatch(app.render('training'),/backtest_id|strategy_id|rules_confirmed/);
});

test('HTTP 200 confirmation with a new blocker never reports a successful permit',()=>{
  for(const status of ['needs_info','cooldown','review_failed','rejected','expired','cancelled']){
    const result=ui.confirmationOutcome({status,blockers:[{code:'PRICE_CHANGED',message:'当前价格已超出计划容差'}],permit:null});
    assert.equal(result.success,false,status);assert.match(result.message,/价格已超出/);assert.doesNotMatch(result.message,/有效计划已生成/);
  }
  assert.equal(ui.confirmationOutcome({status:'permitted',permit:null}).success,false);
  assert.equal(ui.confirmationOutcome({status:'permitted',permit:{id:'p',revoked_at:123}}).success,false);
  assert.equal(ui.confirmationOutcome({status:'permitted',permit:{id:'p'}}).success,true);
  assert.match(ui.confirmationOutcome({status:'matched',permit:{id:'p'}}).message,/已关联成交/);
});
