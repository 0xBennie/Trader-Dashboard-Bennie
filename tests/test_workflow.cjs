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
test('progress distinguishes a fully reviewed period from no eligible history and escapes user text',()=>{
 const p={personal:{numerator:1,denominator:1},ai:{numerator:0,denominator:1},pending:[],completed:[{id:'x" onclick="evil',symbol:'<img>',personal_complete:true}]};
 const html=w.progress({review_progress:p});assert.match(html,/已完成 1 \/ 1/);assert.match(html,/已全部完成/);assert.doesNotMatch(html,/<img>|onclick="evil/);
 p.personal={numerator:0,denominator:0};p.ai.denominator=0;p.completed=[];
 assert.match(w.progress({review_progress:p}),/暂时没有已确认平仓/);assert.doesNotMatch(w.progress({review_progress:p}),/已全部完成/);
});
