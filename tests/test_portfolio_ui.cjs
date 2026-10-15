const test=require('node:test'),assert=require('node:assert/strict');
const money=require('../static/money.js'),ui=require('../static/portfolio.js'),dashboard=require('../static/dashboard.js');
test('decimal display never passes exact amounts through binary Number',()=>{
 assert.equal(money.format('9007199254740993.125'),'9,007,199,254,740,993.13');
 assert.equal(money.format('-0.005'),'-0.01');assert.equal(money.format('-0.0001'),'0.00');
 assert.equal(money.format('1.001e-2',5),'0.01001');
 assert.equal(money.format('0.000000001',9),'0.000000001');
 for(const v of [null,undefined,'','NaN','Infinity','<script>','1e9999'])assert.equal(money.format(v),'—');
});
const zero={realized:'0',fees:'0',funding:'0',net:'0'};
const state={credentials_configured:true,snapshot:{captured_at:100},exchanges:{okx:{configured:true}}};
const report={available:true,accounts:[{name:'Binance',exchange:'binance',totals:{USDT:{...zero,net:'9007199254740993.125'}}},{name:'OKX',exchange:'okx',totals:{USDT:zero}}],window:{start:1,end:1000,shortened:true},totals:{USDT:{...zero,net:'9007199254740993.125'},BNB:{...zero,net:'-0.001'}},audit:{ledger_count:3},excluded:[{exchange:'variational',reason:'未接入'}]};
test('combined ledger states currency exclusions, actual period and exact account amounts',()=>{
 const html=ui.render(state,report,null,30);
 assert.match(html,/9,007,199,254,740,993.13/);assert.match(html,/未覆盖所选全部时间/);
 assert.match(html,/BNB/);assert.match(html,/未假设 USD、USDT、USDC 等价/);assert.match(html,/未接入/);
 assert.match(html,/data-portfolio-exchange="bybit"[^>]*disabled/);
 assert.match(html,/相同订单编号跨交易所分别计算/);
});
test('incomplete ledger cannot show a total, escapes messages, keeps selected sources',()=>{
 const html=ui.render(state,{available:false,issues:['<script>']},['okx'],7);
 assert.match(html,/暂不能计算/);assert.match(html,/未知金额不会按 0/);assert.match(html,/&lt;script&gt;/);
 assert.doesNotMatch(html,/portfolio-total|<script>/);assert.match(html,/data-portfolio-exchange="okx" checked/);
 assert.doesNotMatch(html,/data-portfolio-exchange="binance" checked/);
});
test('homepage uses exact combined income and marks unavailable income unknown',()=>{
 const input={...state,portfolio:report,recorder_settings:{},journals:[]};
 assert.match(dashboard.overview(input),/9,007,199,254,740,993.13/);
 const html=dashboard.overview({...input,portfolio:{available:false}});
 assert.match(html,/合并已实现净额/);assert.match(html,/账单范围待核对/);assert.doesNotMatch(html,/9,007/);
});
