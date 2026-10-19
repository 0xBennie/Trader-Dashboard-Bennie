const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8').split('function tradesView()')[0];
function renderExternal(){
 const journal={id:'external-test',exchange:'variational',symbol:'DEMO',market:'Variational 永续',title:'DEMO 记录',direction:'LONG',status:'持仓中',entry_price:'100.25',quantity:'2.4',stop_loss:'95.25',take_profit:'110.25',opened_at:'',timeframe:'30m',risk:{valid:true,rr:'2',risk:'12'},screenshot_snapshot:{mark_price:'99.25',unrealized_pnl:'-2.40',reported_roi_pct:'-10.00',notional:'238.20',liquidation_price:'50',orders:[{kind:'take_profit',price:'110.25',close_position:true},{kind:'stop_loss',price:'95.25',close_position:true}]}};
 const c=vm.createContext({JournalAssistant:require('../static/assistant.js'),JournalMentor:require('../static/mentor.js'),JournalMoney:require("../static/money.js"),JournalLearning:require('../static/learning.js'),JournalWorkflow:require('../static/workflow.js'),navigator:{onLine:true},fixture:{journals:[journal],analyses:[],snapshot:{captured_at:Date.now(),positions:[{symbol:'DEMO',entryPrice:'999',positionAmt:'10000',unRealizedProfit:'555'}]},recorder_settings:{auto_ai:true}},window:{localStorage:{getItem:()=>null},fetch:()=>{}},JournalClient:{draftStore:()=>({read:()=>null}),createApi:()=>{}},JournalRecorder:{price:v=>String(v),market:()=>{throw Error('Binance market must not render')},observations:()=>{throw Error('Binance observations must not render')}},JournalDashboard:{cycleSummary:()=>''}});
 return vm.runInContext(source+'\nstate=fixture;selectedId="external-test";journalView()',c);
}
test('external record keeps screenshot provenance and USD without borrowing Binance position',()=>{
 const html=renderExternal();
 assert.match(html,/Variational · 截图记录/);
 assert.match(html,/记录入场价（USD）/);
 assert.match(html,/100\.25/);
 assert.match(html,/实际入场时间/);
 assert.match(html,/仅截图证据 · 无成交账单/);
 assert.match(html,/尚未接入/);
 assert.match(html,/−12\.00 USD/);
 assert.doesNotMatch(html,/999|10,000|555|开仓 \/ 平仓自动复盘已开启|USDT/);
});
test('dashboard keeps manual external records discoverable outside Binance totals',()=>{
 const d=require('../static/dashboard.js');
 const html=d.overview({journals:[{id:'demo',exchange:'variational',symbol:'DEMO',status:'持仓中'}]});
 assert.match(html,/账户覆盖状态/);assert.match(html,/查看 Binance 交易/);assert.match(html,/data-open-journal="demo"/);assert.match(html,/不含 Variational/);
});
