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
test('quick card sizes from the stop and never exceeds the risk budget',()=>{
 const cap={cap:200,currency:'USD'};
 const r=ui.quickCalc({direction:'LONG',entry:'4.76195',stop:'4.690',target:'4.94815',budget:''},cap);
 assert.equal(r.budget,200);assert.equal(r.qty,2779);assert.ok(r.risk<=200&&r.risk>199);assert.ok(Math.abs(r.rr-2.588)<0.01);
 const s=ui.quickCalc({direction:'SHORT',entry:'100',stop:'99',budget:'50'},cap);assert.match(s.errors[0],/做空的止损要高于入场价/);
 const w=ui.quickCalc({direction:'LONG',entry:'2.38794',stop:'2.230',target:'2.53929',budget:'300'},cap);
 assert.ok(w.warnings.some(x=>/超过你确认的单笔上限 200/.test(x)));assert.ok(w.warnings.some(x=>/低于 1R/.test(x)));
 assert.equal(ui.floorQty(0.60394),0.6039);assert.equal(ui.floorQty(0),0);
});
test('cooldown banner shows remaining time, escapes symbol, and hides when expired',()=>{
 const now=Date.now(),c={active:true,minutes:30,until:now+20*60000,manual_active:false,trigger:{source:'closed_trade',symbol:'<b>X',at:now-600000,pnl:'-116.05',asset:'USDT'}};
 const html=ui.cooldownBanner(c);assert.match(html,/冷静期 · 还剩 20 分钟/);assert.match(html,/-116\.05 USDT/);assert.doesNotMatch(html,/<b>X/);
 assert.match(ui.cooldownBanner(c,true),/href="#assistant"/);
 assert.equal(ui.cooldownBanner({...c,until:now-1}),'');assert.equal(ui.cooldownBanner({active:false}),'');
});
test('assistant page leads with the mentor flow and folds the detailed form',()=>{
 ui.set({notes:[{state:'confirmed',kind:'risk_rule',risk_budget_upper:'200',risk_currency:'USD',updated:2},{state:'confirmed',kind:'risk_rule',risk_budget_upper:'500',risk_currency:'USD',updated:1}]},{active:false,minutes:30});
 const html=ui.render();assert.match(html,/id="mentor"/);assert.match(html,/看行情，判断这笔/);assert.match(html,/inputmode="decimal"/);assert.match(html,/我刚止损了/);assert.match(html,/<details class="panel assistant-detail">/);
 ui.set(null,null);
});
