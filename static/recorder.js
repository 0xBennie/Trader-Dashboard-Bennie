'use strict';
(function(root){
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=(v,d=2)=>v===null||v===undefined||v===''?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});
  const price=v=>number(v,Math.abs(Number(v))<1?6:Math.abs(Number(v))<10?4:2);
  const clock=t=>t?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(t)):'等待首次采集';
  const selectedMarket={},selectedPosition={};
  function bar(state){
    const s=state.recorder||{}, settings=state.recorder_settings||{};
    const dashboard=typeof module!=='undefined'?require('./dashboard.js'):root.JournalDashboard;
    const {healthy,label}=dashboard.health(state);
    if(state.credentials_configured===false)return '<div class="recorder-bar"><div class="recorder-state"><span class="recorder-light"></span><div><strong>Binance · 尚未连接</strong><p>填写只读 API 凭证后开始自动记录，也可以先手动记账。</p></div></div><button class="button small" data-action="settings">连接交易所</button></div>';
    const errors=[s.error,...(s.ledger_issues||[]),...(s.market_errors||[])].filter(Boolean);
    return `<div class="recorder-bar ${healthy?'recording':''}"><div class="recorder-state"><span class="recorder-light"></span><div><strong>Binance · ${label}</strong><p>仓位 ${settings.interval||15} 秒 · 成交 60 秒 · 挂单 30 秒 · 行情 5 分钟</p></div></div><div class="recorder-last">最近成功采集 <b>${clock(s.last_success)}</b></div><button class="button small" data-action="recorder-settings">采集设置</button>${errors.length?`<p class="recorder-error">${errors.map(e=>esc(String(e).replace(/[。；]+$/,''))).join('；')}。已保存的记录仍可查看。</p>`:''}</div>`;
  }
  function candleChart(m,entry){
    const bars=m.candles||[];if(!bars.length)return '<p class="helper">本次未取得已收盘 K 线。</p>';
    const W=820,H=290,left=18,right=90,top=20,bottom=45,plot=W-left-right;
    const values=bars.flatMap(b=>[Number(b.high),Number(b.low)]),max=Math.max(...values),min=Math.min(...values),pad=(max-min)*.1||max*.01||1,hi=max+pad,lo=min-pad;
    const y=v=>top+(hi-Number(v))/(hi-lo)*(H-top-bottom),x=i=>left+(i+.5)*plot/bars.length,w=Math.max(1,plot/bars.length*.65);
    let svg=[0,.25,.5,.75,1].map(f=>{const v=hi-(hi-lo)*f,yy=y(v);return `<line x1="${left}" y1="${yy}" x2="${W-right}" y2="${yy}" stroke="#e9edf5"/><text x="${W-right+10}" y="${yy+4}">${price(v)}</text>`;}).join('');
    svg+=bars.map((b,i)=>{const color=Number(b.close)>=Number(b.open)?'#009b81':'#ed5e7c';return `<g><title>${clock(b.time)} 开 ${esc(b.open)} 高 ${esc(b.high)} 低 ${esc(b.low)} 收 ${esc(b.close)} 量 ${esc(b.volume)}</title><line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${color}"/><rect x="${x(i)-w/2}" y="${Math.min(y(b.open),y(b.close))}" width="${w}" height="${Math.max(1,Math.abs(y(b.open)-y(b.close)))}" fill="${color}"/></g>`;}).join('');
    if(Number(entry)>=lo&&Number(entry)<=hi)svg+=`<line x1="${left}" x2="${W-right}" y1="${y(entry)}" y2="${y(entry)}" stroke="#4161cf" stroke-dasharray="4 4"/><text x="${left+5}" y="${y(entry)-6}" fill="#4161cf">记录均价 ${price(entry)}</text>`;
    svg+=`<text x="${left}" y="${H-12}">${clock(bars[0].time)}</text><text x="${W-right}" y="${H-12}" text-anchor="end">${clock(bars.at(-1).time)}</text>`;
    return `<svg class="auto-candle-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(m.symbol)} 自动保存的 5 分钟 K 线图">${svg}</svg>`;
  }
  function market(j){
    const snapshots=j.market_captures||[],m=snapshots.find(x=>x.id===Number(selectedMarket[j.id]))||snapshots[0];
    if(!m)return '<div class="auto-chart empty"><h3>行情会自动留档</h3><p>首次采集完成后，这里会出现 K 线图。无需上传截图。</p></div>';
    return `<div class="auto-chart"><div class="capture-heading"><div><span class="eyebrow">自动留档</span><h3>${esc(j.symbol)} · 5 分钟行情</h3></div><label>查看历史快照<select id="market-capture" aria-label="行情快照时间">${snapshots.map(s=>`<option value="${s.id}" ${s.id===m.id?'selected':''}>${clock(s.captured)}</option>`).join('')}</select></label></div>${candleChart(m,j.execution?.entry_price||j.entry_price)}<div class="capture-caption"><span>每 5 分钟及仓位、挂单变化时保存</span><span>只绘制已收盘 K 线 · 香港时间</span></div></div>`;
  }
  function groupEvents(events){
    const groups=new Map();
    for(const e of [...events].sort((a,b)=>a.occurred-b.occurred)){
      const key=e.kind==='orders'?`orders:${e.occurred}`:`${e.order_key}:${['open','add'].includes(e.kind)?'in':'out'}`;
      let g=groups.get(key);
      if(!g){g={...e,count:0,quantity:0,notional:0};groups.set(key,g);}
      g.count++;g.occurred=Math.max(g.occurred,e.occurred);
      if(e.kind==='close'||e.kind==='open')g.kind=e.kind;
      if(e.quantity){g.quantity+=Number(e.quantity);g.notional+=Number(e.quantity)*Number(e.price);}
    }
    return [...groups.values()].sort((a,b)=>b.occurred-a.occurred);
  }
  function observations(j){
    const captures=j.captures||[],events=groupEvents(j.events||[]).slice(0,14),selected=captures.find(c=>String(c.captured)===String(selectedPosition[j.id]))||captures[0];
    const pnl=[...captures].reverse().map(c=>Number(c.position?.unRealizedProfit||0)),hi=Math.max(0,...pnl),lo=Math.min(0,...pnl),range=hi-lo||1;
    const sampledHi=pnl.length?Math.max(...pnl):0,sampledLo=pnl.length?Math.min(...pnl):0;
    const points=pnl.map((v,i)=>`${14+i*712/Math.max(1,pnl.length-1)},${12+(hi-v)/range*84}`).join(' ');
    return `<section class="panel auto-observations"><div class="panel-heading"><h3>交易过程 · 自动记录</h3><span class="badge blue">${j.capture_count||0} 次仓位快照</span></div>${j.execution&&!j.execution.history_complete?'<p class="notice">最早开仓超出已读取历史，入场价和入场时间保持待核对。</p>':''}${captures.length?`<div class="observation-stats"><span>近 ${captures.length} 次采样最高浮盈亏 <b>${number(sampledHi)} USDT</b></span><span>最低浮盈亏 <b>${number(sampledLo)} USDT</b></span></div><svg class="pnl-spark" viewBox="0 0 740 112" role="img" aria-label="已采样仓位浮盈亏变化"><line x1="14" x2="726" y1="${12+hi/range*84}" y2="${12+hi/range*84}" stroke="#dce3f2" stroke-dasharray="4 4"/><polyline points="${points}" fill="none" stroke="#4161cf" stroke-width="2"/></svg><label class="snapshot-select">查看仓位快照<select id="position-capture">${captures.map(c=>`<option value="${c.captured}" ${c.captured===selected.captured?'selected':''}>${clock(c.captured)} · 浮盈亏 ${number(c.position?.unRealizedProfit)} USDT</option>`).join('')}</select></label><div class="snapshot-facts"><span>标记价 <b>${price(selected.position?.markPrice)}</b></span><span>数量 <b>${number(Math.abs(Number(selected.position?.positionAmt)),3)}</b></span><span>均价 <b>${price(selected.position?.entryPrice)}</b></span></div><p class="helper">挂单读取：${selected.orders_status==='complete'?`${clock(selected.orders_at)} · ${selected.orders?.length||0} 张`:'未完成，不能判定是否有保护单'}。${(selected.orders||[]).map(o=>`${o.type} ${o.side} ${o.closePosition?'全部':o.quantity} @ ${price(Number(o.triggerPrice)||o.price)}`).map(esc).join('；')}</p>`:'<p class="helper">当前没有持仓过程采样；历史成交已经归档。</p>'}<div class="event-timeline">${events.map(e=>`<div class="trade-event"><i class="event-dot ${e.kind}"></i><div><strong>${({open:'开仓',add:'加仓',reduce:'减仓',close:'平仓',orders:e.initial?'首次挂单快照':'挂单变化'})[e.kind]||esc(e.kind)}</strong><span>${e.kind==='orders'?`${e.orders.length} 张有效挂单`:`${number(e.quantity,3)} · 均价 ${price(e.notional/e.quantity)} · ${e.count} 笔撮合`}</span></div><time>${clock(e.occurred)}</time></div>`).join('')||'<p class="helper">等待自动关联成交。</p>'}</div><p class="helper">成交明细逐笔保存，时间线按订单合并显示；采样间隔内的最高、最低浮盈亏可能未被捕获。</p></section>`;
  }
  root.JournalRecorder={bar,market,observations,price,groupEvents,selectMarket:(jid,id)=>selectedMarket[jid]=id,selectPosition:(jid,id)=>selectedPosition[jid]=id};
  if(typeof module!=='undefined')module.exports=root.JournalRecorder;
})(typeof window!=='undefined'?window:globalThis);
