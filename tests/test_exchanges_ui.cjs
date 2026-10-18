const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const exchanges=require('../static/exchanges.js'),dashboard=require('../static/dashboard.js');
const now=Date.now();
const account=()=>({configured:true,enabled:true,sync:{status:'complete',last_success:now},snapshot:{captured_at:now,positions:[{symbol:'XYZUSDT',positionAmt:'2',unRealizedProfit:'4'}],coverage:{days:30},wallet:{equity:'1000',scope:'USDT 币种权益'}},totals:{USDT:{net:'20'}},stop_coverage:{snapshot_fresh:true,captured_at:now,rows:[]}});
test('account cards distinguish configured, paused, failed and stale snapshots',()=>{
 const a=account();assert.equal(exchanges.status(a,now),'自动记录中');
 a.sync.status='retrying';assert.match(exchanges.status(a,now),/重试/);
 assert.doesNotMatch(dashboard.apiAccount('okx',a,{},now),/自动记录中/);
 a.enabled=false;assert.equal(exchanges.status(a,now),'已暂停');
 a.enabled=true;a.sync.status='complete';assert.equal(exchanges.status(a,now+90001),'快照已过期');
 assert.equal(exchanges.status({},now),'未连接');
});
test('new credentials have separate forms and OKX requires a passphrase',()=>{
 assert.match(exchanges.fields('okx'),/name="passphrase" type="password" required/);
 assert.doesNotMatch(exchanges.fields('bybit'),/name="passphrase"/);
 assert.match(exchanges.fields('bybit'),/UTA/);
 assert.match(exchanges.fields('bybit'),/每 60 秒/);
});
test('new accounts are not counted as Variational screenshot journals or Binance evidence',()=>{
 const j={id:'okx1',exchange:'okx',symbol:'XYZUSDT',status:'持仓中'};
 const s={journals:[j],exchanges:{okx:account(),bybit:account()}};
 const html=dashboard.accountCoverage(s,now);
 assert.match(html,/>OKX</);assert.match(html,/>Bybit</);
 assert.doesNotMatch(html,/1 笔记录待核对/);
 assert.match(html,/data-exchange-filter="okx"/);
 assert.equal(exchanges.account(s,'okx'),s.exchanges.okx);
 assert.equal(exchanges.account(s,'binance'),s);
});
test('account errors and unsupported credential fields cannot inject HTML',()=>{
 const a=account();a.sync.error='<img src=x onerror=alert(1)>';
 const html=dashboard.apiAccount('okx',a,{},now);
 assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});
test('OKX journal uses OKX position, fee unit and order context',()=>{
 const source=fs.readFileSync('static/app.js','utf8').split('function tradesView()')[0];
 const j={id:'okx1',exchange:'okx',symbol:'XYZUSDT',status:'持仓中',direction:'LONG',entry_price:'100',quantity:'2',risk:{valid:false,reason:'待补充'},execution:{positionSide:'BOTH'}};
 const fixture={journals:[j],analyses:[],snapshot:{positions:[{symbol:'XYZUSDT',positionAmt:'999',entryPrice:'999'}]},exchanges:{okx:{snapshot:{captured_at:now,positions:[{symbol:'XYZUSDT',positionSide:'BOTH',positionAmt:'2',entryPrice:'100',markPrice:'102',unRealizedProfit:'4'}]},trade_context:{}}}};
 const c=vm.createContext({JournalAssistant:require('../static/assistant.js'),JournalMoney:require("../static/money.js"),JournalLearning:require('../static/learning.js'),fixture,navigator:{onLine:true},JournalWorkflow:require('../static/workflow.js'),window:{localStorage:{getItem:()=>null},fetch:()=>{}},JournalClient:{draftStore:()=>({read:()=>null}),createApi:()=>{}},JournalRecorder:{price:v=>String(v),market:()=>'',observations:()=>''},JournalDashboard:dashboard});
 const html=vm.runInContext(source+'\nstate=fixture;selectedId="okx1";journalView()',c);
 assert.match(html,/OKX · USDT 永续/);assert.match(html,/记录入场价（USDT）/);
 assert.doesNotMatch(html,/999|Variational · 截图记录|尚未接入 Variational/);
});
