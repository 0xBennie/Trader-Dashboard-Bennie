const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../static/assistant.js');
const bar={time:1000,close_time:1999,open:'100',high:'102',low:'98',close:'101',volume:'4'};
test('chart excludes future bars in decision-only view and remains a contained svg',()=>{
  const html=ui.chart({interval:'5m',anchor:2000,candles:[bar,{...bar,time:3000,close_time:3999,high:'999'}]}, {},true);
  assert.match(html,/replay-scroll/);assert.doesNotMatch(html,/999/);assert.match(html,/只|观察时点前/);
});
test('missing entry and reference venue are explicitly labelled',()=>{
 const html=ui.replayHtml({source:{exchange:'binance',symbol:'TESTUSDT',reference_only:true,note:'参考'},collected_at:1,anchor_kind:'recorded_observation',windows:[{interval:'5m',anchor:2000,candles:[bar],status:'complete',coverage:'partial'}],limitations:['<script>bad</script>']},'test');
 assert.match(html,/入场时间未知/);assert.match(html,/跨交易所参考/);assert.match(html,/窗口有缺口/);assert.doesNotMatch(html,/<script>/);
});
test('assistant route includes risk inputs and private memory download',()=>{
 const html=ui.render();assert.match(html,/name="risk_budget"/);assert.match(html,/name="equity"/);assert.match(html,/memory.md/);assert.match(html,/不执行下单/);
});
