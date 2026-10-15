(function(root){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=value=>value===null||value===undefined||value===''?null:Number.isFinite(Number(value))?Number(value):null;
  const money=value=>num(value)===null?'—':Number(value).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const signed=value=>num(value)===null?'—':(Number(value)>0?'+':'')+money(value);
  const tone=value=>num(value)===null?'':Number(value)>0?'positive':Number(value)<0?'negative':'';
  const stamp=value=>value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'尚无快照';
  const icons={trend:'<path d="m3 16 6-6 4 4 8-10M15 4h6v6"/>',wallet:'<rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 9h18M16 14h3"/>',review:'<path d="M8 3H5v18h14V3h-3M9 2h6v4H9zM8 13l3 3 5-6"/>',shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="M9 12h6"/>',arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>'};
  const icon=name=>`<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.arrow}</svg>`;
  const accountMark=name=>`<span class="account-mark" aria-hidden="true">${({Binance:'B',OKX:'OK',Bybit:'BY',Variational:'V'})[name]||'·'}</span>`;
  function health(state,now=Date.now()){
    const r=state?.recorder||{},settings=state?.recorder_settings||{},last=Number(r.last_success)||0;
    const stale=!last||now-last>Math.max(90000,(settings.interval||15)*3000);
    const healthy=Boolean(state?.credentials_configured!==false&&settings.enabled&&r.status==='running'&&!stale);
    const partial=healthy&&Boolean(r.error||r.ledger_issues?.length||r.market_errors?.length);
    return {healthy:healthy&&!partial,stale,last,label:state?.credentials_configured===false?'待配置 Binance':!settings.enabled?'自动记录已暂停':r.status==='retrying'?'连接中断 · 正在重试':r.status==='stopped'?'采集服务未连接':partial?'部分数据待补齐':healthy?'自动记录中':last?'数据已过期 · 等待采集':'正在连接自动记录'};
  }
  function model(state,now=Date.now()){
    const snapshot=state?.snapshot||{},status=health(state,now),known=Boolean(snapshot.captured_at&&Array.isArray(snapshot.positions));
    const positions=(snapshot.positions||[]).filter(p=>num(p.positionAmt)!==null&&Number(p.positionAmt)!==0);
    const fresh=known&&status.healthy&&now-snapshot.captured_at<=90000;
    const closed=(state?.journals||[]).filter(j=>j.execution?.closed_ms).sort((a,b)=>b.execution.closed_ms-a.execution.closed_ms);
    const recent=closed.filter(j=>now-j.execution.closed_ms<=30*86400000);
    const due=recent.filter(j=>j.review_status?!j.review_status.personal_complete:!j.reflection?.trim());
    const pnl=positions.reduce((sum,p)=>num(p.unRealizedProfit)===null?NaN:sum+Number(p.unRealizedProfit),0);
    return {known,fresh,positions,status,closed,recent,due,pnl:known&&Number.isFinite(pnl)?pnl:null,
      net:snapshot.coverage?.complete?num(state?.totals?.USDT?.net):null,days:snapshot.coverage?.days};
  }
  function cycle(j){
    const e=j.execution||{},r=j.trade_receipt||{},realized=num(e.realized_pnl),fee=num(e.fees?.USDT??0);
    const otherFees=Object.entries(e.fees||{}).filter(([asset,amount])=>asset!=='USDT'&&num(amount)!==0);
    // A receipt's funding allocation is only applicable to the same completed execution.
    const receiptValid=r.journal_id===j.id&&['closed','已平仓'].includes(r.status)&&r.symbol===j.symbol&&num(r.realized_pnl_usdt)===realized&&num(r.commission_usdt)===fee;
    const funding=receiptValid?num(r.funding_usdt):null;
    return {realized,fee,otherFees,funding,net:realized===null||fee===null?null:realized-fee+(funding??0),
      complete:Boolean(e.history_complete),exit:receiptValid?num(r.exit_price):null,
      duration:e.closed_ms&&e.opened_ms?Math.max(0,e.closed_ms-e.opened_ms):null};
  }
  function cycleSummary(j){
    if(!j.execution?.closed_ms)return '';
    const c=cycle(j),duration=c.duration===null?'待核对':`${Math.floor(c.duration/3600000)} 小时 ${Math.floor(c.duration%3600000/60000)} 分钟`;
    return `<section class="cycle-summary"><div class="cycle-summary-head"><div><span class="eyebrow">实际成交结果</span><h3>${c.funding===null?'扣手续费后损益':'已核对净收益'} <small>USDT</small></h3></div><strong class="${tone(c.net)}">${signed(c.net)}</strong></div><div class="cycle-facts"><span>已实现盈亏<b class="${tone(c.realized)}">${signed(c.realized)}</b></span><span>手续费<b>${money(c.fee)}</b></span><span>资金费<b>${c.funding===null?'未分摊':signed(c.funding)}</b></span><span>持仓时长<b>${duration}</b></span></div><p>入场 ${stamp(j.execution.opened_ms)} → 平仓 ${stamp(j.execution.closed_ms)}${c.exit===null?'':` · 平仓均价 ${c.exit.toLocaleString('en-US',{maximumFractionDigits:6})}`}</p><p>${c.funding===null?'本笔结果尚未计入资金费；账户收益统计按交易所全部账单计算。':'含实际成交手续费和已核对的持仓期间资金费。'}${c.otherFees.length?' 另有非 USDT 费用：'+c.otherFees.map(([a,v])=>esc(v)+' '+esc(a)).join('、')+'，未换算。':''}${!c.complete?' 开仓历史不完整，结果仍需核对。':''}</p></section>`;
  }
  function nextActions(state,now=Date.now()){
    const m=model(state,now),items=[];
    if(!m.status.healthy)items.push({title:'核对自动记录',text:m.status.label,action:'recorder-settings',label:'查看采集',kind:'attention'});
    const missing=(state.journals||[]).filter(j=>j.status==='持仓中'&&!j.thesis?.trim()).sort((a,b)=>(b.updated||0)-(a.updated||0));
    if(missing.length)items.push({title:missing[0].symbol+' · 补一句入场理由',text:`${missing.length} 笔记录待补原始想法 · 补写时间会保留`,action:'dashboard-journal',id:missing[0].id,focus:'thesis',label:'补充理由'});
    if(m.due.length)items.push({title:`${m.due.length} 笔交易待本人复盘`,text:'先回顾最近一笔：'+m.due[0].symbol+' · 本人复盘',action:'dashboard-journal',id:m.due[0].id,focus:'reflection',label:'开始复盘'});
    if(!state.ai?.available)items.push({title:'配置 AI 服务',text:'接入后可生成复盘、审核计划',action:'ai-settings',label:'去设置',kind:'setup'});
    return `<section class="dashboard-actions" aria-label="待处理事项"><div class="dashboard-section-head"><h3>待处理事项 <small>${items.length}</small></h3></div>${items.length?`<div class="dashboard-action-list">${items.map(i=>`<button class="dashboard-action ${i.kind||''}" data-action="${i.action}" ${i.id?`data-open-journal="${esc(i.id)}"`:''} ${i.focus?`data-focus-field="${i.focus}"`:''}><span class="action-icon" aria-hidden="true">${icon(i.kind==='attention'?'shield':i.kind==='setup'?'spark':'review')}</span><span class="action-copy"><b>${esc(i.title)}</b><small>${esc(i.text)}</small></span><em>${i.label}<i aria-hidden="true"> ↗</i></em></button>`).join('')}</div>`:'<p class="dashboard-empty">暂时没有待处理事项。新的成交会自动记录。</p>'}</section>`;
  }
  function stopCoverage(state,now=Date.now(),showExternal=true,exchange="binance",name="Binance"){
    const m=model(state,now),coverage=state.stop_coverage||{},rows=coverage.rows||[],current=m.fresh&&coverage.snapshot_fresh;
    const labels={full:'已挂全仓止损',partial:'仅覆盖部分',missing:'未见固定止损',unknown:'待核对'};
    const counts={full:0,partial:0,missing:0,unknown:0};
    const normalized=rows.map(r=>({...r,status:current?r.status:'unknown'}));normalized.forEach(r=>counts[r.status]++);
    const manual=(state.journals||[]).filter(j=>j.exchange==='variational'&&j.status==='持仓中');
    const findJournal=r=>(state.journals||[]).find(j=>(j.exchange||'binance')===exchange&&j.symbol===r.symbol&&j.status==='持仓中'&&j.execution?.positionSide===r.position_side);
    return `<section class="stop-coverage" aria-label="止损挂单覆盖"><div class="dashboard-section-head"><div class="risk-heading">${icon('shield')}<h3>止损覆盖</h3></div><span class="dashboard-muted">${name} · ${stamp(coverage.captured_at)}</span></div>
      ${normalized.length?`<div class="coverage-counts"><span>全仓 <b>${counts.full}</b></span><span>部分 <b>${counts.partial}</b></span><span>未见固定止损 <b>${counts.missing}</b></span><span>待核对 <b>${counts.unknown}</b></span></div><div class="coverage-list">${normalized.sort((a,b)=>({missing:0,partial:1,unknown:2,full:3}[a.status]-{missing:0,partial:1,unknown:2,full:3}[b.status])).map(r=>{const j=findJournal(r),known=r.status!=='unknown';return `<div class="coverage-row"><div><b>${esc(r.symbol)} <small>${r.direction==='LONG'?'做多':'做空'}</small></b><p>${known?`已挂止损数量 ${esc(r.covered_quantity)} / ${esc(r.quantity)} · ${Number(r.coverage_pct).toFixed(1)}%`:(current?esc(r.reason):'数据未更新，无法确认当前保护状态')}</p>${known&&r.stops?.length?`<small>触发价 ${r.stops.map(o=>esc(o.price)+(o.type==='STOP'?'（限价）':'（市价）')).join(' / ')} · 挂单 ${stamp(r.captured_at)}</small>`:''}${known&&r.excluded?'<small>未计入不限定平仓的挂单</small>':''}</div><div class="coverage-row-actions"><span class="coverage-badge ${r.status}">${labels[r.status]||'待核对'}</span>${j?`<button class="button quiet small" data-action="dashboard-journal" data-open-journal="${esc(j.id)}">查看日志 →</button>`:''}</div></div>`;}).join('')}</div>`:`<div class="coverage-empty ${current?'flat':'unverified'}"><span class="coverage-empty-icon">${icon('shield')}</span><div><b>${current?name+' 当前空仓':name+' 保护状态待核对'}</b><p class="dashboard-empty">${current?'最新 '+name+' 快照为空仓，暂无需核对的仓位。':'尚无有效的最新仓位证据，无法确认止损覆盖。'}</p></div></div>`}
      ${showExternal&&manual.length?`<div class="coverage-external"><b>Variational · ${manual.length} 笔记录待核对</b><p>截图显示的止损不能证明当前仍有效，不计入上述 Binance 统计。</p>${manual.map(j=>`<button class="button quiet small" data-action="dashboard-journal" data-open-journal="${esc(j.id)}">${esc(j.symbol)} · 查看截图 →</button>`).join('')}</div>`:''}
      <details class="coverage-method"><summary>统计口径</summary><p class="coverage-footnote">仅核对固定止损挂单的方向、剩余数量与平仓属性；不含追踪止损。挂单覆盖不保证成交，限价止损、价差保护与滑点可能影响执行。计划价位不计入覆盖。</p></details></section>`;
  }
  function apiAccount(exchange,a,state,now){
    const name={okx:'OKX',bybit:'Bybit'}[exchange],snapshot=a.snapshot||{},positions=snapshot.positions||[];
    const fresh=a.configured&&a.enabled&&a.sync?.status==='complete'&&now-(snapshot.captured_at||0)<=90000;
    const total=a.totals?.USDT,pnl=snapshot.captured_at?positions.reduce((sum,p)=>sum+Number(p.unRealizedProfit),0):null;
    const status=!a.configured?'未连接':!a.enabled?'已暂停':a.sync?.status==='running'?'正在同步':a.sync?.status==='retrying'?'同步失败 · 自动重试':a.sync?.status==='partial'?'部分数据待核对':fresh?'自动记录中':snapshot.captured_at?'快照已过期':'等待首次同步';
    return `<article class="account-card ${fresh?'verified':'pending'}"><div class="account-card-heading"><b>${accountMark(name)}${name}</b><span class="coverage-badge ${fresh?'full':'unknown'}">${status}</span></div><h3>${snapshot.captured_at?(positions.length?positions.length+' 个持仓':fresh?'当前空仓':'上次快照空仓'):'连接 USDT 永续账户'}</h3>${snapshot.captured_at?`<div class="exchange-account-values"><span>窗口已实现净额 · USDT<strong class="${tone(total?.net)}">${signed(total?.net??0)}</strong></span><span>${fresh?'持仓':'上次快照'}浮盈亏 · USDT<strong class="${tone(pnl)}">${signed(pnl)}</strong></span></div><p>${stamp(snapshot.captured_at)} 快照 · 近 ${snapshot.coverage?.days||'—'} 天账单</p><p>${esc(snapshot.wallet?.scope||'USDT 权益')}：${money(snapshot.wallet?.equity)} USDT</p>`:`<p>${name==='OKX'?'填写 API Key、Secret 和 Passphrase。':'填写统一账户（UTA）的 API Key 和 Secret。'}接入后自动记录成交和持仓。</p>`}${a.sync?.error?`<p class="negative">${esc(a.sync.error)}</p>`:''}<div class="account-links"><button class="button small" data-action="${a.configured?'dashboard-journal':'settings'}" ${a.configured?`data-exchange-filter="${exchange}"`:''}>${a.configured?'查看交易':'连接账户'}</button>${a.configured?`<button class="button small secondary-link" data-action="exchange-sync" data-exchange="${exchange}" ${a.sync?.status==='running'?'disabled':''}>立即同步</button>`:''}</div></article>`;
  }
  function apiStops(state,now){
    return Object.entries(state.exchanges||{}).filter(([,a])=>a.configured||a.snapshot?.captured_at).map(([exchange,a])=>{
      const adapted={...a,credentials_configured:a.configured,recorder:{...a.sync,status:a.sync?.status==='complete'?'running':a.sync?.status},recorder_settings:{enabled:a.enabled,interval:60},journals:state.journals};
      return stopCoverage(adapted,now,false,exchange,{okx:'OKX',bybit:'Bybit'}[exchange]);
    }).join('');
  }
  function accountCoverage(state,now=Date.now()){
    const m=model(state,now),manual=(state.journals||[]).filter(j=>j.exchange==='variational'&&['持仓中','待核对'].includes(j.status));
    const positionLabel=!m.known?'待读取':m.positions.length?`${m.positions.length} 个持仓`:(m.fresh?'当前空仓':'上次快照空仓');
    return `<div class="account-coverage" aria-label="账户覆盖状态"><article class="account-card ${m.fresh?'verified':'pending'}"><div class="account-card-heading"><b>${accountMark('Binance')}Binance</b><span class="coverage-badge ${m.fresh?'full':'unknown'}">${m.fresh?'自动同步':'当前状态待确认'}</span></div><h3>${positionLabel}</h3><p>${m.known?stamp(state.snapshot.captured_at)+' · 持仓快照':'等待首次持仓快照'}${!m.fresh?' · 不代表当前状态':''}</p>${m.positions.length?`<p>${m.positions.map(p=>esc(p.symbol)).join(' · ')}</p>`:''}<div class="account-links"><button class="button small" data-action="dashboard-journal" data-exchange-filter="binance" aria-label="查看 Binance 交易">查看交易</button><button class="button small secondary-link" data-action="recorder-settings">采集设置</button></div></article>${Object.entries(state.exchanges||{}).filter(([,a])=>a.configured||a.snapshot?.captured_at).map(([exchange,a])=>apiAccount(exchange,a,state,now)).join('')}<article class="account-card pending"><div class="account-card-heading"><b>${accountMark('Variational')}Variational</b><span class="coverage-badge unknown">未接入</span></div><h3>${manual.length?`${manual.length} 笔记录待核对`:'当前仓位未知'}</h3><p>来自历史截图，当前仓位、止损与盈亏待核对。</p><div class="account-links">${manual.map(j=>`<button class="button small" data-action="dashboard-journal" data-open-journal="${esc(j.id)}">${esc(j.symbol)}</button>`).join('')||'<button class="button small" data-action="dashboard-journal" data-exchange-filter="variational">查看记录</button>'}</div></article></div>${Object.entries(state.exchanges||{}).some(([,a])=>!a.configured&&!a.snapshot?.captured_at)?`<div class="account-connect-strip"><span>连接更多账户</span><div>${Object.entries(state.exchanges||{}).filter(([,a])=>!a.configured&&!a.snapshot?.captured_at).map(([exchange])=>`<button class="button small" data-action="settings" data-connect-exchange="${exchange}" aria-label="连接 ${{okx:'OKX',bybit:'Bybit'}[exchange]} 账户"><span aria-hidden="true">＋</span> ${{okx:'OKX',bybit:'Bybit'}[exchange]}</button>`).join('')}</div></div>`:''}`;
  }
  function overview(state,now=Date.now()){
    if(!state)return '<section class="dashboard-overview" aria-busy="true">正在读取账户概览…</section>';
    const m=model(state,now),latest=m.closed.slice(0,3);
    const done=state.review_progress?.personal.numerator??0,total=state.review_progress?.personal.denominator??m.recent.length;
    return `<div class="dashboard-overview">
      <section class="dashboard-kpis compact-kpis" aria-label="账户与复盘摘要">
        <button class="kpi-net" data-action="dashboard-analytics"><span class="kpi-label">已实现净额 <i>USDT</i>${icon('trend')}</span><strong class="${tone(m.net)}">${signed(m.net)}</strong><small>Binance · ${m.days?'近 '+esc(m.days)+' 天':'已同步期间'} · 已扣手续费与资金费</small></button>
        <button class="kpi-position" data-action="dashboard-journal" data-exchange-filter="binance"><span class="kpi-label">${m.fresh?'持仓':'上次快照'}浮盈亏 <i>USDT</i>${icon('wallet')}</span><strong class="${tone(m.pnl)}">${signed(m.pnl)}</strong><small>Binance · ${m.known?m.positions.length+' 个持仓':'仓位待读取'} · 不含 Variational</small></button>
        <button class="kpi-review" data-view="progress"><span class="kpi-label">本人复盘${icon('review')}</span><strong>${done}<i class="metric-denominator"> / ${total}</i></strong><small>近 30 天已确认平仓 · AI 报告另计</small>${total?`<span class="kpi-review-track" aria-hidden="true"><span style="width:${Math.max(0,Math.min(100,done/total*100))}%"></span></span>`:''}</button>
      </section>
      <div class="dashboard-focus"><section class="account-section" aria-label="持仓保护状态">${stopCoverage(state,now,false)}${apiStops(state,now)}</section>${nextActions(state,now)}</div>
      <div class="dashboard-workspace"><section class="dashboard-recent"><div class="dashboard-section-head"><h3>最近平仓</h3><button class="button quiet small" data-action="dashboard-journal">全部交易</button></div>${latest.length?latest.map(j=>{const c=cycle(j);return `<button class="dashboard-trade" data-action="dashboard-journal" data-open-journal="${esc(j.id)}" data-focus-field="reflection"><span><b>${esc(j.symbol)}</b><small>${({binance:'Binance',okx:'OKX',bybit:'Bybit'}[j.exchange||'binance']||'交易所')} · ${stamp(j.execution.closed_ms)} · ${j.review_status?.personal_complete?'已复盘':'待复盘'}</small></span><span class="dashboard-trade-result"><b class="${tone(c.net)}">${signed(c.net)} <i>USDT</i></b><small>${c.funding===null?'扣手续费 · 资金费未分摊':'含手续费与资金费'}</small></span></button>`;}).join(''):'<p class="dashboard-empty">暂无已确认平仓的交易。</p>'}</section><section class="dashboard-accounts" aria-label="账户来源"><div class="dashboard-section-head"><h3>账户来源</h3><button class="button quiet small" data-action="settings">管理连接</button></div>${accountCoverage(state,now)}</section></div>
    </div>`;
  }
  const api={health,model,cycle,cycleSummary,nextActions,stopCoverage,accountCoverage,apiAccount,apiStops,overview};
  if(typeof module!=='undefined')module.exports=api;else root.JournalDashboard=api;
})(typeof window!=='undefined'?window:globalThis);
