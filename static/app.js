'use strict';
let state, view = 'training', selectedId = null, reportId = null, dirty = false, syncing = false, toastTimer, tradeMode = 'orders', orderFilter = null, shownDays = null, editBase = null, savingNotes = false, analyticsDay = null, analyticsMonth = null;
try{selectedId=window.localStorage.getItem('selected-journal')||selectedId;}catch{}
const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fmt = (value, digits = 2) => value === null || value === undefined || value === '' ? '—' : Number(value).toLocaleString('en-US', {minimumFractionDigits:digits, maximumFractionDigits:digits});
const signed = (value, digits = 2) => (Number(value) > 0 ? '+' : '') + fmt(value, digits);
const precise = (value, minimum = 0) => { const [whole, fraction='']=String(value??'').split('.'); const decimals=fraction.replace(/0+$/,'').padEnd(minimum,'0'); return escapeHTML(whole.replace(/\B(?=(\d{3})+(?!\d))/g,','))+(decimals?'.'+escapeHTML(decimals):''); };
const tone = value => Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : 'neutral';
const date = (stamp, full = false) => stamp ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Hong_Kong', year:full?'numeric':undefined, month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(stamp)) : '尚未记录';
let journalFilters={exchange:'all',status:'all'}, pickerFilters={...journalFilters}, pickerQuery='';
const noteSections=new Map(), evidenceSections=new Map(), panelExpansion=new Map();
let renderedView=null;
const expandablePanels=["review-method","more-reviews","completed-reviews","discipline-details","review-tools"];
const chosen = () => state.journals.find(j => j.id === selectedId) || state.journals[0];
const reports = () => state.analyses.filter(a => a.journal_id === chosen()?.id);
const external = j => j?.exchange === 'variational';
const exchangeName = j => ({binance:'Binance',okx:'OKX',bybit:'Bybit',variational:'Variational'}[j?.exchange||'binance']||'交易所');
const journalAccount = j => !j?.exchange||j.exchange==='binance'?state:(state.exchanges?.[j.exchange]||{snapshot:{},trade_context:{}});
let ledgerExchange='binance';
const ledgerAccount=()=>JournalExchanges.account(state,ledgerExchange);
const ledgerPicker=()=>JournalExchanges.picker(ledgerExchange);
const syncExchange=()=>{
  if(['trades','analytics'].includes(view))return ledgerExchange;
  const j=view==='journal'?(filteredJournals().find(j=>j.id===selectedId)||filteredJournals()[0]):chosen();
  return ['journal','review'].includes(view)&&['okx','bybit'].includes(j?.exchange)?j.exchange:'binance';
};
const journalFresh=j=>{const a=journalAccount(j),stamp=a.snapshot?.captured_at;return Boolean(stamp&&Date.now()-stamp<=90000&&(['okx','bybit'].includes(j?.exchange)?a.enabled&&a.sync?.status==='complete':a.recorder_settings?.enabled&&a.recorder?.status==='running'));};
const currency = j => external(j) ? 'USD' : 'USDT';
const position = () => external(chosen()) || chosen()?.execution?.closed_ms ? [] : journalAccount(chosen()).snapshot.positions?.filter(p => p.symbol === chosen()?.symbol && (!chosen()?.execution || p.positionSide===chosen().execution.positionSide)) || [];
const priceFmt = value => JournalRecorder.price(value);
const verified = () => Boolean(state.snapshot.captured_at);

const drafts = JournalClient.draftStore(window.localStorage);
const api = JournalClient.createApi(window.fetch.bind(window), () => state?.csrf, token => {if(state)state.csrf=token;}, () => {document.body.hidden=true;location.replace('/login'+location.hash);});
function formValues() {return JournalLearning.values($('#plan-form'));}
function keepDraft() {
  if(!$('#plan-form')||!editBase)return;
  dirty=Object.keys(JournalClient.changedFields(editBase,formValues())).length>0;
  const saved=dirty?drafts.write(chosen().id,editBase,formValues()):(drafts.remove(chosen().id),true);
  $('#save-state').textContent=dirty?(saved?'草稿已保存在本机':'草稿尚未备份，请保持页面打开'):'已保存';
  $('#save-state').classList.toggle('unsaved-label',dirty);
  updateSaveButton();
}
function draftConflicts(j,draft) {
  if(!draft)return '';
  const changed=JournalClient.changedFields(draft.base,draft.values);
  const labels={review_keep:'做对的地方',review_next:'下一笔改进',review_execution:'执行评价',review_mistakes:'改进问题',thesis:'入场理由',emotion:'情绪',stop_loss:'计划止损',take_profit:'计划目标',exit_plan:'退出计划',invalidation:'失效条件',reflection:'复盘',quantity:'数量',entry_price:'入场价'};
  const conflicts=Object.keys(changed).filter(k=>String(j[k]??'')!==String(draft.base[k]??'')&&String(j[k]??'')!==String(changed[k]));
  return conflicts.length?`<div class="draft-conflict"><b>这些字段有两个版本，请选择保留哪一个</b>${conflicts.map(k=>`<div><strong>${escapeHTML(labels[k]||k)}</strong><p>已保存：${escapeHTML(j[k]||'空白')}</p><p>你的草稿：${escapeHTML(changed[k]||'空白')}</p><button type="button" class="button small" data-resolve="${escapeHTML(k)}" data-version="mine">保留我的草稿</button> <button type="button" class="button small" data-resolve="${escapeHTML(k)}" data-version="saved">采用已保存值</button></div>`).join('')}</div>`:'';
}
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').hidden=false; toastTimer=setTimeout(()=>$('#toast').hidden=true,5000); }
function badge(direction) { return direction === 'LONG' ? '<span class="badge long">↗ 做多</span>' : direction === 'SHORT' ? '<span class="badge short">↘ 做空</span>' : '<span class="badge warn">方向待核对</span>'; }
function metric(label, value, sub, unit='', cls='', action='') { const tag=action?'button':'div';return `<${tag} class="metric ${action?'metric-link':''}" ${action?`data-action="${action}" aria-label="查看成交订单汇总"`:''}><div class="metric-label">${label}${action?'<span class="metric-arrow">↗</span>':''}</div><div class="metric-value ${cls}">${value}<small>${unit}</small></div><div class="metric-sub">${sub}</div></${tag}>`; }

function chrome() {
  document.querySelectorAll('[data-wait-state]').forEach(button=>button.disabled=false);
  $('#logout-button').hidden=!state.auth?.enabled;
  const snapshot=state.snapshot, complete=verified(), sync=state.sync;
  if(!$('#recorder-status').contains(document.activeElement))$('#recorder-status').innerHTML=JournalRecorder.bar(state);
  if(snapshot.coverage?.days && shownDays!==snapshot.coverage.days){$('#sync-days').value=String(snapshot.coverage.days);shownDays=snapshot.coverage.days;}
  const total=state.totals.USDT || {realized:'0',fees:'0',funding:'0',net:'0'};
  const positions=(snapshot.positions || []).filter(p=>p.marginAsset==='USDT' || p.symbol.endsWith('USDT'));
  const unrealized=positions.reduce((sum,p)=>sum+Number(p.unRealizedProfit),0);
  $('#metrics').innerHTML=metric('Binance 已实现净额',complete?signed(total.net):'—',complete?`近 ${snapshot.coverage.days} 天 · 已扣手续费及资金费`:'同步后计算真实收益','USDT',complete?tone(total.net):'neutral')+
    metric(JournalDashboard.model(state).fresh?'Binance 持仓浮盈亏':'Binance 上次快照浮盈亏',complete?signed(unrealized):'—',complete?`${positions.length} 个 USDT 持仓 · ${date(snapshot.captured_at)} 快照`:'等待 Binance 持仓数据','USDT',complete?tone(unrealized):'neutral')+
    metric('成交订单',complete?state.orders.length:'—',complete?`${state.trades.length} 笔撮合明细 · ${snapshot.coverage.symbols.length} 个交易对`:'同一订单的多次撮合合并统计','单','','orders')+
    metric('AI 报告',state.analyses.filter(a=>a.status==='complete').length,`${state.journals.length} 篇交易日志 · 保留历史版本`,'份');
  const targetExchange=syncExchange(), targetSync=targetExchange==='binance'?sync:state.exchanges?.[targetExchange]?.sync;
  const running=targetSync?.status==='running'; syncing=running;
  $('#sync-label').textContent=running?'正在同步…':'同步 '+exchangeName({exchange:targetExchange});
  $('[data-action="sync"]').disabled=running;
  const liveHealth=JournalDashboard.health(state);
  $('#connection-dot').className='dot'+(liveHealth.healthy?' online':'');
  $('#connection-text').textContent=liveHealth.label;
  const aiStatus=$('.page-actions [data-action="ai-settings"]');aiStatus.textContent=state.ai?.available?'AI 已配置':'配置 AI';aiStatus.classList.toggle('is-connected',Boolean(state.ai?.available));
  $('#updated-time').textContent=complete?`${date(snapshot.captured_at)} 更新`:'尚未同步';
  $('#journal-count').textContent=state.journals.length;
  const notice=$('#sync-notice');
  if (view==='share') notice.hidden=true;
  else if (sync.status==='failed') { notice.hidden=false; notice.className='notice'; notice.textContent=sync.error+(complete?' 页面显示上次成功同步的快照。':' 暂无已验证的账户数据。'); }
  else if (running) { notice.hidden=false; notice.className='notice info'; notice.textContent='正在读取持仓、成交及账单。完整同步后会一次性更新，已有日志会保留。'; }
  else if (complete && Date.now()-snapshot.captured_at>300000) { notice.hidden=false; notice.className='notice info'; notice.textContent=`持仓与浮盈亏来自 ${date(snapshot.captured_at)} 的快照。点击“同步 Binance”刷新；此页面不是实时行情。`; }
  else notice.hidden=true;
  document.body.dataset.section=JournalTraining.routes.includes(view)?'training':'journal';
  document.body.dataset.view=view;
  if(location.hash!=='#'+view)history.replaceState(null,'','#'+view);
  $('#section-navigation').innerHTML=JournalWorkflow.navigation(view);
  const labels={training:['总览','账户与风险','先核对账户覆盖，再处理今天的风险与待办。'],progress:['复盘','复盘','从实际执行出发，留下下一次要做的改变。'],journal:['交易日志','持仓与日志','成交自动归档，仓位持续留档。你只需要补充当时的想法。'],trades:['成交明细','成交明细','成交、手续费与资金费分开记录，不把划转算成收益。'],review:['AI 报告','AI 报告','结合原始截图、交易计划与成交证据，审视每一次决策。'],analytics:['收益统计','收益统计','按交易所账单计算，只展示已有数据能支持的结论。'],share:['分享','分享工作台','把交易和复盘，留成一张自己的成绩单。']};
  const l=labels[view]; $('#breadcrumb').innerHTML=`工作台 <span>/</span> ${l[0]}`; $('#page-title').textContent=l[1]; $('#page-description').textContent=l[2];
  $('#tabs-label').innerHTML=`${l[0]} <span>${view==='journal'?String(state.journals.length).padStart(2,'0'):''}</span>`;
  document.querySelectorAll('[data-view]').forEach(b=>{const active=b.closest('.sidebar')?b.dataset.view===JournalWorkflow.group(view):b.dataset.view===view;b.classList.toggle('active',active);b.setAttribute('aria-current',active?'page':'false');});
}

function currentPosition(j) {
  const list=position(), p=list.length===1?list[0]:null;
  const side=p?(p.positionSide==='BOTH'?(Number(p.positionAmt)>0?'LONG':'SHORT'):p.positionSide):j.direction;
  return `<div class="trade-header"><div class="instrument"><span class="coin-icon">${j.symbol==='ETHUSDT'?'⟠':'◈'}</span><div><h2>${escapeHTML(j.symbol.replace('USDT',' / USDT'))}<small>永续合约</small></h2><p>${external(j)?'Variational · 截图记录':exchangeName(j)+' · USDT 永续'} <span class="footer-dot">·</span> ${external(j)?'截图时'+escapeHTML(j.status):p?(journalFresh(j)?'当前账户持仓':'上次账户快照'):escapeHTML(j.status)}</p></div></div>${badge(side)}</div>
    <div class="position-grid">
      <div><span class="data-label">${p?'持仓均价':'记录入场价'}</span><span class="data-value">${external(j)?precise(j.entry_price):priceFmt(p?.entryPrice || j.entry_price)} <small>${currency(j)}</small></span></div>
      <div><span class="data-label">${p?'持仓数量':'记录数量'}</span><span class="data-value">${fmt(p?Math.abs(Number(p.positionAmt)):j.quantity,3)} <small>${escapeHTML(j.symbol.replace('USDT',''))}</small></span></div>
      <div><span class="data-label">${p?'标记价格':external(j)?'截图时状态':'状态'}</span><span class="data-value">${p?priceFmt(p.markPrice):escapeHTML(j.status)}</span></div>
      <div><span class="data-label">${p?'浮动盈亏':'实际入场时间'}</span><span class="data-value ${p?tone(p.unRealizedProfit):'neutral'}">${p?signed(p.unRealizedProfit):escapeHTML(j.opened_at?.replace('T',' ') || '待核对')}</span></div>
    </div>${list.length>1?'<div class="connection-inset">当前交易对存在双向持仓，请分别核对多单与空单。本页未合并均价。</div>':''}`;
}

function filteredJournals(filters=journalFilters,query='') {
  const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return state.journals.filter(j=>(filters.exchange==='all'||(j.exchange||'binance')===filters.exchange)&&(filters.status==='all'||j.status===filters.status)&&terms.every(term=>[j.symbol,j.title,j.tags,exchangeName(j)].join(' ').toLocaleLowerCase().includes(term)))
    .sort((a,b)=>(a.status==='持仓中'?0:1)-(b.status==='持仓中'?0:1)||(b.execution?.opened_ms||b.created||0)-(a.execution?.opened_ms||a.created||0));
}
function journalFilterBar(count,filters=journalFilters) {
  const exchanges=[...new Set(state.journals.map(j=>j.exchange||'binance'))].sort();
  const label=x=>({binance:'Binance',variational:'Variational',okx:'OKX',bybit:'Bybit'}[x]||x);
  const options=(list,value)=>list.map(([v,t])=>`<option value="${escapeHTML(v)}" ${v===value?'selected':''}>${escapeHTML(t)}</option>`).join('');
  return `<section class="journal-filters" aria-label="筛选交易日志"><label>交易所<select id="journal-exchange">${options([['all','全部交易所'],...exchanges.map(x=>[x,label(x)])],filters.exchange)}</select></label><label>状态<select id="journal-status">${options([['all','全部状态'],...['持仓中','已平仓','计划中','待核对'].map(x=>[x,x])],filters.status)}</select></label><span class="journal-filter-count" role="status">显示 <b>${count}</b> / ${state.journals.length} 笔</span><button class="button quiet small" data-action="clear-journal-filters">清除筛选</button></section>`;
}
function focusNoteField(name) {
  const input=$('#plan-form')?.elements[name];
  for(let parent=input?.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  input?.scrollIntoView({block:'center'});input?.focus({preventScroll:true});
}
function journalNavigator(list,j) {
  const index=list.findIndex(n=>n.id===j.id);
  return `<div class="journal-navigator"><button class="journal-choose" data-action="choose-journal" aria-haspopup="dialog"><span class="journal-choose-icon" aria-hidden="true">⌕</span><span><small>交易目录 · ${list.length} 笔${journalFilters.exchange!=='all'||journalFilters.status!=='all'?' · 已筛选':''}</small><b>${escapeHTML(j.symbol)} <span>${exchangeName(j)}</span></b></span><span class="journal-choose-label">切换交易 <i aria-hidden="true">⌄</i></span></button><div class="journal-stepper"><span>${index+1} / ${list.length}</span><button class="button" data-action="step-journal" data-step="-1" aria-label="上一笔交易" ${index<=0?'disabled':''}>‹</button><button class="button" data-action="step-journal" data-step="1" aria-label="下一笔交易" ${index===list.length-1?'disabled':''}>›</button></div></div>`;
}
function journalPickerRows() {
  return filteredJournals(pickerFilters,pickerQuery).map(j=>`<button class="journal-choice ${j.id===selectedId?'selected':''}" data-pick-journal="${escapeHTML(j.id)}" ${j.id===selectedId?'aria-current="true"':''}><span class="journal-choice-symbol">${escapeHTML(j.symbol)}<small>${exchangeName(j)} · ${j.execution?.opened_ms?'入场':'记录于'} ${date(j.execution?.opened_ms||j.created)}</small></span><span class="journal-choice-state"><b class="${j.status==='持仓中'?'is-open':''}">${escapeHTML(j.status)}</b><small>${j.review_status?.personal_complete?'复盘已完成':j.status==='已平仓'?'待写复盘':j.thesis?'已留入场想法':'待补入场想法'}</small></span><span class="journal-choice-arrow" aria-hidden="true">${j.id===selectedId?'✓':'→'}</span></button>`).join('')||'<div class="journal-picker-empty"><b>没有找到这笔交易</b><p>换个关键词，或清除交易所与状态筛选。</p><button class="button" data-action="reset-journal-picker">清除搜索和筛选</button></div>';
}
function refreshJournalPicker() {
  $('#journal-picker-results').innerHTML=journalPickerRows();
  $('#journal-picker .journal-filter-count').textContent=`找到 ${filteredJournals(pickerFilters,pickerQuery).length} / ${state.journals.length} 笔`;
}
function openJournalPicker(reset=true) {
  if(reset){pickerFilters={...journalFilters};pickerQuery='';}
  const dialog=$('#journal-picker');
  dialog.innerHTML=`<div class="dialog-head"><div><h2 id="journal-picker-title">选择一笔交易</h2><p>找到当时的判断，接着写下这次的复盘。</p></div><button class="icon-button" data-action="close-journal-picker" aria-label="关闭交易目录">×</button></div><label class="journal-search-label">搜索交易<input id="journal-search" type="search" value="${escapeHTML(pickerQuery)}" placeholder="搜索合约、标题或标签" autocomplete="off"></label>${journalFilterBar(0,pickerFilters)}<div id="journal-picker-results" class="journal-picker-results" aria-label="交易搜索结果"></div>`;
  refreshJournalPicker();if(!dialog.open)dialog.showModal();$('#journal-search').focus();
}
async function selectJournal(id,filters=journalFilters) {
  if(!state.journals.some(j=>j.id===id))return;
  $('#journal-picker').close();
  if(!await saveNotes(true))return;
  selectedId=id;journalFilters={...filters};reportId=null;
  try{localStorage.setItem('selected-journal',selectedId);}catch{}
  render();$('.journal-choose')?.focus({preventScroll:true});
}
function journalView() {
  if(!state.journals.length){editBase=null;return '<section class="panel empty"><h3>记录你的第一笔交易</h3><p>连接 Binance、OKX 或 Bybit 后可自动归档成交，也可以先手动创建一条日志。</p><button class="button primary" data-action="new">新建交易日志</button> <button class="button" data-action="settings">连接交易所</button></section>';}
  const visible=filteredJournals();
  if(!visible.length){editBase=null;return '<section class="panel empty"><h3>没有符合条件的日志</h3><p>试试其他交易所或状态，已有记录仍保留在账本中。</p><button class="button" data-action="choose-journal">搜索交易</button> <button class="button" data-action="clear-journal-filters">显示全部日志</button></section>';}
  if(!visible.some(j=>j.id===selectedId))selectedId=visible[0].id;
  const j=chosen();
  const imageSources=[...(Array.isArray(j.images)?j.images:[]),...(j.image?[{url:j.image,label:'你补充的原始截图',timeframe:j.timeframe}]:[])];
  const images=imageSources.filter((item,index,all)=>item&&typeof item.url==='string'&&/^\/assets\/[A-Za-z0-9_-]+\.(png|jpe?g|webp)$/.test(item.url)&&all.findIndex(x=>x?.url===item.url)===index);
  return `${journalNavigator(visible,j)}
    <div class="journal-layout"><div class="journal-main"><section class="panel">${currentPosition(j)}${JournalDashboard.cycleSummary(j)}<div class="journal-share-entry"><button class="button primary" data-action="focus-note" data-field="${j.status==='已平仓'?'reflection':'thesis'}">${j.status==='已平仓'?(j.review_status?.personal_complete?'查看我的复盘':'写这笔复盘'):'记录入场想法'} <span aria-hidden="true">↓</span></button><button class="button" data-action="share-trade">分享交易 ↗</button></div></section>${notesForm(j)}<details class="journal-evidence panel" ${evidenceSections.get(j.id)?'open':''}><summary>行情、截图与过程证据</summary>
    <div id="automatic-market">${external(j)?externalSnapshot(j):JournalRecorder.market(j)}</div>
    ${images.map(item=>`<details class="original-image"><summary>查看${escapeHTML(item.label||'交易截图')} · ${escapeHTML(item.timeframe||j.timeframe)}</summary><button class="chart-button" data-action="image" data-image="${escapeHTML(item.url)}" aria-label="放大${escapeHTML(item.label||'交易截图')}"><img class="chart-image" src="${escapeHTML(item.url)}" alt="${escapeHTML(j.symbol)} ${escapeHTML(item.label||'用户提供的历史截图')}"><span class="expand-label">⤢ 查看原图</span></button></details>`).join('')}
    <div class="connection-inset">${external(j)?'用户截图已归档；未接入 Variational 自动采集，当前仓位与挂单状态待核对。':position().length?`持仓数据：${date(journalAccount(j).snapshot.captured_at,true)} 快照。持仓均价不等于单笔成交价。`:j.execution?`已关联 ${exchangeName(j)} 实际成交${j.execution.closed_ms?' · '+date(j.execution.closed_ms,true)+' 已平仓':''}。`:'记录字段待核对，尚未关联特定成交。'} </div>
    <div id="automatic-observations">${external(j)?'':JournalRecorder.observations(j)}</div></details></div><aside class="right-column">${external(j)?screenshotOrders(j):j.execution?.closed_ms?'<section class="panel closed-context"><h3>这笔交易已结束</h3><p>上方结果来自实际成交。原始计划和过程快照保留在日志中，当前账户挂单不用于这笔历史交易。</p></section>':ordersPanel(j)+protectionPanel(j)}${aiPanel(j)}${planPanel(j)}</aside></div>`;
}

function externalSnapshot(j) {
  const s=j.screenshot_snapshot;
  if(!s)return '<div class="connection-inset">手动记录 · 尚未接入该交易所自动采集。</div>';
  return `<section class="cycle-summary"><div class="cycle-summary-head"><div><span class="eyebrow">用户提供的持仓截图</span><h3>截图时的仓位状态</h3></div><strong class="${tone(s.unrealized_pnl)}">${s.unrealized_pnl==null?'浮盈亏未提供':signed(s.unrealized_pnl)+' USD'}</strong></div><div class="cycle-facts"><span>标记价格<b>${precise(s.mark_price)}</b></span><span>名义价值<b>${fmt(s.notional)} USD</b></span><span>清算价格<b>${s.liquidation_price?precise(s.liquidation_price):'待核对'}</b></span><span>截图显示回报率<b class="${tone(s.reported_roi_pct)}">${s.reported_roi_pct==null?'待核对':signed(s.reported_roi_pct)+'%'}</b></span></div><p>归档时间：${date(j.created,true)}（香港）；${j.opened_at?'实际开仓时间见上方':'实际开仓时间未提供'}。以上为截图原值，不是实时行情。</p><p>USD 为截图美元标价，结算资产、手续费与资金费待核对；未计入 Binance 账户收益。</p><p>实际盈亏可从 Variational 的 Portfolio 导出成交、已实现盈亏与资金费账单。<a class="external-export-link" href="https://help.variational.io/en/articles/15029202-how-do-i-download-my-trading-history" target="_blank" rel="noopener noreferrer">查看官方导出方法 ↗</a></p></section>`;
}
function screenshotOrders(j) {
  const orders=j.screenshot_snapshot?.orders||[];
  return `<section class="panel plan-panel"><div class="panel-heading"><h3>截图中的止盈止损</h3><span class="badge warn">待实时核对</span></div>${orders.map(o=>`<div class="plan-row"><span>${o.kind==='take_profit'?'止盈卖出':'止损卖出'} · ${o.close_position?'全部仓位':'部分仓位'}</span><strong>${priceFmt(o.price)} USD</strong></div>`).join('')||'<p class="helper">未提供挂单证据。</p>'}<p class="helper">仅记录原图显示的挂单。Variational 尚未接入，无法确认现在是否仍有效；修改左侧计划不改变这份截图证据。</p></section>`;
}

function field(j,key,label,placeholder,type='text') { return `<label>${label}<input name="${key}" type="${type}" value="${escapeHTML(j[key])}" placeholder="${placeholder}" ${type==='number'?'step="any" min="0.00000001"':type==='datetime-local'?'step="1"':''}></label>`; }
function notesForm(j) {
  const saved=j, draft=drafts.read(j.id), conflict=draftConflicts(saved,draft);
  editBase={...j};dirty=false;
  if(draft){const changed=JournalClient.changedFields(draft.base,draft.values);for(const key of Object.keys(changed))editBase[key]=draft.base[key]??'';j=drafts.restore(j,draft);dirty=Object.keys(changed).length>0;}
  const open=noteSections.get(j.id)||[j.status==='已平仓'?'reflection':'thesis'];
  const section=(key,number,title,sub,body)=>`<details class="note-section" data-note-section="${key}" ${open.includes(key)?'open':''}><summary><span class="note-number" aria-hidden="true">${number}</span><span class="note-section-copy"><b>${title}</b><small>${sub}</small></span><span class="note-chevron" aria-hidden="true">⌄</span></summary><div class="note-section-body">${body}</div></details>`;
  const present=key=>String(j[key]||'').trim().length>0;
  return `<section class="panel notes-panel"><div class="panel-heading"><h3>我的交易笔记</h3><span class="sub-label ${dirty?'unsaved-label':''}" id="save-state" role="status">${dirty?'草稿已恢复 · 尚未提交':'已保存'}</span></div><p class="note-intro">${j.execution?.closed_ms?(j.review_status?.personal_complete?'✓ 本人复盘已完成，修改后会继续保留历史版本。':'先写一句实际执行与下一次改进，保存后计入本人复盘。'):'先留下一句入场理由，其余内容按需展开。修改会保留历史版本。'}</p><form id="plan-form" novalidate data-journal-id="${escapeHTML(j.id)}">${conflict}
  ${section('thesis','01','入场想法',present('thesis')?'已记录入场理由':'补一句当时为什么入场',`
    <label>为什么在这里入场？<textarea name="thesis" placeholder="当时看到了什么？你预期价格会怎样走？">${escapeHTML(j.thesis)}</textarea></label>
    <div class="form-grid"><label>入场时的情绪<select name="emotion">${['未记录','平静','犹豫','急于入场','担心错过','想扳回亏损','其他'].map(e=>`<option ${j.emotion===e?'selected':''}>${e}</option>`).join('')}</select></label>${field(j,'tags','策略标签','例如：支撑反弹、趋势回踩')}</div>`)}
  ${section('risk','02','风险与退出',present('stop_loss')&&present('take_profit')?'已填写计划价位 · 实际挂单另行核对':'填写止损、目标与退出条件',`
    <div class="form-grid">${field(j,'stop_loss',`计划止损（${currency(j)}）`,'补充失效位置','number')}${field(j,'take_profit',`计划目标（${currency(j)}）`,'补充止盈位置','number')}</div>
    <div class="form-grid"><label>什么情况说明我错了？<textarea name="invalidation" placeholder="记录具体、可观察的失效条件">${escapeHTML(j.invalidation)}</textarea></label><label>准备怎样退出？<textarea name="exit_plan" placeholder="止损、止盈与调整仓位的条件">${escapeHTML(j.exit_plan)}</textarea></label></div>
    <div class="note-subheading">盈利保护 <small>由你设置和操作</small></div><div class="form-grid">${field(j,'protect_trigger_pct','有利方向的价格涨幅（%）','例如 1.5','number')}${field(j,'protect_price',`触发后计划止损价（${currency(j)}）`,'填写保护价','number')}</div><p class="helper">按价格相对持仓均价的变化计算，不是含杠杆收益率。留空表示未设定；${external(j)?'该交易所尚未自动同步，触发条件需自行核对。':'保存后仅按同步快照核对。'}计划不会在交易所创建或修改订单。</p>`)}
  ${section('reflection','03','持仓观察与复盘',['reflection','review_keep','review_next'].some(present)?'已写下个人观察 / 复盘':'记下执行过程，以及下一次要改进的事',`<label>持仓观察 / 平仓后的复盘<textarea name="reflection" placeholder="实际发生了什么？哪些动作符合计划，哪些偏离了？下一笔如何改进？">${escapeHTML(j.reflection)}</textarea></label><p class="helper">这是你的个人复盘，AI 分析单独保留。</p>${JournalLearning.fields(j)}`)}
  ${section('facts','04','交易信息','核对合约、数量、时间与记录状态',`
    <div class="form-grid">${field(j,'title','日志标题','给这一笔起个名字')}${field(j,'symbol','合约 / 交易对',external(j)?'CRCL':'ETHUSDT')}</div>
    <div class="form-grid three"><label>方向<select name="direction">${[['UNKNOWN','待核对'],['LONG','做多'],['SHORT','做空']].map(([v,t])=>`<option value="${v}" ${j.direction===v?'selected':''}>${t}</option>`).join('')}</select></label><label>记录状态<select name="status">${['待核对','计划中','持仓中','已平仓'].map(t=>`<option ${j.status===t?'selected':''}>${t}</option>`).join('')}</select></label>${field(j,'opened_at','入场时间（香港）','','datetime-local')}</div>
    <div class="form-grid">${field(j,'entry_price',`记录入场价（${currency(j)}）`,'待核对','number')}${field(j,'quantity','数量（基础币）','待核对','number')}</div><p class="helper">${external(j)?'USD 表示截图美元标价，结算资产待核对。':'与实际成交自动归档的证据分别保留。'}${position().length===1?'<button type="button" class="button text-button" data-action="use-position">填入当前持仓均价与数量</button>':''}</p>`)}
  <div class="form-actions note-save-bar"><span>草稿自动保留，保存后同步<span class="save-shortcut">⌘ / Ctrl + Enter 保存</span></span><button class="button primary" type="submit" ${dirty?'':'disabled'}>${dirty?'保存笔记':'已保存'}</button></div></form></section>`;
}

function aiPanel(j) {
  const list=reports(), report=list.find(a=>a.status==='complete'), running=list.some(a=>a.status==='running'), failed=list.find(a=>a.status==='failed');
  return `<section class="panel ai-panel ${state.ai?.available?'':'ai-unconfigured'}"><div class="ai-title"><h3>AI 复盘</h3><span class="model-chip">${state.ai?.available?'已配置':'未配置'}</span></div>
  ${report&&report.journal_updated!==j.updated?'<p class="report-stale">笔记已更新，下面的报告基于旧版本。请重新生成后对照。</p>':''}
  ${report?`<div class="ai-insight"><b>${escapeHTML(report.title)}</b><br>${escapeHTML(report.summary)}</div>`:`<div class="ai-insight">从这笔 <b>${escapeHTML(j.symbol.replace('USDT',''))} 交易</b>开始，整理当时的判断。</div><div class="ai-check"><span>✓</span><span>${j.image?'原始截图已保存':'交易日志已保存'}</span></div><div class="ai-check"><span>${!external(j)&&journalAccount(j).snapshot.captured_at?'✓':'○'}</span><span>${external(j)?'仅截图证据 · 无成交账单':journalAccount(j).snapshot.captured_at?'交易所持仓与成交已同步':'账户数据等待同步'}</span></div><div class="ai-check"><span>○</span><span>${j.thesis?'开仓理由已记录':'补充开仓理由，复盘才有参照'}</span></div>`}
  ${running?'<div class="busy-row"><span class="loading-mark"></span>正在阅读截图与成交证据…<br>完成后报告会自动保存。</div>':''}
  ${failed&&!report&&!running?`<p class="helper report-failed">${escapeHTML(failed.error)}</p>`:''}
  ${JournalWorkflow.aiButton(state,j,list,navigator.onLine)}${report?'<button class="button wide" data-action="view-report">查看已保存的报告</button>':''}<p class="ai-foot">${report?date(report.created)+' 生成 · '+escapeHTML(report.model):'生成报告不替代本人复盘'}</p></section>`;
}

function planPanel(j) {
  const risk=j.risk, filled=[j.direction!=='UNKNOWN',j.entry_price,j.quantity,j.stop_loss,j.take_profit,j.thesis,j.invalidation].filter(Boolean).length;
  return `<section class="panel plan-panel"><div class="panel-heading"><h3>这笔交易的计划</h3><span class="badge ${filled===7?'long':'warn'}">${filled}/7 已填写</span></div><div class="plan-row"><span>计划止损</span><strong>${j.stop_loss?priceFmt(j.stop_loss)+' '+currency(j):'待补充'}</strong></div><div class="plan-row"><span>计划目标</span><strong>${j.take_profit?priceFmt(j.take_profit)+' '+currency(j):'待补充'}</strong></div><div class="plan-row"><span>计划风险收益比</span><strong>${risk.valid?'1 : '+fmt(risk.rr):'—'}</strong></div>${risk.valid?`<div class="rr-bar"><span style="width:${100/(1+Number(risk.rr))}%"></span><i></i></div><div class="plan-row"><span>理论止损损失</span><strong class="negative">−${fmt(risk.risk)} ${currency(j)}</strong></div>`:''}<p class="plan-tip">${risk.valid?'根据记录价位和数量计算，未包含手续费、资金费及滑点。':escapeHTML(risk.reason)}<br>计划价格不代表交易所已有委托。</p></section>`;
}

function ordersPanel(j) {
  const ctx=journalAccount(j).trade_context?.[j.symbol];
  if(!ctx)return '<section class="panel plan-panel"><h3>交易所挂单</h3><p class="helper">同步对应交易所后读取普通挂单与条件单。</p></section>';
  if(ctx.status!=='complete')return `<section class="panel plan-panel"><h3>交易所挂单</h3><p class="helper">${escapeHTML(ctx.error)}<br>读取失败不代表没有挂单。</p></section>`;
  const orders=ctx.orders.filter(o=>!j.execution||o.positionSide===j.execution.positionSide);
  const p=position()[0],stops=orders.filter(o=>['STOP','STOP_MARKET','TRAILING_STOP_MARKET'].includes(o.type)&&o.side===(j.direction==='LONG'?'SELL':'BUY'));
  let warning=p&&!stops.length?'<p class="report-stale">这次完整挂单快照中未读到该仓位的止损单。</p>':'';
  const additions=orders.filter(o=>o.type==='LIMIT'&&!o.reduceOnly&&!o.closePosition&&o.side===(j.direction==='LONG'?'BUY':'SELL'));
  const targets=orders.filter(o=>o.type.startsWith('TAKE_PROFIT')&&o.side===(j.direction==='LONG'?'SELL':'BUY'));
  if(p&&additions.length&&targets.length===1&&!targets[0].closePosition){const future=Math.abs(Number(p.positionAmt))+additions.reduce((n,o)=>n+Number(o.quantity),0),covered=Number(targets[0].quantity);if(covered<future)warning+=`<p class="report-stale">若现有加仓单全部成交，数量将变为 ${fmt(future,3)}；当前固定数量止盈只覆盖 ${fmt(covered/future*100)}%，其余数量需另行核对退出安排。</p>`;}
  return `<section class="panel plan-panel"><div class="panel-heading"><h3>交易所挂单</h3><span class="badge blue">${journalFresh(j)?'已读取':'历史快照'}</span></div>${warning}${orders.map(o=>`<div class="exchange-order"><div><span class="badge ${o.type.startsWith('TAKE')?'long':'warn'}">${o.type.startsWith('TAKE')?'止盈':o.type.startsWith('TRAILING')?'追踪止损':o.type.startsWith('STOP')?'止损':o.type==='LIMIT'?(o.side==='BUY'?'限价买入':'限价卖出'):o.type==='MARKET'?'市价委托':'其他条件委托'}</span><strong>${priceFmt(Number(o.triggerPrice)||o.price)}</strong></div><p>${o.positionSide==='LONG'?'多仓':o.positionSide==='SHORT'?'空仓':'单向仓位'} · ${o.closePosition?'全部平仓':precise(o.quantity)+' 数量'} · ${Number(o.triggerPrice)?['CONTRACT_PRICE','last','LastPrice'].includes(o.workingType)?'最新成交价触发':['MARK_PRICE','mark','MarkPrice'].includes(o.workingType)?'标记价格触发':['index','IndexPrice'].includes(o.workingType)?'指数价格触发':'触发价格类型待核对':'普通委托'}${o.priceProtect?' · 价格保护开':''}</p></div>`).join('')||'<p class="helper">当前没有读取到止盈止损条件单。</p>'}<p class="helper">${date(ctx.captured_at)} 挂单快照。触发不保证成交价，价格保护开启时可能延迟触发。</p></section>`;
}

function protectionPanel(j) {
  const ctx=journalAccount(j).trade_context?.[j.symbol], p=position()[0], calc=ctx?.planning||{}, entry=Number(p?.entryPrice||calc.entry||j.entry_price), pct=Number(j.protect_trigger_pct), isLong=j.direction==='LONG', isShort=j.direction==='SHORT';
  const configured=pct>0&&Number(j.protect_price)>0&&(isLong||isShort)&&entry>0;
  const threshold=configured?entry*(1+(isLong?1:-1)*pct/100):null;
  const stale=!ctx||ctx.status!=='complete'||!journalFresh(j)||Date.now()-ctx.captured_at>90000;
  const reached=configured&&!stale&&(p?(Number(p.markPrice)/entry-1)*(isLong?1:-1)*100:Number(calc.price_change_pct))>=pct;
  return `<section class="panel plan-panel protection-panel"><div class="panel-heading"><h3>盈利保护计划</h3><span class="badge ${configured?'blue':'warn'}">${configured?'手动执行':'待你设置'}</span></div><div class="plan-row"><span>触发涨幅</span><strong>${j.protect_trigger_pct?precise(j.protect_trigger_pct)+'%':'自定义'}</strong></div><div class="plan-row"><span>对应触发价</span><strong>${threshold?priceFmt(threshold)+' USDT':'—'}</strong></div><div class="plan-row"><span>计划移至</span><strong>${j.protect_price?priceFmt(j.protect_price)+' USDT':'自定义'}</strong></div><p class="helper">${configured?(stale?'请同步最新仓位后核对是否达到阈值。':reached?'本次快照已达到你设置的阈值，请自行核对挂单。':'本次快照尚未达到你设置的阈值。'):'在左侧笔记中填写触发涨幅和保护价，按你的规则执行。'}</p><div class="reference-prices"><span>入场均价 <b>${priceFmt(entry)}</b></span><span>估算含费保本价 <b>${priceFmt(calc.estimated_net_break_even)}</b></span></div><p class="helper">${j.exchange==='okx'||j.exchange==='bybit'?'此账户尚未估算含费保本价；入场均价不含平仓成本。':'含费价考虑已入账成本和预估平仓手续费；未来资金费、滑点另计。'}系统不会自动改挂单。</p></section>`;
}

function tradesView() {
  return `${ledgerPicker()}<section class="panel"><div class="trade-view-heading"><div><h3>每次下单，合并看清</h3><p>一张订单可能分多次撮合，汇总后再看均价和费用。</p></div><div class="segmented"><button data-trade-mode="orders" class="${tradeMode==='orders'?'selected':''}">按订单 <span>${ledgerAccount().orders.length}</span></button><button data-trade-mode="fills" class="${tradeMode==='fills'?'selected':''}">逐笔成交 <span>${ledgerAccount().trades.length}</span></button></div></div><div class="filter-bar"><input id="trade-search" aria-label="搜索交易对" placeholder="搜索交易对，例如 ETH"><select id="trade-side" aria-label="筛选成交方向"><option value="">全部买卖方向</option><option value="BUY">买入</option><option value="SELL">卖出</option></select><span class="helper" id="trade-result-count"></span>${orderFilter?'<button class="button small" data-action="clear-order-filter">清除订单筛选</button>':''}</div><div class="table-wrap"><table><thead><tr><th>成交时间（香港）</th><th>交易对</th><th>买卖 / 持仓方向</th><th>${tradeMode==='orders'?'成交均价':'成交价格'}</th><th>数量</th><th>已实现盈亏</th><th>手续费</th>${tradeMode==='orders'?'<th>撮合明细</th>':''}</tr></thead><tbody id="trade-rows"></tbody></table></div><div class="table-note">按“交易对 + 订单编号”合并当前窗口内的撮合，只统计有成交的订单。一次完整开仓到平仓可能对应多张订单；此数量不是完整交易轮数，也不用于计算胜率。</div></section>`;
}
function renderTrades() {
  const search=$('#trade-search')?.value.toUpperCase()||'', side=$('#trade-side')?.value||'';
  const grouped=tradeMode==='orders';
  const rows=(grouped?ledgerAccount().orders:ledgerAccount().trades).filter(t=>t.symbol.includes(search)&&(!side||t.side===side)&&(!orderFilter||grouped||(t.symbol===orderFilter.symbol&&t.order_key===orderFilter.key)));
  $('#trade-result-count').textContent=`${rows.length} ${grouped?'个成交订单':'笔撮合成交'}`;
  $('#trade-rows').innerHTML=rows.length?rows.slice(0,1000).map(t=>`<tr><td>${date(t.time,true)}</td><td><b>${escapeHTML(t.symbol)}</b></td><td><span class="${t.side==='BUY'?'positive':'negative'}">${t.side==='BUY'?'买入':'卖出'}</span> / ${t.positionSide==='LONG'?'多仓':t.positionSide==='SHORT'?'空仓':'单向'}</td><td>${grouped?Number(t.price).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:8}):precise(t.price,2)}</td><td>${precise(t.qty)}</td><td class="${tone(t.realizedPnl)}">${Number(t.realizedPnl)>0?'+':''}${precise(t.realizedPnl)} USDT</td><td>${grouped?Object.entries(t.fees).map(([a,v])=>precise(v)+' '+escapeHTML(a)).join('<br>'):precise(t.commission)+' '+escapeHTML(t.commissionAsset)}</td>${grouped?`<td><button class="fill-count" data-order-key="${escapeHTML(t.order_key)}" data-symbol="${escapeHTML(t.symbol)}">${t.fill_count} 笔 ›</button></td>`:''}</tr>`).join(''):`<tr><td colspan="8"><div class="empty">${ledgerAccount().snapshot.captured_at?'该范围没有匹配的成交。':'完成所选交易所同步后，真实成交会出现在这里。'}</div></td></tr>`;
  if(rows.length>1000) $('#trade-result-count').textContent=`显示前 1,000 / ${rows.length} 笔，完整记录可导出`;
}

function reviewView() {
  if(!chosen())return '<section class="panel empty"><h3>先留下一笔交易记录</h3><p>有了日志，就可以记录自己的复盘，也可以选择 AI 服务辅助分析。</p><button class="button primary" data-action="new">新建交易日志</button></section>';
  const list=reports(), report=list.find(a=>a.id===reportId)||list.find(a=>a.status==='complete')||list[0], running=list.some(a=>a.status==='running');
  let body='<div class="empty"><div class="empty-icon">✧</div><h3>留下一次认真回看的记录</h3><p>选择日志，结合原始截图、计划与真实成交，生成这笔交易的复盘。</p></div>';
  if(report?.status==='running') body='<div class="empty"><span class="loading-mark"></span><h3>正在整理这笔交易</h3><p>模型正在整理本次提交的交易证据。通常需要 1–3 分钟，完成后会自动保存。</p></div>';
  if(report?.status==='failed') body=`<div class="empty"><h3>这次复盘没有完成</h3><p>${escapeHTML(report.error)}</p>${JournalWorkflow.aiButton(state,chosen(),list,navigator.onLine)}</div>`;
  if(report?.status==='complete') body=`<article class="report"><span class="badge blue">✧ AI 复盘报告</span><h2>${escapeHTML(report.title)}</h2><div class="report-meta">${date(report.created,true)} · ${escapeHTML(report.model)} · 证据截至 ${date(report.evidence_time)}</div>${report.journal_updated<chosen().updated?'<p class="notice">这份报告生成后，交易笔记有过修改。可重新生成以纳入最新记录。</p>':''}<p class="report-summary">${escapeHTML(report.summary)}</p>${[['facts','已确认的事实'],['risks','需要留意的风险'],['questions','还需要补充什么'],['next_review','下一次复盘怎么做']].map(([key,title])=>`<h3>${title}</h3><ul>${report[key].map(s=>`<li>${escapeHTML(s)}</li>`).join('')}</ul>`).join('')}<p class="helper">报告基于生成时的证据，用于回顾交易过程，不会自动执行交易。</p></article>`;
  if(report?.images_omitted)body=body.replace('</article>',`<p class="helper">本次使用纯文本模型，${Number(report.images_omitted)} 张截图未发送、未读取。</p></article>`);
  if(report?.verification_notes?.length) body=body.replace('</article>',`<p class="helper">证据核对：${report.verification_notes.map(escapeHTML).join(' ')}</p></article>`);
  return `<div class="review-layout"><aside class="panel review-picker"><label>选择交易日志<select id="review-journal">${state.journals.map(j=>`<option value="${escapeHTML(j.id)}" ${j.id===chosen().id?'selected':''}>${exchangeName(j)} · ${escapeHTML(j.title||j.symbol)}</option>`).join('')}</select></label>${JournalWorkflow.aiButton(state,chosen(),list,navigator.onLine)}<button class="button wide" data-action="dashboard-journal" data-open-journal="${escapeHTML(chosen()?.id||'')}" data-focus-field="reflection">${chosen()?.review_status?.personal_complete?'查看本人复盘':'写下本人复盘'} →</button><p class="helper">仅发送所选日志、对应交易对的持仓和最近最多 200 笔成交、原始截图。不会发送 API 密钥。</p>${list.length?'<div class="form-divider"></div><p class="helper">报告历史</p>':''}${list.map(a=>`<button class="history-item ${report?.id===a.id?'selected':''}" data-report="${a.id}">${escapeHTML(a.status==='complete'?a.title:a.status==='running'?'正在生成':'生成未完成')}<small>${date(a.created)} · ${a.status==='complete'?'已保存':a.status==='running'?'进行中':'可重试'}</small></button>`).join('')}</aside><section class="panel">${body}</section></div>`;
}

function analyticsView() { return ledgerPicker()+JournalAnalytics.render({...ledgerAccount(),exchange:ledgerExchange},analyticsDay,analyticsMonth); }

function render() { if(renderedView)for(const name of expandablePanels){const panel=$('.'+name);if(panel)panelExpansion.set(renderedView+':'+name,panel.open);} const previous=$('#plan-form');if(previous){noteSections.set(previous.dataset.journalId,[...previous.querySelectorAll('[data-note-section][open]')].map(d=>d.dataset.noteSection));evidenceSections.set(previous.dataset.journalId,Boolean($('.journal-evidence')?.open));} if(state)chrome(); else document.body.dataset.section=JournalTraining.routes.includes(view)?'training':'journal'; $('#content').innerHTML=JournalTraining.routes.includes(view)?JournalTraining.render(view):({journal:journalView,trades:tradesView,review:reviewView,analytics:analyticsView,share:()=>JournalSharing.render(state)}[view])(); if(view==='share')JournalSharing.mount(); if(view==='trades')renderTrades(); for(const name of expandablePanels){const panel=$('.'+name),open=panelExpansion.get(view+':'+name);if(panel&&open!==undefined)panel.open=open;}renderedView=view; }
async function refresh(renderBody=true) { state=await api('/api/state'); if(!state.journals.some(j=>j.id===selectedId))selectedId=state.journals[0]?.id; chrome(); if(renderBody&&!dirty)render(); }
function updateSaveButton() {
  const button=$('#plan-form [type="submit"]');if(!button)return;
  button.disabled=savingNotes||!dirty;
  button.textContent=savingNotes?'正在保存…':dirty?'保存笔记':'已保存';
}
async function saveNotes(silent=false) {
  const form=$('#plan-form'); if(!form||!dirty)return true;
  if(savingNotes)return false;
  const invalid=form.querySelector(':invalid');if(invalid)focusNoteField(invalid.name);
  if(!form.reportValidity())return false;
  keepDraft();
  const values=formValues(), changes=JournalClient.changedFields(editBase,values), jid=chosen().id;
  const payload={...changes,id:jid,base_values:Object.fromEntries(Object.keys(changes).map(k=>[k,editBase[k]??'']))};
  savingNotes=true;const submit=form.querySelector('[type="submit"]');submit.disabled=true;submit.textContent='正在保存…';
  try {
    const saved=await api('/api/journals',payload);
    const latest=formValues(), editedWhileSaving=JSON.stringify(latest)!==JSON.stringify(values);
    editBase=saved;
    if(editedWhileSaving){drafts.write(jid,saved,latest);dirty=true;await refresh(false);keepDraft();}
    else{drafts.remove(jid);dirty=false;await refresh();await JournalTraining.reload(false);await JournalLearning.changed();}
    if(!silent)toast(editedWhileSaving?'已保存刚才提交的版本，新的修改仍在草稿中。':(state.journals.find(j=>j.id===jid)?.review_status?.personal_complete?'本人复盘已完成，进度已更新。':'交易笔记已保存，历史版本已保留。'));return !editedWhileSaving;
  }catch(error){toast(error.message);return false;}
  finally{savingNotes=false;updateSaveButton();}
}
async function sync() { const x=syncExchange();if(x!=='binance'){if(!state.exchanges?.[x]?.configured){openAISettings('exchanges');toast('先在连接设置中填写 '+exchangeName({exchange:x})+' 只读 API 凭证。');return;}try{await api(`/api/exchanges/${x}/sync`,{days:Number($('#sync-days').value)||30});toast('开始同步 '+exchangeName({exchange:x})+'。');await refresh(false);}catch(error){toast(error.message);}return;} if(!state.credentials_configured){openAISettings('exchanges');toast('先在连接设置中填写 Binance 只读 API 凭证。');return;}try{await api('/api/sync',{days:Number($('#sync-days').value)});toast('开始读取 Binance 数据。');setTimeout(()=>refresh(false).catch(e=>toast(e.message)),150);}catch(error){toast(error.message);} }
async function analyze() { if(!await saveNotes(true))return;const gate=JournalWorkflow.aiEntry(state,chosen(),reports(),navigator.onLine);if(gate.action!=='analyze'){if(gate.action==='ai-settings')openAISettings();else if(gate.action==='dashboard-journal'){view='journal';render();focusNoteField('thesis');}toast(gate.hint);return;}try{const result=await api('/api/analyze',{id:chosen().id});reportId=result.id;view='review';await refresh();toast('AI 复盘已开始，报告会自动保存。');}catch(error){toast(error.message);} }

function openAISettings(panel='ai'){JournalAISettings.show(state.ai);JournalExchanges.show(state.exchanges);$('#settings-status').textContent=`Binance：${state.credentials_configured?'已保存密钥':'尚未配置'}。${state.sync.error||'读取持仓、成交与账单。'}`;JournalInterface.settings(panel);$('#settings-dialog').showModal();}
JournalAISettings.bind();
function refreshAIEntries(){document.querySelectorAll('[data-ai-entry]').forEach(el=>{el.outerHTML=JournalWorkflow.aiButton(state,chosen(),reports(),navigator.onLine);});const panel=$('.ai-panel');if(panel){panel.classList.toggle('ai-unconfigured',!state.ai?.available);panel.querySelector('.model-chip').textContent=state.ai?.available?'已配置':'未配置';}}
document.addEventListener('click', async event=>{
  const button=event.target.closest('button,[data-action]');if(!button)return;
  try {
    if(button.dataset.pickJournal){await selectJournal(button.dataset.pickJournal,pickerFilters);return;}
    if(button.dataset.analyticsDay){analyticsDay=button.dataset.analyticsDay;render();return;}
    if(button.dataset.analyticsMonth){analyticsMonth=button.dataset.analyticsMonth;render();return;}
    if(button.dataset.resolve){const key=button.dataset.resolve;if(button.dataset.version==='saved'){if(key==='review_mistakes'){$('#plan-form').querySelectorAll('[name="_review_mistake"]').forEach(el=>el.checked=String(chosen()[key]||'').split(',').includes(el.value));}else $('#plan-form').elements[key].value=chosen()[key]??'';}editBase[key]=chosen()[key]??'';keepDraft();render();return;}
    if(button.dataset.action==='share-trade'){if(!await saveNotes(true))return;JournalSharing.openTrade(chosen());view='share';render();window.scrollTo(0,0);return;}
    if(button.dataset.view){if(!await saveNotes(true))return;view=button.dataset.view;render();window.scrollTo(0,0);return;}
    if(button.dataset.journal){if(!await saveNotes(true))return;selectedId=button.dataset.journal;try{window.localStorage.setItem('selected-journal',selectedId);}catch{}reportId=null;render();return;}
    if(button.dataset.report){reportId=button.dataset.report;render();return;}
    if(button.dataset.tradeMode){tradeMode=button.dataset.tradeMode;orderFilter=null;render();return;}
    if(button.dataset.orderKey){tradeMode='fills';orderFilter={key:button.dataset.orderKey,symbol:button.dataset.symbol};render();return;}
    const action=button.dataset.action;
    if(action==='pin-review-action'){if(!await saveNotes(true))return;await api('/api/review/actions',{journal_id:chosen().id});await refresh();await JournalLearning.reload();toast('已加入首页提醒；下一笔结束后可以记录实践。');return;}
    if(!state&&['settings','ai-settings','recorder-settings'].includes(action)){toast('正在读取连接信息，请稍后再试。');return;}
    if(action==='dashboard-journal'||action==='dashboard-analytics'){if(!await saveNotes(true))return;if(button.dataset.exchangeFilter)journalFilters={exchange:button.dataset.exchangeFilter,status:'all'};if(button.dataset.openJournal){journalFilters={exchange:'all',status:'all'};selectedId=button.dataset.openJournal;try{localStorage.setItem('selected-journal',selectedId);}catch{}}view=action==='dashboard-journal'?'journal':'analytics';render();const focus=button.dataset.focusField;if(['thesis','reflection','invalidation','exit_plan','stop_loss'].includes(focus)){focusNoteField(focus);}else window.scrollTo(0,0);return;}
    if(action==='logout'){if(!await saveNotes(true))return;await api('/api/logout',{});dirty=false;document.body.hidden=true;location.replace('/login');return;}
    if(action==='orders'){if(!await saveNotes(true))return;view='trades';tradeMode='orders';orderFilter=null;render();window.scrollTo(0,0);}
    if(action==='choose-journal'){openJournalPicker();return;}
    if(action==='close-journal-picker'){$('#journal-picker').close();return;}
    if(action==='reset-journal-picker'){pickerFilters={exchange:'all',status:'all'};pickerQuery='';openJournalPicker(false);return;}
    if(action==='step-journal'){const list=filteredJournals(),index=list.findIndex(j=>j.id===selectedId),step=Number(button.dataset.step);if([-1,1].includes(step)&&list[index+step])await selectJournal(list[index+step].id);return;}
    if(action==='focus-note'){if(['thesis','reflection'].includes(button.dataset.field))focusNoteField(button.dataset.field);return;}
    if(action==='clear-journal-filters'){if(button.closest('#journal-picker')){pickerFilters={exchange:'all',status:'all'};openJournalPicker(false);return;}if(!await saveNotes(true))return;journalFilters={exchange:'all',status:'all'};render();return;}
    if(action==='clear-order-filter'){orderFilter=null;render();}
    if(action==='recorder-settings'){const f=$('#recorder-form');f.elements.enabled.checked=state.recorder_settings.enabled;f.elements.auto_ai.checked=state.recorder_settings.auto_ai;f.elements.interval.value=state.recorder_settings.interval;$('#recorder-dialog').showModal();}
    if(action==='close-recorder')$('#recorder-dialog').close();
    if(action==='sync')return sync();
    if(action==='analyze')return analyze();
    if(action==='view-report'){if(!await saveNotes(true))return;view='review';reportId=null;render();}
    if(action==='image'){$('#full-image').src=button.dataset.image||chosen().image;$('#image-dialog').showModal();}
    if(action==='close-image')$('#image-dialog').close();
    if(action==='settings'||action==='ai-settings'){openAISettings(action==='settings'?'exchanges':'ai');const exchange=button.dataset.connectExchange;if(['binance','okx','bybit'].includes(exchange)){const section=$('#connect-'+exchange);section.open=true;section.scrollIntoView({block:'start'});section.querySelector('input')?.focus({preventScroll:true});}}
    if(action==='ai-check'){
      const payload=JournalAISettings.payload($('#ai-settings-form'));JournalAISettings.busy(true);$('#ai-setting-status').textContent='正在读取模型列表，不调用生成…';
      try{JournalAISettings.modelResult(await api('/api/ai/models',payload));}
      catch(error){$('#ai-setting-status').textContent=error.message;}
      finally{JournalAISettings.busy(false);}
    }
    if(action==='close-settings'){$('#settings-dialog').close();refreshAIEntries();if(JournalTraining.routes.includes(view))render();}
    if(action==='export')$('#export-dialog').showModal();
    if(action==='close-export')$('#export-dialog').close();
    if(action==='new'){if(!await saveNotes(true))return;const result=await api('/api/journals',{title:'新的交易记录',symbol:'ETHUSDT',direction:'UNKNOWN',status:'待核对',emotion:'未记录'});selectedId=result.id;journalFilters={exchange:'all',status:'all'};view='journal';await refresh();focusNoteField('title');}
    if(action==='use-position'){const p=position()[0], form=$('#plan-form');form.elements.entry_price.value=p.entryPrice;form.elements.quantity.value=Math.abs(Number(p.positionAmt));form.elements.direction.value=p.positionSide==='BOTH'?(Number(p.positionAmt)>0?'LONG':'SHORT'):p.positionSide;form.elements.status.value='持仓中';dirty=true;keepDraft();toast('已填入当前仓位均价与数量，请核对后保存。');}
  }catch(error){toast(error.message);}
});
document.addEventListener('submit',async event=>{
  event.preventDefault();
  if(event.target.id==='plan-form'){await saveNotes();}
  if(event.target.id==='recorder-form'){const f=event.target;try{await api('/api/recorder',{enabled:f.elements.enabled.checked,auto_ai:f.elements.auto_ai.checked,interval:Number(f.elements.interval.value)});$('#recorder-dialog').close();await refresh(false);toast('采集设置已保存。');}catch(error){toast(error.message);}}
  if(event.target.id==='ai-settings-form'){
    const payload=JournalAISettings.payload(event.target);JournalAISettings.busy(true);
    try{state.ai=await api('/api/ai/config',payload);JournalAISettings.show(state.ai);refreshAIEntries();chrome();$('#ai-setting-status').textContent=state.ai.configured?'已使用 '+state.ai.provider+' · '+state.ai.model+'。生成能力会在首次复盘时验证。':'设置已保存，填写此服务商 API Key 后即可使用。';}
    catch(error){$('#ai-setting-status').textContent=error.message;}
    finally{JournalAISettings.busy(false);}
  }
  if(event.target.id==='settings-form'){
    const form=event.target,button=form.querySelector('button[type="submit"]');button.disabled=true;
    try{await api('/api/credentials',Object.fromEntries(new FormData(form)));form.reset();$('#settings-dialog').close();toast('密钥已保存到本机。');await sync();}catch(error){toast(error.message);}finally{button.disabled=false;}
  }
});
document.addEventListener('input',event=>{
  if(event.target.closest('#plan-form'))keepDraft();
  if(event.target.id==='trade-search')renderTrades();
  if(event.target.id==='journal-search'){pickerQuery=event.target.value;refreshJournalPicker();}
});
document.addEventListener('keydown',event=>{
  if((event.metaKey||event.ctrlKey)&&event.key==='Enter'&&event.target.closest('#plan-form')){event.preventDefault();if(dirty&&!savingNotes)$('#plan-form').requestSubmit();}
});
document.addEventListener('change',async event=>{
  if(event.target.id==='ledger-exchange'){ledgerExchange=event.target.value;orderFilter=null;render();return;}
  if(['journal-exchange','journal-status'].includes(event.target.id)){
    const key=event.target.id==='journal-exchange'?'exchange':'status',value=event.target.value;
    pickerFilters[key]=value;refreshJournalPicker();return;
  }
  if(event.target.closest('#plan-form'))keepDraft();
  if(event.target.id==='market-capture'){JournalRecorder.selectMarket(chosen().id,event.target.value);$('#automatic-market').innerHTML=JournalRecorder.market(chosen());}
  if(event.target.id==='position-capture'){JournalRecorder.selectPosition(chosen().id,event.target.value);$('#automatic-observations').innerHTML=JournalRecorder.observations(chosen());}
  if(event.target.id==='trade-side')renderTrades();
  if(event.target.id==='review-journal'){selectedId=event.target.value;try{window.localStorage.setItem('selected-journal',selectedId);}catch{}reportId=null;render();}
});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
window.addEventListener('hashchange',()=>{const route=location.hash.slice(1)==='strategies'?'training':location.hash.slice(1);if(['training','progress','journal','trades','review','analytics','share'].includes(route)&&!dirty){view=route;render();}});
let pollActive=false;
setInterval(async()=>{
  if(!state||pollActive||document.hidden)return;
  pollActive=true;
  try{
    const old=state, oldIds=state.journals.map(j=>j.id).join(','), oldReports=JSON.stringify(state.analyses.map(a=>[a.id,a.status]));
    await refresh(false);
    const overview=$('#today-account-overview');if(overview&&!overview.contains(document.activeElement)){const methodOpen=Boolean(overview.querySelector('.coverage-method')?.open);overview.innerHTML=JournalDashboard.overview(state);const method=overview.querySelector('.coverage-method');if(method)method.open=methodOpen;}
    const safe=!dirty&&!JournalTraining.routes.includes(view)&&!document.activeElement?.closest('#plan-form,select,dialog');
    if(safe&&view!=='share'&&(view==='journal'||view==='review'||oldIds!==state.journals.map(j=>j.id).join(',')||oldReports!==JSON.stringify(state.analyses.map(a=>[a.id,a.status]))||old.sync.status==='running'))render();
  }catch(error){$('#connection-text').textContent='本地服务暂未连接';$('#recorder-status').innerHTML=JournalRecorder.bar({...state,recorder:{...state.recorder,status:'stopped',error:'本地服务暂未连接，自动采集状态无法确认'}});}finally{pollActive=false;}
},5000);
if(location.hash==='#strategies')history.replaceState(null,'','#training');
const initialRoute=location.hash.slice(1);if(['training','progress','journal','trades','review','analytics','share'].includes(initialRoute))view=initialRoute;
JournalExchanges.init({api,refresh,state:()=>state,toast});
JournalSharing.init({api,toast,render:()=>render()});
JournalLearning.init({api,toast,state:()=>state,refresh,render:()=>{if(view==='progress')render();}});
JournalTraining.init({api,toast,openAISettings,accountState:()=>state,render:()=>{if(JournalTraining.routes.includes(view))render();},navigate:next=>{view=next;render();window.scrollTo(0,0);}});
refresh().catch(error=>{if(JournalTraining.routes.includes(view)){render();return;}$('#content').innerHTML=`<section class="panel empty"><h3>无法读取本地服务</h3><p>${escapeHTML(error.message)}</p><p>请确认“启动交易手记.command”正在运行，再刷新页面。</p></section>`;});
