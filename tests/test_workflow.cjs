const test=require('node:test'),assert=require('node:assert/strict');
const w=require('../static/workflow.js');
const journal={id:'j',symbol:'ETHUSDT',thesis:'区间收复',updated:100};
const ready={ai:{available:true}};
test('AI entry only offers generation when connected, online and evidence exists',()=>{
 assert.equal(w.aiEntry(null,journal).status,'loading');
 assert.equal(w.aiEntry({},journal).action,'ai-settings');
 assert.equal(w.aiEntry(ready,journal,[],false).disabled,true);
 assert.equal(w.aiEntry(ready,null).disabled,true);
 assert.equal(w.aiEntry(ready,{id:'empty'}).action,'dashboard-journal');
 assert.equal(w.aiEntry(ready,journal).action,'analyze');
 assert.equal(w.aiEntry(ready,{id:'screenshot',images:[{url:'/assets/example.png'}]}).action,'analyze');
});
test('running, failed, stale and configured transitions are consistent',()=>{
 assert.equal(w.aiEntry(ready,journal,[{status:'running'}]).disabled,true);
 assert.equal(w.aiEntry(ready,journal,[{status:'failed'}]).status,'failed');
 assert.equal(w.aiEntry(ready,journal,[{status:'complete',journal_updated:90}]).status,'stale');
 assert.equal(w.aiEntry({},journal,[{status:'complete',journal_updated:90}]).action,'ai-settings');
 assert.match(w.aiButton({},journal,[]),/配置 AI 服务/);
 assert.doesNotMatch(w.aiButton({},journal,[]),/data-action="analyze"/);
});
test('secondary destinations keep the correct primary navigation group',()=>{
 assert.equal(w.group('review'),'progress');assert.equal(w.group('analytics'),'training');assert.equal(w.group('trades'),'journal');
 assert.match(w.navigation('review'),/data-view="review" aria-current="page"/);
});
test('review workspace presents closed trades rather than completion targets and escapes user text',()=>{
 const p={personal:{numerator:1,denominator:1},ai:{numerator:0,denominator:1},pending:[],completed:[{id:'x" onclick="evil',symbol:'<img>',personal_complete:true}]};
 const html=w.progress({review_progress:p});assert.match(html,/最近交易，直接回看/);assert.match(html,/1 笔有本人笔记/);assert.doesNotMatch(html,/<img>|onclick="evil|<progress|待复盘|完成率/);
 p.personal={numerator:0,denominator:0};p.ai.denominator=0;p.completed=[];
 assert.match(w.progress({review_progress:p}),/暂时没有已确认平仓/);
});
test('review rows use verified cycle results, retain cost gaps and order by close time',()=>{
 global.JournalDashboard=require('../static/dashboard.js');
 try{
 const rows=[{id:'old',symbol:'ETHUSDT',closed_at:100,ai_complete:true},{id:'new',symbol:'BTCUSDT',closed_at:200}];
 const state={review_progress:{personal:{numerator:0,denominator:2},ai:{numerator:1,denominator:2},pending:rows,completed:[]},journals:rows.map(j=>({...j,exchange:'binance',direction:'LONG',reflection:'<script> note',execution:{opened_ms:1,closed_ms:j.closed_at,history_complete:j.id==='old',realized_pnl:'20',fees:{USDT:'1',BNB:'0.1'}}}))};
 const html=w.progress(state);assert.ok(html.indexOf('BTCUSDT')<html.indexOf('ETHUSDT'));assert.match(html,/\+19\.00/);assert.match(html,/资金费未分摊/);assert.match(html,/其他币种费用/);assert.match(html,/AI 报告已保存/);assert.match(html,/&lt;script&gt;/);assert.match(html,/待核对/);assert.doesNotMatch(html,/data-focus-field="reflection"/);
 }finally{delete global.JournalDashboard;}
});
