(function(root){
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(x,d=8)=>x==null||!isFinite(x)?'—':Number(x).toLocaleString('en-US',{maximumFractionDigits:d});
  const time=x=>x?new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',hour:'2-digit',minute:'2-digit',hour12:false}):'';
  const TONES={go:'verdict-go',conditional:'verdict-maybe',wait_trigger:'verdict-wait',no:'verdict-no'};
  const MARK={pass:'✓',fail:'✗',unknown:'?'};
  const FRAMES=[['5m','5 分钟'],['15m','15 分钟'],['30m','30 分钟'],['1h','1 小时'],['4h','4 小时'],['1d','1 天']];
  const reviews=new Map(),reviewLoading=new Set();
  let deps=null,status=null,form={exchange:'variational',direction:'LONG',timeframe:'5m',setup_id:''},result=null,busy=false,loading=false;
  const store={get(k){try{return root.localStorage?.getItem('mentor:'+k)||'';}catch{return '';}},set(k,v){try{root.localStorage?.setItem('mentor:'+k,v);}catch{}}};

  function statusBar(s,ctx={}){
    const sys=s?.system,risk=s?.open_positions?.by_direction||{};
    const draft=sys&&!sys.confirmed;
    return `<section class="mentor-status" aria-label="当前状态"><div id="cooldown-slot">${ctx.cooldownHtml||''}</div>
      <div class="mentor-status-row"><div><span>持仓中合计风险</span><b>多 ${num(risk.LONG,2)} · 空 ${num(risk.SHORT,2)}</b></div>
      <div><span>我的交易系统</span><b>${sys?`v${esc(sys.version)}${draft?' · 草案':''}`:'读取中'}</b></div>
      <span id="cooldown-action">${ctx.coolAction||''}</span></div>
      ${s?.reminder?`<p class="mentor-reminder"><span>下一笔只改一件事</span>${esc(s.reminder)}</p>`:''}
      ${draft?`<form class="mentor-draft" id="mentor-confirm-form"><p>判断用的是我起草的规则，待你确认。单笔最多亏账户的</p><label><input name="account_risk_pct" type="number" inputmode="decimal" step="0.1" min="0.1" max="10" value="${esc(sys.rules.account_risk_pct)}"><span>%</span></label><button type="submit" class="button small" data-mentor-confirm>确认我的交易系统</button></form>`:''}
    </section>`;
  }

  function setupPicker(setups,chosen){
    return `<div class="mentor-setups" role="radiogroup" aria-label="交易类型">${(setups||[]).filter(s=>s.status!=='paused').map(s=>`<button type="button" role="radio" aria-checked="${s.id===chosen}" class="mentor-setup ${s.id===chosen?'selected':''}" data-mentor-setup="${esc(s.id)}" data-frame="${esc(s.default_timeframe)}"><b>${esc(s.name)}</b><small>${esc(s.summary)}</small></button>`).join('')}</div>`;
  }

  function field(name,label,ph,required=false){
    return `<label>${label}<input name="${name}" type="number" inputmode="decimal" step="any" min="0" ${required?'required':''} value="${esc(form[name]||'')}" placeholder="${esc(ph)}"></label>`;
  }

  function live(){
    const A=root.JournalAssistant;if(!A?.quickCalc)return '';
    const cap=A.riskCap?.(ctxMemory),pct=Number(status?.system?.rules?.account_risk_pct),eq=Number(form.equity);
    const limits=[cap?.cap,eq>0&&pct>0?Math.round(eq*pct)/100:null,Number(form.risk_budget)>0?Number(form.risk_budget):null].filter(x=>x>0);
    const r=A.quickCalc({direction:form.direction,entry:form.entry_price,stop:form.stop_loss,target:form.take_profit,budget:limits.length?String(Math.min(...limits)):''},cap);
    if(r.errors.length)return `<p class="quick-error">${r.errors.map(esc).join('<br>')}</p>`;
    if(r.dist_pct==null)return '<p class="helper">填入场和止损，先算出按上限能开多少；点下面的按钮再看行情判断。</p>';
    return `<p class="mentor-live">止损距离 <b>${num(r.dist_pct,2)}%</b> · 按 ${num(r.budget,2)} 上限可开 <b>${r.qty?num(r.qty):'—'}</b>${r.rr!=null?` · 盈亏比 <b>${num(r.rr,2)}R</b>`:''}</p>${r.warnings.length?`<ul class="quick-warnings">${r.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}`;
  }
  let ctxMemory=null;

  function formHtml(){
    const setups=status?.system?.setups||[],chosen=setups.find(s=>s.id===form.setup_id),gut=form.setup_id==='discretionary';
    const positions=(status?.open_positions?.items||[]).filter(p=>p.direction===form.direction);
    return `<form id="mentor-form" class="mentor-flow" autocomplete="off">
      <section class="mentor-step"><h3><i>1</i>做什么</h3>
        <div class="mentor-grid"><label>合约<input name="symbol" required value="${esc(form.symbol||'')}" placeholder="NEAR 或 NEARUSDT" autocapitalize="characters"></label>
        <label>交易所<select name="exchange">${[['variational','Variational'],['binance','Binance'],['okx','OKX'],['bybit','Bybit']].map(([v,l])=>`<option value="${v}" ${form.exchange===v?'selected':''}>${l}</option>`).join('')}</select></label></div>
        <div class="quick-direction" role="group" aria-label="方向"><button type="button" class="${form.direction!=='SHORT'?'selected long':''}" data-mentor-dir="LONG" aria-pressed="${form.direction!=='SHORT'}">做多</button><button type="button" class="${form.direction==='SHORT'?'selected short':''}" data-mentor-dir="SHORT" aria-pressed="${form.direction==='SHORT'}">做空</button></div>
        ${setupPicker(setups,form.setup_id)}
        <label class="mentor-frame">决策周期<select name="timeframe">${FRAMES.map(([v,l])=>`<option value="${v}" ${form.timeframe===v?'selected':''}>${l}</option>`).join('')}</select></label>
        ${chosen?`<p class="helper">${esc(chosen.name)}：${esc(chosen.summary)}</p>`:'<p class="helper">不选类型也能判断，但最多给到「有条件做」。</p>'}
      </section>
      <section class="mentor-step"><h3><i>2</i>价格和风险</h3>
        <div class="mentor-grid">${field('entry_price','入场价','计划价位',true)}${field('stop_loss','止损价','错了在哪')}${field('take_profit','止盈价','留空给建议')}${field('risk_budget','本笔最多亏','默认用你的上限')}${field('equity','这个交易所的账户权益','用来算账户 %')}
        ${positions.length?`<label>这是加仓吗<select name="add_journal_id"><option value="">不是，新开一笔</option>${positions.map(p=>`<option value="${esc(p.journal_id)}" ${form.add_journal_id===p.journal_id?'selected':''}>加到 ${esc(p.symbol)} · ${num(p.qty)} @ ${num(p.entry)}</option>`).join('')}</select></label>`:''}</div>
        ${gut?`<label>押什么反应<input name="bet" maxlength="500" required value="${esc(form.bet||'')}" placeholder="例如：5m 放量收回 0.48"></label><label>多久没出现就走<input name="time_limit" maxlength="500" required value="${esc(form.time_limit||'')}" placeholder="例如：6 根 5m K 线"></label>`:''}
        <div id="mentor-live" aria-live="polite">${live()}</div>
        <button class="button primary mentor-go" type="submit" ${busy?'disabled':''}>${busy?'正在读 Binance K 线…':'看行情，判断这笔'}</button>
      </section></form>`;
  }

  function bar(d){return `<div class="mentor-dim"><span>${esc(d.name)}</span><i><b style="width:${Math.max(0,Math.min(100,d.score/d.max*100))}%"></b></i><em>${num(d.score,1)}</em></div>`;}

  function verdictCard(r){
    const j=r?.judgment;if(!j)return '';
    if(j.state==='unavailable'||!TONES[j.state])return `<section class="mentor-verdict verdict-none"><h3>③ 这次没能判断</h3><p>${esc(j.reason||'形态判断没有完成。')}</p><p class="helper">下方「详细计划」里的风险和行情检查仍然有效。</p></section>`;
    const z=j.sizing||{},cur=esc(z.currency||''),plan=r.plan||{};
    const head=j.state==='wait_trigger'&&j.trigger_price!=null?`<p class="verdict-line">${esc(j.wait_for)}</p><p class="verdict-price">${num(j.trigger_price)}</p>`
      :j.state==='no'?`<p class="verdict-line">${esc(j.hard_fails[0]||j.counter||'综合评分太低。')}</p>`
      :j.state==='go'?`<p class="verdict-line">位置、触发和风险都满足你的规则。按计划下单，止损挂上。</p>`
      :`<p class="verdict-line">${esc(j.wait_for||'可以做，但有没满足的条件。')}</p>`;
    const tier=(k,label)=>`<div class="${z.recommended===k?'chosen':''}"><span>${label}${z.recommended===k?' · 建议':''}</span><b>${num(k==='trial'?z.trial_qty:z.full_qty)}</b></div>`;
    const basis=[z.cap_amount?`个人上限 ${num(z.cap_amount,2)}`:'',z.cap_pct_amount?`账户 ${num(z.equity,2)} 的 % 上限 ${num(z.cap_pct_amount,2)}`:''].filter(Boolean).join('，');
    const flow=j.flow||{};
    return `<section class="mentor-verdict ${TONES[j.state]}" aria-live="polite">
      <div class="verdict-head"><span class="verdict-badge">${esc(j.label)}</span><span>${esc(plan.symbol)} · ${plan.direction==='SHORT'?'做空':'做多'} · ${esc(j.setup?.name)} · ${num(j.score,0)} 分</span></div>
      ${head}
      ${j.hard_fails?.length>1?`<ul class="verdict-fails">${j.hard_fails.slice(1).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}
      ${j.counter&&j.state!=='no'?`<p class="verdict-counter"><span>最强反证</span>${esc(j.counter)}</p>`:''}
      <div class="verdict-size">${tier('trial','试单')}${tier('full','正常仓位')}</div>
      <p class="helper">预算 ${num(z.budget,2)} ${cur}${basis?`（取最小：${esc(basis)}）`:''}。打到止损约亏 ${num(z.risk,2)} ${cur}${z.combined_pct!=null?`；加上已有同方向持仓占账户 ${num(z.combined_pct,2)}%（上限 ${num(z.combined_limit_pct,2)}%）`:''}。${esc(z.note||'')}</p>
      ${j.add?`<p class="helper">加仓后整笔 ${num(j.add.total_qty)} @ ${num(j.add.avg_entry)}，按止损 ${num(j.add.stop)} 合计风险 ${num(j.add.total_risk,2)}；按新止损最多再加 ${num(j.add.room_qty)}。</p>`:''}
      <div class="verdict-levels"><div><span>止损参考</span><b>${num(j.levels?.stop)}</b><small>${esc(j.levels?.stop_basis)}</small></div><div><span>止盈参考</span><b>${num(j.levels?.target)}</b><small>${esc(j.levels?.target_basis)}</small></div></div>
      <div class="mentor-dims">${(j.dimensions||[]).map(bar).join('')}</div>
      <details class="mentor-checks"><summary>每一项检查</summary>${(j.dimensions||[]).map(d=>`<h4>${esc(d.name)} ${num(d.score,1)} / ${d.max}</h4><ul>${d.checks.map(c=>`<li class="check-${esc(c.status)}"><i aria-hidden="true">${MARK[c.status]||'?'}</i><span>${esc(c.label)}</span><small>${esc(c.value)}</small></li>`).join('')}</ul>`).join('')}
        <h4>Binance 资金面</h4><p class="helper">${flow.status==='complete'||flow.status==='partial'?`主动买卖比 ${num(flow.taker_ratio_avg,2)} · OI ${flow.oi_change_pct==null?'—':num(flow.oi_change_pct,2)+'%'} · 头部账户多空比 ${num(flow.top_long_short,2)} · 资金费 ${flow.funding_rate==null?'—':num(flow.funding_rate*100,4)+'%'}`:'未取得'}</p></details>
      ${(j.lesson_cards||[]).map(c=>`<article class="mentor-lesson"><h4>${esc(c.title)}</h4><p>${esc(c.body)}</p><p><b>这样做：</b>${esc(c.do)}</p></article>`).join('')}
      <p class="helper">${j.draft?'按草案规则判断。':''}${esc(j.source?.note||'')} 数据截至 ${time(j.source?.through)}。${esc(j.note||'')}</p>
    </section>`;
  }

  const QTONE={right_clean:'verdict-go',good_loss:'verdict-wait',clean_unknown:'verdict-wait',lucky:'verdict-maybe',both:'verdict-no'};
  function reviewPanel(r){
    if(!r)return '<p class="helper">正在整理这笔的导师复盘…</p>';
    if(r.status!=='complete')return `<p class="helper">${esc(r.note||'这笔暂时不能复盘。')}</p>`;
    const j=r.judgment||{},hit={target:'先到止盈',stop:'先到止损',ambiguous:'同一根 K 线两边都碰到'}[j.first_hit];
    const verdict={go:'可以做',conditional:'有条件做',wait_trigger:'等触发',no:'别做'}[r.check_state];
    return `<div class="mentor-review-body ${QTONE[r.quadrant]||''}">
      <div class="verdict-head"><span class="verdict-badge">${esc(r.quadrant_label)}</span>${r.pnl!=null?`<span>结果 ${num(r.pnl,2)} ${esc(r.pnl_asset||'')}</span>`:''}</div>
      <div class="review-split"><div><h4>想法对不对</h4><p><b>${j.state==='right'?'对':j.state==='wrong'?'错':'无法判断'}</b>${hit?` · ${hit}`:''}</p>${j.mfe_r!=null?`<p class="helper">持仓期间最多浮盈 ${num(j.mfe_r,2)}R，最多浮亏 ${num(j.mae_r,2)}R</p>`:''}<p class="helper">${esc(j.note)}</p></div>
      <div><h4>执行好不好</h4><ul class="mentor-checks-list">${(r.execution||[]).map(c=>`<li class="check-${esc(c.status)}"><i aria-hidden="true">${MARK[c.status]||'?'}</i><span>${esc(c.label)}</span>${c.value?`<small>${esc(c.value)}</small>`:''}</li>`).join('')}</ul></div></div>
      ${r.check_id||r.check_state?`<p class="helper">开仓前判断：${esc(verdict||'未完成')}${r.link==='auto'?'（自动关联：入场前 2 小时内同币同方向的最近一次判断）':''}</p>`:'<p class="helper">这笔没有开仓前判断。下一笔先在「开仓前问助手」走一遍三步。</p>'}
      ${r.next_action?`<div class="review-next"><span>下一笔只改一件事</span><p>${esc(r.next_action.instruction)}</p>${r.adopted?'<p class="helper">已放进首页提醒。</p>':`<button type="button" class="button small" data-mentor-adopt="${esc(r.journal_id)}">采纳，放进首页提醒</button>`}</div>`:'<p class="review-next done">这笔按计划执行，不需要新增提醒。</p>'}
      ${(r.lesson_cards||[]).map(c=>`<article class="mentor-lesson"><h4>${esc(c.title)}</h4><p>${esc(c.body)}</p></article>`).join('')}
      <p class="helper">${r.reference_only?'Binance 参考行情，不是这个交易所的成交证据。':''}${esc(r.note)}</p></div>`;
  }
  function reviewSlot(j){
    if(!j||j.status!=='已平仓')return '';
    return `<section class="panel mentor-review" data-mentor-review="${esc(j.id)}"><h3>导师复盘</h3>${reviewPanel(reviews.get(j.id))}</section>`;
  }
  async function loadReview(id){
    if(reviewLoading.has(id))return;reviewLoading.add(id);
    try{reviews.set(id,await deps.api('/api/mentor/review?id='+encodeURIComponent(id)));}
    catch(e){reviews.set(id,{status:'error',note:e.message});}
    finally{reviewLoading.delete(id);const el=document.querySelector(`[data-mentor-review="${CSS.escape(id)}"]`);if(el)el.outerHTML=reviewSlot({id,status:'已平仓'});}
  }

  function render(ctx={}){
    ctxMemory=ctx.memory||null;
    return `<div class="mentor" id="mentor">${statusBar(status,ctx)}${formHtml()}<div id="mentor-verdict">${verdictCard(result)}</div></div>`;
  }

  function repaint(){const el=document.querySelector('#mentor-form');if(el)el.outerHTML=formHtml();}
  async function loadStatus(){if(loading)return;loading=true;try{status=await deps.api('/api/assistant/status');deps.render();}catch(e){deps.toast(e.message);}finally{loading=false;}}
  function mount(){if(document.querySelector('#mentor')&&!status)loadStatus();document.querySelectorAll('[data-mentor-review]').forEach(el=>{if(!reviews.has(el.dataset.mentorReview))loadReview(el.dataset.mentorReview);});}
  function init(d){
    deps=d;form.equity=store.get('equity:'+form.exchange);
    document.addEventListener('input',e=>{const f=e.target.closest('#mentor-form');if(!f)return;const prev=form.exchange;Object.assign(form,Object.fromEntries(new FormData(f)));
      if(e.target.name==='equity')store.set('equity:'+form.exchange,form.equity||'');
      if(form.exchange!==prev){form.equity=store.get('equity:'+form.exchange);repaint();return;}
      const o=document.querySelector('#mentor-live');if(o)o.innerHTML=live();});
    document.addEventListener('change',e=>{if(e.target.closest('#mentor-form')&&['exchange','add_journal_id','timeframe'].includes(e.target.name)){Object.assign(form,Object.fromEntries(new FormData(e.target.closest('form'))));if(e.target.name==='exchange')form.equity=store.get('equity:'+form.exchange);repaint();}});
    document.addEventListener('click',async e=>{const b=e.target.closest('button');if(b?.dataset.mentorAdopt){b.disabled=true;try{await deps.api('/api/mentor/adopt',{journal_id:b.dataset.mentorAdopt});status=null;const cached=reviews.get(b.dataset.mentorAdopt);if(cached)cached.adopted=true;b.textContent='已放进提醒';deps.toast('已加入首页提醒，下次开仓前会看到。');}catch(err){b.disabled=false;deps.toast(err.message);}return;}if(!b||!b.closest('#mentor'))return;
      if(b.dataset.mentorDir){Object.assign(form,Object.fromEntries(new FormData(b.closest('form'))),{direction:b.dataset.mentorDir});repaint();}
      if(b.dataset.mentorSetup){Object.assign(form,Object.fromEntries(new FormData(b.closest('form'))),{setup_id:form.setup_id===b.dataset.mentorSetup?'':b.dataset.mentorSetup,timeframe:b.dataset.frame||form.timeframe});repaint();}});
    document.addEventListener('submit',async e=>{
      if(e.target.id==='mentor-confirm-form'){e.preventDefault();try{await deps.api('/api/system/confirm',{account_risk_pct:Number(new FormData(e.target).get('account_risk_pct'))});status=null;await loadStatus();deps.toast('交易系统已确认，之后按新版本判断。');}catch(err){deps.toast(err.message);}return;}
      if(e.target.id!=='mentor-form')return;e.preventDefault();if(busy)return;
      Object.assign(form,Object.fromEntries(new FormData(e.target)));busy=true;repaint();
      try{result=await deps.api('/api/assistant/check',{exchange:form.exchange,symbol:form.symbol,direction:form.direction,timeframe:form.timeframe,entry_price:form.entry_price,quantity:'',stop_loss:form.stop_loss||'',take_profit:form.take_profit||'',risk_budget:form.risk_budget||'',equity:form.equity||'',thesis:form.bet||'',invalidation:'',setup_id:form.setup_id||'',bet:form.bet||'',time_limit:form.time_limit||'',add_journal_id:form.add_journal_id||''});
        root.JournalAssistant?.remember?.(result);}
      catch(err){deps.toast(err.message);}
      finally{busy=false;repaint();const v=document.querySelector('#mentor-verdict');if(v){v.innerHTML=verdictCard(result);v.scrollIntoView({behavior:'smooth',block:'start'});}}
    });
  }
  const api={init,mount,render,statusBar,setupPicker,verdictCard,reviewPanel,reviewSlot,set:s=>{if('status' in s)status=s.status;if('form' in s)form={...form,...s.form};if('result' in s)result=s.result;}};
  if(typeof module!=='undefined')module.exports=api;else root.JournalMentor=api;
})(typeof window!=='undefined'?window:globalThis);
