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
const review=(over={})=>({status:'complete',journal_id:'j1',quadrant:'lucky',quadrant_label:'运气赚钱：结果好，过程有问题',
  judgment:{state:'right',first_hit:'target',mfe_r:2.1,mae_r:0.4,note:'Binance 参考 K 线'},
  execution:[{code:'early_exit',label:'按止损或止盈离场',status:'fail',value:'<b>离场 101</b>'},{code:'cooldown',label:'不在冷静期内开仓',status:'pass',value:''},{code:'add_recalc',label:'没有临时加仓',status:'unknown',value:'无法核对'}],
  next_action:{code:'early_exit',instruction:'提前离场前写下看到的失效信号。'},lesson_cards:[],check_state:'no',link:'auto',reference_only:true,note:'规则复盘',...over});
test('review panel shows quadrant, judgment, checks, next action and escapes',()=>{
  const html=ui.reviewPanel(review());
  assert.match(html,/运气赚钱/);assert.match(html,/先到止盈/);assert.match(html,/2\.1R/);
  assert.match(html,/data-mentor-adopt="j1"/);assert.match(html,/提前离场前写下/);assert.match(html,/自动关联/);
  assert.match(html,/无法核对/);assert.doesNotMatch(html,/<b>离场/);
  const u=ui.reviewPanel(review({judgment:{state:'unknown',first_hit:null,mfe_r:null,mae_r:null,note:'入场时间未知：不判断'},next_action:null,quadrant:'clean_unknown',quadrant_label:'执行好（判断无法核对）'}));
  assert.match(u,/入场时间未知/);assert.doesNotMatch(u,/data-mentor-adopt/);
});
test('review slot only for closed trades',()=>{
  assert.equal(ui.reviewSlot({id:'a',status:'持仓中'}),'');
  assert.match(ui.reviewSlot({id:'a',status:'已平仓'}),/data-mentor-review="a"/);
});
test('assistant mount wakes the mentor on every page, not only the assistant page',()=>{
  let mounted=0;const prev={doc:global.document,m:global.JournalMentor};
  global.document={querySelector:()=>null,querySelectorAll:()=>[]};
  delete require.cache[require.resolve('../static/assistant.js')];
  global.JournalMentor={mount:()=>{mounted++;}};
  const a=require('../static/assistant.js');a.mount();
  global.document=prev.doc;global.JournalMentor=prev.m;delete require.cache[require.resolve('../static/assistant.js')];
  assert.equal(mounted,1);
});
test('adopted review shows confirmation instead of the button',()=>{
  const html=ui.reviewPanel(review({adopted:true}));
  assert.doesNotMatch(html,/data-mentor-adopt/);assert.match(html,/已放进首页提醒/);
});
const homeStatus=(over={})=>({cooldown:{active:false,minutes:30},reminder:'<i>提前离场前写下失效信号</i>',
  habits:[{code:'early_exit',count:6,label:'没到止损也没到止盈就提前离场',lesson:{title:'提前离场要有信号',do:'先确认可见的失效信号'}},{code:'no_stop',count:4,label:'没有止损',lesson:{title:'数量由止损决定',do:'只填入场和止损'}}],
  open_positions:{by_direction:{LONG:0,SHORT:0},items:[],stale:[{journal_id:'br1',symbol:'BR',exchange:'variational',direction:'LONG',updated:1}]},
  system:{version:1,confirmed:false,rules:{account_risk_pct:2}},...over});
test('home card leads with state, reminder, habits and stale positions',()=>{
  const html=ui.homeCard(homeStatus());
  assert.match(html,/id="mentor-home"/);assert.match(html,/href="#assistant"/);
  assert.match(html,/提前离场前写下失效信号/);assert.doesNotMatch(html,/<i>提前/);
  assert.match(html,/没到止损也没到止盈就提前离场/);assert.match(html,/6 次/);
  assert.match(html,/data-mentor-position="br1"/);assert.match(html,/Variational · 多/);assert.match(html,/data-position-action="still_open"/);assert.match(html,/data-position-action="closed"/);
  assert.match(html,/草案/);
});
test('home card shows cooldown and an empty state without data',()=>{
  const now=Date.now();
  const c=ui.homeCard(homeStatus({cooldown:{active:true,minutes:30,until:now+12*60000,trigger:{symbol:'ETH',at:now-18*60000,source:'closed_trade',pnl:'-50',asset:'USDT'}},habits:[],open_positions:{by_direction:{},items:[],stale:[]},reminder:null}));
  assert.match(c,/冷静期/);assert.match(c,/12 分钟/);assert.doesNotMatch(c,/最常犯/);
  assert.match(ui.homeCard(null),/读取/);
});
