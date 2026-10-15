(function(root) {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=typeof module!=='undefined'?require('./money.js'):root.JournalMoney;
  const fmt=(v,d=2)=>money.format(v,d);
  const sign=v=>(Number(v)>0?'+':'')+fmt(v);
  const tone=v=>Number(v)>0?'positive':Number(v)<0?'negative':'neutral';
  const dayLabel=k=>k?.slice(5).replace('-',' / ')||'—';
  const clock=t=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(t));

  function curve(days) {
    const values=[0,...days.map(d=>Number(d.cumulative))],low=Math.min(...values),high=Math.max(...values),span=high-low||1;
    const x=i=>52+i/Math.max(1,values.length-1)*800,y=v=>200-(v-low)/span*150;
    const line=values.map((v,i)=>(i?'L':'M')+x(i)+','+y(v)).join(' ');
    const ticks=Array.from({length:4},(_,i)=>{let v=low+span*i/3;return `<line x1="52" x2="852" y1="${y(v)}" y2="${y(v)}" stroke="#e8ebf0"/><text x="42" y="${y(v)+4}" text-anchor="end">${fmt(v,0)}</text>`;}).join('');
    return `<svg class="daily-curve" viewBox="0 0 900 242" role="img" aria-label="按香港日历时间排列的累计已实现净额，起点为零，单位USDT">${ticks}<path d="${line}" fill="none" stroke="#2549d8" stroke-width="2.5"/>${days.map((d,i)=>`<circle cx="${x(i+1)}" cy="${y(Number(d.cumulative))}" r="3" fill="#2549d8"><title>${d.key}：累计 ${sign(d.cumulative)} USDT；当日 ${sign(d.net)} USDT</title></circle>`).join('')}<text x="52" y="233">${dayLabel(days[0]?.key)}</text><text x="852" y="233" text-anchor="end">${dayLabel(days.at(-1)?.key)}</text></svg>`;
  }

  function calendar(data,month,selected) {
    const months=[...new Set(data.days.map(d=>d.key.slice(0,7)))],index=months.indexOf(month);
    const [year,m]=month.split('-').map(Number),offset=(new Date(Date.UTC(year,m-1,1)).getUTCDay()+6)%7;
    const count=new Date(Date.UTC(year,m,0)).getUTCDate(),map=Object.fromEntries(data.days.map(d=>[d.key,d]));
    const cells=Array(offset).fill('<span class="calendar-blank"></span>');
    for(let n=1;n<=count;n++) {
      const key=month+'-'+String(n).padStart(2,'0'),d=map[key];
      cells.push(`<button class="pnl-day ${d?.active?tone(d.net):'inactive'} ${key===selected?'selected':''}" ${d?`data-analytics-day="${key}"`:'disabled'} aria-label="${key} ${d?(d.active?'净额 '+sign(d.net)+' USDT':'无账单或成交'):'不在同步范围'}" aria-pressed="${key===selected}"><span>${n}${d?.partial?'<sup>·</sup>':''}</span><strong>${d?.active?sign(d.net):'—'}</strong><small>${d?.order_count?d.order_count+' 单':d?.ledger_count?'仅账单':''}</small></button>`);
    }
    return `<section class="panel calendar-panel"><div class="panel-heading"><div><h3>每日盈亏</h3><p class="helper">点日期查看费用、订单数和账单</p></div><div class="month-controls"><button aria-label="上个月" data-analytics-month="${months[index-1]||''}" ${index<=0?'disabled':''}>‹</button><b>${year} 年 ${m} 月</b><button aria-label="下个月" data-analytics-month="${months[index+1]||''}" ${index>=months.length-1?'disabled':''}>›</button></div></div><div class="pnl-calendar">${['一','二','三','四','五','六','日'].map(d=>`<span class="weekday">${d}</span>`).join('')}${cells.join('')}</div><p class="helper calendar-caption">香港时间 · USDT · 日期旁的小点表示仅覆盖当天部分时段。灰色日期不在当前同步范围。</p></section>`;
  }

  function detail(day) {
    if(!day)return '';
    return `<section class="panel day-detail" id="day-detail"><div class="panel-heading"><div><h3>${esc(day.key)} 的账单</h3><p class="helper">${day.order_count} 个订单 · ${day.fill_count} 笔撮合${day.partial?' · 仅覆盖同步时段':''}</p></div><strong class="day-total ${tone(day.net)}">${sign(day.net)} <small>USDT</small></strong></div><div class="ledger-split">${[['平仓盈亏',day.realized],['手续费',day.fees],['资金费',day.funding]].map(([label,value])=>`<div><span>${label}</span><b class="${tone(value)}">${sign(value)}</b></div>`).join('')}</div>${day.entries.length?`<div class="table-wrap day-ledger"><table><thead><tr><th>香港时间</th><th>交易所</th><th>交易对</th><th>类型</th><th>金额（USDT）</th></tr></thead><tbody>${day.entries.slice(0,200).map(r=>`<tr><td>${clock(r.time)}</td><td>${esc(r.exchange||'binance')}</td><td>${esc(r.symbol)}</td><td>${{realized:'平仓盈亏',fees:'手续费',funding:'资金费'}[r.kind]}</td><td title="原始金额 ${esc(r.amount)} USDT" class="${tone(r.amount)}">${Number(r.amount)>0?'+':''}${fmt(r.amount,8)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">这一天没有 USDT 盈亏、手续费或资金费账单。</div>'}${day.entries.length>200?`<p class="helper">显示前 200 / ${day.entries.length} 条账单，合计包含全部记录；完整账单可导出 JSON。</p>`:''}</section>`;
  }

  function render(state,selectedDay,selectedMonth) {
    const data=state.analytics;
    if(!data?.available)return `<section class="panel empty"><h3>先同步真实账单</h3><p>${data?.issues?.length?data.issues.map(esc).join("；"):"同步后即可按日期和交易对复盘收益。"}</p><button class="button primary" data-action="${state.exchange&&state.exchange!=='binance'?'exchange-sync':'sync'}" ${state.exchange&&state.exchange!=='binance'?`data-exchange="${esc(state.exchange)}"`:''}>同步 ${esc(({okx:'OKX',bybit:'Bybit'}[state.exchange]||'Binance'))}</button></section>`;
    const days=data.days,s=data.summary,total=state.totals.USDT||{realized:'0',fees:'0',funding:'0',net:'0'};
    const best=days.find(d=>d.key===s.best),worst=days.find(d=>d.key===s.worst);
    const month=days.some(d=>d.key.startsWith(selectedMonth))?selectedMonth:days.at(-1).key.slice(0,7);
    const day=days.find(d=>d.key===selectedDay)||days.filter(d=>d.active).at(-1)||days.at(-1);
    const scale=Math.max(1,...data.symbols.map(r=>Math.abs(Number(r.net))));
    return `<div class="analytics-intro"><p>按账单复盘 <b>${s.active_days}</b> 个有活动的交易日</p><span>${s.positive_days} 天净盈利 · ${s.negative_days} 天净亏损 · 以同步范围为准</span></div><div class="review-kpis"><div><span>最好的一天</span><strong class="${best?tone(best.net):''}">${best?sign(best.net):'—'}</strong><small>${dayLabel(best?.key)} · USDT</small></div><div><span>最差的一天</span><strong class="${worst?tone(worst.net):''}">${worst?sign(worst.net):'—'}</strong><small>${dayLabel(worst?.key)} · USDT</small></div><div><span>手续费净额</span><strong class="${tone(total.fees)}">${sign(total.fees)}</strong><small>交易所账单 · USDT</small></div><div><span>窗口账单最大回撤</span><strong>${fmt(s.max_ledger_drawdown)}</strong><small>累计已实现净额峰谷差 · USDT</small></div></div><div class="analytics-top-grid"><section class="panel analytics-panel"><div class="panel-heading"><div><h3>累计已实现净额</h3><p class="helper">${days[0].key} — ${days.at(-1).key}</p></div><b class="curve-total ${tone(total.net)}">${sign(total.net)} <small>USDT</small></b></div>${curve(days)}<p class="helper">按实际日历时间排列，日末净额累计。起点为 0，不是账户权益曲线；未实现盈亏与划转不计入。</p></section><section class="panel cost-panel"><h3>收益怎么组成</h3>${[['平仓盈亏',total.realized],['手续费',total.fees],['资金费',total.funding],['已实现净额',total.net]].map(([label,value],i)=>`<div class="cost-row ${i===3?'cost-total':''}"><span>${label}</span><strong class="${tone(value)}">${sign(value)}</strong></div>`).join('')}<p class="helper">全部为有符号 USDT 账单。只扣一次手续费；资金费收入或支出单独列出。</p></section></div>${calendar(data,month,day.key)}${detail(day)}<section class="panel symbol-panel"><div class="panel-heading"><div><h3>哪个交易对贡献了收益</h3><p class="helper">按窗口内已实现净额排序；包含该交易对的手续费和资金费</p></div><span class="sub-label">USDT</span></div><div class="table-wrap"><table><thead><tr><th>交易对 / 净额贡献</th><th>平仓盈亏</th><th>手续费</th><th>资金费</th><th>净额</th><th>成交订单</th></tr></thead><tbody>${data.symbols.map(r=>`<tr><td><b>${esc(r.key)}</b><span class="contribution-track"><i class="${tone(r.net)}" style="width:${Math.abs(Number(r.net))/scale*100}%"></i></span></td><td class="${tone(r.realized)}">${sign(r.realized)}</td><td>${sign(r.fees)}</td><td class="${tone(r.funding)}">${sign(r.funding)}</td><td class="${tone(r.net)}"><b>${sign(r.net)}</b></td><td>${r.order_count} 单 <small> / ${r.fill_count} 笔</small></td></tr>`).join('')||'<tr><td colspan="6">此范围暂无数据</td></tr>'}</tbody></table></div><p class="table-note">未分配交易对的账单列为“未归属”，不丢弃。订单数不是完整开平仓轮次，因此这里不把订单胜率当作策略胜率。</p></section>${Object.keys(state.totals).some(a=>a!=='USDT')?`<section class="panel summary-table"><h3>其他结算币种</h3>${Object.entries(state.totals).filter(([a])=>a!=='USDT').map(([a,t])=>`<p>${esc(a)} 净额 ${esc(t.net)}；未折算或混入 USDT。</p>`).join('')}</section>`:''}`;
  }
  root.JournalAnalytics={render};
})(window);
