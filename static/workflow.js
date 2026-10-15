(function(root){
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=x=>x?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(x)):'时间待核对';
  const group=view=>({analytics:'training',trades:'journal',review:'progress'}[view]||view);
  const sections={training:[['training','账户与风险'],['analytics','收益统计']],journal:[['journal','持仓与日志'],['trades','成交明细']],progress:[['progress','交易回顾'],['review','AI 报告']]};
  function navigation(view){return (sections[group(view)]||[]).map(([v,label])=>`<button class="button ${v===view?'selected':''}" data-view="${v}" aria-current="${v===view?'page':'false'}">${label}</button>`).join('');}
  function aiEntry(state,journal,reports=[],online=true){
    const result=(status,label,action,hint,disabled=false)=>({status,label,action,hint,disabled});
    if(!online)return result('offline','联网后继续','none','当前离线，已保存的报告仍可查看。',true);
    if(!state)return result('loading','正在读取 AI 状态','none','连接状态读取后即可继续。',true);
    if(reports.some(r=>r.status==='running'))return result('running','正在生成复盘…','none','报告会自动保存，无需重复提交。',true);
    if(!state.ai?.available)return result('setup','配置 AI 服务','ai-settings','选择服务商并保存 API Key，已有报告仍可查看。');
    if(!journal)return result('empty','先选择一笔交易','none','交易自动归档后即可开始。',true);
    if(!(journal.execution||journal.screenshot_snapshot||journal.image||journal.images?.length||journal.thesis?.trim()))return result('needs_evidence','补充交易依据','dashboard-journal','先补一句入场理由或交易证据，避免空记录分析。');
    const latest=reports.find(r=>r.status==='complete');
    if(latest&&latest.journal_updated<journal.updated)return result('stale','更新 AI 复盘','analyze','笔记已修改，更新报告以纳入最新内容。');
    if(reports[0]?.status==='failed')return result('failed','重试 AI 复盘','analyze','上次生成未完成，原始记录和已有报告保留。');
    return result('ready',latest?'生成新的复盘':'生成 AI 复盘','analyze',journal.thesis?.trim()?'结合这笔交易的记录与证据生成。':'尚未记录原始理由；报告会明确这一缺口。');
  }
  function aiButton(state,journal,reports,online=true){
    const a=aiEntry(state,journal,reports,online);
    return `<div class="ai-entry" data-ai-entry><button class="button primary wide" data-action="${a.action}" ${a.disabled?'disabled':''} ${journal?`data-open-journal="${esc(journal.id)}" data-focus-field="thesis"`:''}>${a.label}</button><p class="helper" role="status">${a.hint}</p></div>`;
  }
  function progress(state){
    const p=state?.review_progress;
    if(!p)return '<section class="review-worklist" aria-busy="true"><h2>正在整理交易记录…</h2></section>';
    const records=new Map((state.journals||[]).map(j=>[j.id,j]));
    const items=[...p.pending,...p.completed].sort((a,b)=>(b.closed_at||0)-(a.closed_at||0));
    const venue=j=>({binance:'Binance',okx:'OKX',bybit:'Bybit',variational:'Variational'}[j.exchange||'binance']||'交易所');
    const rows=list=>list.map(item=>{
      const j={...(records.get(item.id)||{}),...item},c=root.JournalDashboard?.cycle(j);
      const verified=j.execution?.history_complete===true&&j.execution?.fees&&c?.net!==null&&Number.isFinite(c?.net);
      const amount=verified?`${c.net>0?'+':''}${c.net.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`:'待核对';
      const costs=verified?(c.funding===null?'扣手续费 · 资金费未分摊':'含手续费与已核对资金费'):'查看原始成交证据';
      const note=String(j.reflection||j.review_next||j.review_keep||'').trim();
      const direction=({LONG:'做多',SHORT:'做空'})[j.execution?.direction||j.direction]||'';
      return `<button class="review-task trading-recap-row" data-action="dashboard-journal" data-open-journal="${esc(j.id)}"><span class="trading-recap-copy"><span class="trading-recap-title"><b>${esc(j.symbol)}</b>${direction?`<i>${direction}</i>`:''}${j.ai_complete?'<i class="recap-ai">AI 报告已保存</i>':''}</span><small>${venue(j)} · ${time(j.closed_at)} 平仓</small>${note?`<span class="trading-recap-note">${esc(note.slice(0,100))}${note.length>100?'…':''}</span>`:''}</span><span class="trading-recap-result"><b class="${verified?(c.net>0?'positive':c.net<0?'negative':''):''}">${amount}${verified?' <i>USDT</i>':''}</b><small>${costs}${verified&&c.otherFees.length?' · 另有其他币种费用':''}</small><em>查看交易 →</em></span></button>`;
    }).join('');
    return `<section class="review-worklist trading-recap"><div class="dashboard-section-head"><div><span class="eyebrow">近 30 天 · ${items.length} 笔已确认平仓</span><h2>最近交易，直接回看</h2><p class="helper">回看成交结果和已采集的过程证据，有想法再留一句。</p></div><button class="button quiet small" data-view="analytics">查看收益统计 ↗</button></div>${items.length?rows(items.slice(0,8)):'<div class="review-all-done"><b>暂时没有已确认平仓的交易</b><p>接入的交易所会自动记录成交，平仓后在这里查看。</p><button class="button" data-action="dashboard-journal">查看交易日志</button></div>'}${items.length>8?`<details class="more-reviews"><summary>更早的 ${items.length-8} 笔交易</summary>${rows(items.slice(8))}</details>`:''}<details class="review-method"><summary>记录情况与数据口径</summary><p class="helper">${p.personal.numerator} 笔有本人笔记 · ${p.ai.numerator} 笔有平仓后 AI 报告。笔记按需填写，AI 报告单独保留。仅展示已确认平仓的记录；历史不完整、未分摊资金费和其他币种费用会单独标明。</p></details></section>`;
  }
  const api={group,navigation,aiEntry,aiButton,progress};
  if(typeof module!=='undefined')module.exports=api;else root.JournalWorkflow=api;
})(typeof window!=='undefined'?window:globalThis);
