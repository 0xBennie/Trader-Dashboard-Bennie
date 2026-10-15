(function(root){
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=x=>x?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(x)):'时间待核对';
  const group=view=>({analytics:'training',trades:'journal',review:'progress'}[view]||view);
  const sections={training:[['training','账户与风险'],['analytics','收益统计']],journal:[['journal','持仓与日志'],['trades','成交明细']],progress:[['progress','复盘待办'],['review','AI 报告']]};
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
    if(!p)return '<section class="review-worklist" aria-busy="true"><h2>正在读取复盘进度…</h2></section>';
    const done=p.personal.numerator,total=p.personal.denominator,ai=p.ai.numerator;
    const rows=(items,complete)=>items.map(j=>`<button class="review-task" data-action="dashboard-journal" data-open-journal="${esc(j.id)}" data-focus-field="reflection"><span><b>${esc(j.symbol)}</b><small>${time(j.closed_at)} 平仓 · ${complete?'本人复盘已保存 · ':''}${j.ai_complete?'AI 报告已生成':'尚无 AI 报告'}</small></span><strong>${complete?'查看复盘':'开始复盘'} →</strong></button>`).join('');
    return `<section class="review-worklist"><div class="dashboard-section-head"><div><span class="eyebrow">近 30 天 · 已确认平仓的交易</span><h2>${total?`已完成 ${done} / ${total} 笔本人复盘`:'从第一笔平仓记录开始'}</h2></div><span class="badge blue">AI 报告 ${ai} / ${total}</span></div>${total?`<progress aria-label="本人复盘完成进度" value="${done}" max="${total}"></progress>`:''}<details class="review-method"><summary>如何计数</summary><p class="helper">本人复盘与 AI 报告分别计数，不受风险政策开关影响。外部截图未确认平仓时间时不计入。</p></details>${p.pending.length?rows(p.pending.slice(0,5),false):`<div class="review-all-done"><b>${total?'这段时间的本人复盘已全部完成':'暂时没有已确认平仓的交易'}</b><p>${total?'新交易结束后，会继续出现在这里。':'已有持仓仍可记录观察，成交归档后会自动进入待办。'}</p><button class="button" data-action="dashboard-journal">查看交易日志</button></div>`}${p.pending.length>5?`<details class="more-reviews"><summary>其余 ${p.pending.length-5} 笔待复盘</summary>${rows(p.pending.slice(5),false)}</details>`:''}${p.completed.length?`<details class="completed-reviews"><summary>已完成的本人复盘 · ${p.completed.length}</summary>${rows(p.completed,true)}</details>`:''}</section>`;
  }
  const api={group,navigation,aiEntry,aiButton,progress};
  if(typeof module!=='undefined')module.exports=api;else root.JournalWorkflow=api;
})(typeof window!=='undefined'?window:globalThis);
