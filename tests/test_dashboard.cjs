const test=require('node:test'),assert=require('node:assert/strict');
const d=require('../static/dashboard.js');
const now=2000000000000;
const state=()=>({recorder:{status:'running',last_success:now},recorder_settings:{enabled:true,interval:15},snapshot:{captured_at:now,positions:[],coverage:{complete:true,days:7}},totals:{USDT:{net:'42.50'}},journals:[]});
test('outdated or failed recorder never implies current flat position even if manual sync was successful',()=>{
  const s=state();s.recorder.status='retrying';s.sync={status:'complete'};
  assert.equal(d.model(s,now).fresh,false);assert.match(d.overview(s,now),/上次快照空仓/);assert.match(d.overview(s,now),/当前状态待确认/);
  s.recorder.status='running';s.snapshot.captured_at=now-120000;assert.equal(d.model(s,now).fresh,false);
});
test('absent account data stays unknown, and pending evidence is not healthy',()=>{
  assert.equal(d.model({},now).pnl,null);assert.equal(d.model({},now).net,null);
  assert.doesNotMatch(d.overview({},now),/>当前空仓</);
  const s=state();s.recorder.ledger_issues=['missing'];assert.equal(d.health(s,now).healthy,false);
});
test('AI reports do not count as a personal review and open cycles never become review to-dos',()=>{
  const s=state();s.journals=[{id:'a',execution:{closed_ms:now-1000}},{id:'b',reflection:'我的复盘',execution:{closed_ms:now-1000}},{id:'c',execution:{opened_ms:now}}];s.analyses=[{journal_id:'a',status:'complete'}];
  assert.deepEqual(d.model(s,now).due.map(j=>j.id),['a']);
});
test('costs are explicit; only a matching completed receipt contributes allocated funding',()=>{
  const j={id:'a',symbol:'TESTUSDT',execution:{closed_ms:now,opened_ms:now-1000,history_complete:true,realized_pnl:'20.1234',fees:{USDT:'0.1234'}},trade_receipt:{journal_id:'a',symbol:'TESTUSDT',status:'已平仓',realized_pnl_usdt:'20.1234',commission_usdt:'0.1234',funding_usdt:'-0.5',exit_price:'10.25'}};
  assert.ok(Math.abs(d.cycle(j).net-19.5)<1e-8);assert.match(d.cycleSummary(j),/19.50/);
  j.trade_receipt.journal_id='other';assert.equal(d.cycle(j).funding,null);assert.match(d.cycleSummary(j),/尚未计入资金费/);
});
test('rendered exchange and journal strings cannot inject HTML',()=>{
  const s=state();s.journals=[{id:'x" onclick="bad',symbol:'<img src=x onerror=bad>',execution:{closed_ms:now,opened_ms:now-1000,realized_pnl:'1',fees:{}}}];
  const html=d.overview(s,now);assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/data-open-journal="x" onclick/);
});
test('coverage never claims stale protection and keeps external screenshots unverified',()=>{
  const s=state();s.journals=[{id:'n',exchange:'variational',symbol:'NEAR',status:'持仓中'}];s.stop_coverage={snapshot_fresh:true,captured_at:now,rows:[{symbol:'ETHUSDT',direction:'LONG',status:'full',coverage_pct:'100',covered_quantity:'2',quantity:'2',stops:[]}]};
  assert.match(d.stopCoverage(s,now),/已挂全仓止损/);
  assert.match(d.stopCoverage(s,now),/Variational · 1 笔记录待核对/);
  s.recorder.status='stopped';assert.doesNotMatch(d.stopCoverage(s,now),/已挂全仓止损/);
  assert.match(d.stopCoverage(s,now),/无法确认当前保护状态/);
});
test('flat account is not rendered as a 100 percent stop coverage success',()=>{
 const s=state();s.stop_coverage={snapshot_fresh:true,captured_at:now,rows:[]};
 assert.match(d.stopCoverage(s,now),/暂无需核对的仓位/);assert.doesNotMatch(d.stopCoverage(s,now),/100%|已挂全仓/);
});

test('missing credentials is setup required, never a connection outage or a healthy stale client',()=>{
 const s=state();s.credentials_configured=false;
 assert.equal(d.health(s,now).label,'待配置 Binance');assert.equal(d.health(s,now).healthy,false);
 const bar=require('../static/recorder.js').bar(s);
 assert.match(bar,/尚未连接/);assert.match(bar,/data-action="settings"/);assert.doesNotMatch(bar,/重试|连接中断/);
});

test('unconnected providers use compact entries while paused and historical accounts keep their evidence',()=>{
 const s=state();s.exchanges={okx:{configured:false},bybit:{configured:false}};
 let html=d.accountCoverage(s,now);
 assert.match(html,/连接 OKX 账户/);assert.match(html,/连接 Bybit 账户/);
 assert.doesNotMatch(html,/连接 USDT 永续账户/);
 s.exchanges.okx={configured:true,enabled:false,snapshot:{captured_at:now,positions:[],coverage:{days:7}},sync:{status:'complete'}};
 html=d.accountCoverage(s,now);
 assert.match(html,/已暂停/);assert.match(html,/上次快照空仓/);
 assert.match(html,/data-exchange-filter="okx"/);assert.doesNotMatch(html,/aria-label="连接 OKX 账户"/);
 s.exchanges.okx.configured=false;
 assert.match(d.accountCoverage(s,now),/上次快照空仓/);
});

test('home flags recording issues without making optional notes and AI configuration chores',()=>{
 const s=state();s.recorder.status='stopped';s.journals=[{id:'closed-1',symbol:'ETHUSDT',execution:{closed_ms:now-1000}},{id:'open-1',status:'持仓中',symbol:'BTCUSDT'}];
 const html=d.nextActions(s,now);
 assert.match(html,/核对自动记录/);assert.match(html,/data-action="recorder-settings"/);
 assert.doesNotMatch(html,/待本人复盘|补一句入场理由|配置 AI 服务/);
 s.recorder.status='running';assert.match(d.nextActions(s,now),/Binance 自动记录正常/);
 const home=d.overview(s,now);assert.match(home,/交易回顾/);assert.doesNotMatch(home,/kpi-review-track|待复盘/);
});
