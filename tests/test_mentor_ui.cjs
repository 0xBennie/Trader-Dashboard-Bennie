const test=require('node:test'),assert=require('node:assert/strict');
global.JournalAssistant=require('../static/assistant.js');
const ui=require('../static/mentor.js');
const dims=['context','location','momentum','risk'].map((key,i)=>({key,name:['大背景','位置','信号与动能','风险回报'][i],score:20,max:25,checks:[{key:'k'+i,label:'检查'+i,status:['pass','fail','unknown','pass'][i],value:'值'+i}]}));
const judgment=(over={})=>({state:'wait_trigger',label:'等触发',score:67,dimensions:dims,hard_fails:[],position_ok:true,trigger_ok:false,
  trigger_price:4.955,wait_for:'等 15m 收盘站上 4.955（区域上沿）',counter:'<img src=x onerror=alert(1)>',
  levels:{stop:4.9,stop_basis:'结构外',target:5.1,target_basis:'前方摆动点'},
  sizing:{budget:32.72,currency:'USD',full_qty:409,trial_qty:204.5,recommended:'trial',risk:16.36,combined_pct:1.2,combined_limit_pct:6,equity:1636,cap_amount:200,cap_pct_amount:32.72},
  flow:{status:'complete',taker_ratio_avg:0.98,oi_change_pct:-0.5,top_long_short:2.7},lessons:['trigger_vs_location'],
  lesson_cards:[{code:'trigger_vs_location',title:'到位不等于该进场',body:'说明',do:'挂提醒'}],draft:true,setup:{id:'support_bounce',name:'支撑反弹'},
  source:{exchange:'binance',symbol:'NEARUSDT',reference_only:true,through:1},...over});
const result=(over)=>({id:'c1',created_at:1,plan:{symbol:'NEAR',direction:'LONG',exchange:'variational'},judgment:judgment(over),ai:{status:'not_requested'}});

test('verdict card shows wait state, trigger price, sizing tiers and escapes text',()=>{
  const html=ui.verdictCard(result());
  assert.match(html,/等触发/);assert.match(html,/4\.955/);assert.match(html,/verdict-wait/);
  assert.match(html,/试单/);assert.match(html,/204\.5/);assert.match(html,/409/);
  assert.match(html,/到位不等于该进场/);assert.match(html,/按草案规则/);
  assert.doesNotMatch(html,/<img/);assert.doesNotMatch(html,/<script/);
});
test('verdict card states map to tones and hard fails lead a no',()=>{
  assert.match(ui.verdictCard(result({state:'go',label:'可以做'})),/verdict-go/);
  const no=ui.verdictCard(result({state:'no',label:'别做',hard_fails:['盈亏比 1.2R 低于你系统里的最低 1.5R。']}));
  assert.match(no,/verdict-no/);assert.match(no,/盈亏比 1\.2R/);
  const na=ui.verdictCard({...result(),judgment:{state:'unavailable',reason:'Binance 没有这个永续合约'}});
  assert.match(na,/Binance 没有这个永续合约/);
});
test('status bar shows draft system with confirm, combined risk and reminder',()=>{
  const html=ui.statusBar({system:{version:1,confirmed:false,rules:{account_risk_pct:2,min_rr:1.5}},open_positions:{by_direction:{LONG:150,SHORT:0}},reminder:'<b>止盈不往回收</b>'},{cooldownHtml:'',coolAction:''});
  assert.match(html,/待你确认/);assert.match(html,/data-mentor-confirm/);assert.match(html,/name="account_risk_pct"/);
  assert.match(html,/150/);assert.match(html,/止盈不往回收/);assert.doesNotMatch(html,/<b>止盈/);
  assert.match(html,/id="cooldown-slot"/);
});
test('setup picker and discretionary inputs',()=>{
  const setups=[{id:'support_bounce',name:'支撑反弹',summary:'回踩',default_timeframe:'5m'},{id:'discretionary',name:'盘感试单',summary:'说不清',default_timeframe:'5m'}];
  ui.set({status:{system:{version:1,confirmed:true,rules:{account_risk_pct:2},setups},open_positions:{by_direction:{LONG:0,SHORT:0},items:[]}},form:{setup_id:'discretionary',direction:'LONG',timeframe:'5m'}});
  const html=ui.render({cooldownHtml:'',coolAction:'',memory:null});
  assert.match(html,/data-mentor-setup="support_bounce"/);assert.match(html,/name="bet"/);assert.match(html,/name="time_limit"/);
  assert.match(html,/inputmode="decimal"/);assert.doesNotMatch(html,/<script/);
  ui.set({form:{setup_id:'support_bounce',direction:'LONG',timeframe:'5m'}});
  assert.doesNotMatch(ui.render({cooldownHtml:'',coolAction:''}),/name="bet"/);
});
test('live preview uses the smallest of memory cap, account % and typed budget',()=>{
  global.JournalAssistant=require('../static/assistant.js');
  ui.set({status:{system:{version:1,confirmed:false,rules:{account_risk_pct:2},setups:[]},open_positions:{by_direction:{LONG:0,SHORT:0},items:[]}},
          form:{setup_id:'',direction:'LONG',timeframe:'5m',entry_price:'4.98',stop_loss:'4.93',take_profit:'5.1',equity:'1636',risk_budget:''}});
  const memory={notes:[{state:'confirmed',kind:'risk_rule',risk_budget_upper:'200',risk_currency:'USD',updated:1}]};
  const html=ui.render({memory,cooldownHtml:'',coolAction:''});
  assert.match(html,/按 32\.72 上限可开 <b>654\.4<\/b>/);
});
