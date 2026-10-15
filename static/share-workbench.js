(function(root){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const venues={binance:'Binance',okx:'OKX',bybit:'Bybit',variational:'Variational · 手动记录'};
  const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Hong_Kong',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  let ctx,cfg={mode:'month',period:today().slice(0,7),exchange:'binance',nickname:'Trader',note:'',show_amounts:false,show_details:false,show_note:false,metric:'reviews',journal_id:''},current=null,canvas=null,seq=0,timer=null,links=[],busy=false;
  const time=ms=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(ms));
  function init(context){ctx=context;}
  function openTrade(item){cfg={...cfg,mode:'trade',exchange:item.exchange||'binance',journal_id:item.id};current=null;seq++;}
  function render(state){
    const options=state.journals.filter(j=>(j.exchange||'binance')===cfg.exchange);
    if(cfg.mode==='trade'&&!options.some(j=>j.id===cfg.journal_id)){cfg.journal_id=options[0]?.id||'';current=null;}
    return `<div class="share-workbench"><details class="share-settings" open><summary>分享内容</summary><form id="share-form"><label>报告类型<select name="mode">${[['month','月度报告'],['year','年度报告'],['trade','单笔交易']].map(([k,v])=>`<option value="${k}" ${cfg.mode===k?'selected':''}>${v}</option>`).join('')}</select></label>${cfg.mode==='trade'?`<label>选择交易<select name="journal_id">${options.length?options.map(j=>`<option value="${esc(j.id)}" ${j.id===cfg.journal_id?'selected':''}>${esc(j.symbol)} · ${esc(j.status)} · ${time(j.execution?.opened_ms||j.created)}</option>`).join(''):'<option value="">该交易所暂无记录</option>'}</select></label>`:`<label>${cfg.mode==='year'?'年份':'月份'}<input name="period" type="${cfg.mode==='year'?'number':'month'}" value="${cfg.period}" min="${cfg.mode==='year'?'2000':'2000-01'}" max="${today().slice(0,cfg.mode==='year'?4:7)}" required></label>`}<label>交易所<select name="exchange">${Object.entries(venues).map(([k,v])=>`<option value="${k}" ${cfg.exchange===k?'selected':''}>${v}</option>`).join('')}</select></label><div class="share-divider"></div><label>展示昵称<input name="nickname" value="${esc(cfg.nickname)}" maxlength="32" autocomplete="off" required></label>${cfg.mode==='trade'?'':`<label>日历内容<select name="metric"><option value="reviews" ${cfg.metric==='reviews'?'selected':''}>复盘活动</option><option value="pnl" ${cfg.metric==='pnl'?'selected':''}>每日盈亏</option></select></label>`}<label class="share-toggle">显示金额和价格<input type="checkbox" name="show_amounts" ${cfg.show_amounts?'checked':''}></label>${cfg.mode==='trade'?'':`<label class="share-toggle">显示最近平仓明细<input type="checkbox" name="show_details" ${cfg.show_details?'checked':''}></label>`}<label class="share-toggle">显示我的分享感想<input type="checkbox" name="show_note" ${cfg.show_note?'checked':''}></label><label>给下次的自己<textarea name="note" maxlength="180" placeholder="写一句你愿意分享的复盘感想">${esc(cfg.note)}</textarea></label><p class="share-help">只分享这里填写的感想，原始笔记和 AI 报告保持私密。</p><button class="button primary" type="button" data-share="export" disabled>导出图片</button><button class="button" type="button" data-share="publish-panel" disabled>生成分享链接</button><button class="button quiet" type="button" data-share="refresh">刷新报告数据</button></form><div id="share-publish" class="share-publish-panel" hidden><b>发布当前预览</b><p id="share-disclosure"></p><label>链接有效期<select id="share-ttl"><option value="7">7 天</option><option value="30" selected>30 天</option><option value="90">90 天</option></select></label><p>持有链接的人可查看。可随时撤销；已下载的图片无法收回。</p><button class="button primary" data-share="publish">确认发布此报告</button><button class="button quiet" data-share="cancel-publish">取消</button></div><div id="share-result" role="status"></div><p class="share-help">导出和公开页面与当前预览一致。旧链接不会随新交易自动变化。</p></details><section class="share-preview" aria-label="分享报告预览"><div class="share-preview-status"><b>分享预览</b><span id="share-preview-state" role="status">正在生成…</span></div><div id="share-report"><div class="share-empty">正在读取交易记录…</div></div><div id="share-day-trades" class="share-day-trades"></div></section></div><div class="share-mobile-actions"><button class="button primary" data-share="export" disabled>导出图片</button><button class="button" data-share="settings">编辑内容</button></div><section class="share-links"><h3>已生成的分享链接</h3><p class="share-help">仅主动发布的报告会出现在这里。</p><div id="share-links-list"></div></section>`;
  }
  function setReady(ready){document.querySelectorAll('[data-share="export"],[data-share="publish-panel"]').forEach(b=>b.disabled=!ready);}
  function mount(){
    const form=document.getElementById('share-form');if(!form)return;
    form.addEventListener('submit',e=>e.preventDefault());
    form.addEventListener('input',e=>{if(e.target.matches('input:not([type=checkbox]),textarea'))changed(e);});
    form.addEventListener('change',e=>{if(e.target.matches('select,input[type=checkbox]'))changed(e);});
    document.querySelectorAll('[data-share]').forEach(b=>b.addEventListener('click',()=>action(b)));
    if(current){show();setReady(true);}else refresh();
    loadLinks();
    if(matchMedia('(max-width:700px)').matches)document.querySelector('.share-settings').open=false;
  }
  function changed(e){
    const form=e.target.form,oldMode=cfg.mode,oldExchange=cfg.exchange,values=Object.fromEntries(new FormData(form));
    cfg={...cfg,...values,show_amounts:form.elements.show_amounts.checked,show_details:Boolean(form.elements.show_details?.checked),show_note:form.elements.show_note.checked};
    current=null;canvas=null;seq++;setReady(false);clearTimeout(timer);document.getElementById('share-publish').hidden=true;document.getElementById('share-result').textContent='';
    if(oldMode!==cfg.mode){cfg.period=today().slice(0,cfg.mode==='year'?4:7);ctx.render();return;}
    if(oldExchange!==cfg.exchange&&cfg.mode==='trade'){cfg.journal_id='';ctx.render();return;}
    document.getElementById('share-preview-state').textContent='等待更新…';document.getElementById('share-report').classList.add('share-dimmed');
    timer=setTimeout(refresh,350);
  }
  function show(){
    const node=document.getElementById('share-report');if(!node||!current)return;node.classList.remove('share-dimmed');
    const displayed=current.report;
    canvas=JournalShareRender.mount(node,displayed,day=>{
      const box=document.getElementById('share-day-trades');const trades=displayed.details?.filter(t=>t.closed===day.date)||[];
      box.textContent=trades.length?trades.map(t=>`${t.symbol} · ${t.direction==='LONG'?'做多':t.direction==='SHORT'?'做空':'待核对'}`).join(' / '):day.closed?(displayed.show_details?'该日期平仓不在最近 8 笔展示范围。':'交易明细已隐藏。'):'';
    });
    document.getElementById('share-preview-state').textContent=time(current.report.generated_at)+' 生成 · 未发布预览';
    document.getElementById('share-day-trades').textContent='';
  }
  async function refresh(){
    if(!document.getElementById('share-form'))return;
    const request=++seq;current=null;canvas=null;setReady(false);document.getElementById('share-publish').hidden=true;document.getElementById('share-result').textContent='';
    document.getElementById('share-preview-state').textContent='正在生成…';document.getElementById('share-report').classList.add('share-dimmed');
    try{
      const result=await ctx.api('/api/shares/preview',{...cfg});
      if(request!==seq||!document.getElementById('share-form'))return;
      await document.fonts.ready;
      if(request!==seq||!document.getElementById('share-form'))return;
      current=result;show();setReady(true);
    }catch(error){if(request!==seq||!document.getElementById('share-report'))return;document.getElementById('share-preview-state').textContent='生成未完成';document.getElementById('share-report').classList.remove('share-dimmed');document.getElementById('share-report').innerHTML=`<div class="share-empty"><h3>报告暂未生成</h3><p>${esc(error.message)}</p><p>调整选项后重新生成，或点击刷新报告数据。</p></div>`;}
  }
  async function loadLinks(){try{const result=await ctx.api('/api/shares');links=result.items;const box=document.getElementById('share-links-list');if(!box)return;box.innerHTML=links.length?links.map(r=>`<div class="share-link-row"><div><b>${esc(r.title)}</b><small>${time(r.published)} 发布 · ${r.revoked?'已撤销':r.active?time(r.expires)+' 到期':'已过期'}</small></div><div class="share-link-actions">${r.active?`<button class="button small" data-copy-share="${esc(r.id)}">复制链接</button><a class="button small" href="${esc(r.path)}" target="_blank" rel="noopener noreferrer">查看</a><button class="button small" data-revoke-share="${esc(r.id)}">撤销</button>`:''}</div></div>`).join(''):'<p class="share-help">还没有公开的报告。图片导出不会自动发布链接。</p>';box.querySelectorAll('[data-copy-share]').forEach(b=>b.addEventListener('click',()=>copy(links.find(r=>r.id===b.dataset.copyShare).path)));box.querySelectorAll('[data-revoke-share]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await ctx.api('/api/shares/revoke',{id:b.dataset.revokeShare});ctx.toast('分享已撤销，链接停止提供内容。');document.getElementById('share-result').textContent='';await loadLinks();}catch(e){ctx.toast(e.message);b.disabled=false;}}));}catch(error){const box=document.getElementById('share-links-list');if(box)box.textContent='暂时无法读取分享链接：'+error.message;}}
  async function copy(path){const url=location.origin+path;try{await navigator.clipboard.writeText(url);ctx.toast('分享链接已复制。');}catch{const box=document.getElementById('share-result');box.innerHTML=`<label>复制此链接<input readonly value="${esc(url)}" aria-label="分享链接"></label>`;box.querySelector('input').select();}}
  async function action(button){
    const name=button.dataset.share;
    if(name==='settings'){const settings=document.querySelector('.share-settings');settings.open=true;settings.scrollIntoView({behavior:'auto',block:'start'});return;}
    if(name==='cancel-publish'){document.getElementById('share-publish').hidden=true;return;}
    if(name==='refresh'){clearTimeout(timer);await refresh();return;}
    if(!current)return;
    if(name==='publish-panel'){const r=current.report;document.getElementById('share-disclosure').textContent=`将公开昵称、${r.venue}的${r.mode==='trade'?'单笔交易信息':'统计和活动日历'}${r.show_amounts?'、金额和价格':''}${r.show_details?'、最近平仓明细':''}${r.note?'、你填写的感想':''}。`;document.getElementById('share-publish').hidden=false;document.getElementById('share-publish').scrollIntoView({block:'nearest'});return;}
    if(name==='export'){button.disabled=true;try{await JournalShareRender.download(canvas,current.report);ctx.toast('PNG 图片已导出。');}catch(e){ctx.toast(e.message);}finally{button.disabled=!current;}return;}
    if(name==='publish'&&!busy){busy=true;button.disabled=true;const draft=current.draft_id;try{const result=await ctx.api('/api/shares/publish',{draft_id:draft,days:Number(document.getElementById('share-ttl').value),confirmed:true});if(document.getElementById('share-result')&&current?.draft_id===draft){document.getElementById('share-preview-state').textContent=time(current.report.generated_at)+' 生成 · 已发布固定报告';document.getElementById('share-publish').hidden=true;document.getElementById('share-result').innerHTML=`<a class="share-public-url" href="${esc(result.path)}" target="_blank" rel="noopener noreferrer">查看已发布报告 ↗</a><button class="button" id="copy-new-share">复制分享链接</button>`;document.getElementById('copy-new-share').addEventListener('click',()=>copy(result.path));}ctx.toast('当前预览已发布为固定报告。');await loadLinks();}catch(e){ctx.toast(e.message);}finally{busy=false;button.disabled=false;}}
  }
  root.JournalSharing={init,render,mount,openTrade};
})(window);
