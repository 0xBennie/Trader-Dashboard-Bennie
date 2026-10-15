'use strict';
(()=>{
  const token=location.pathname.split('/').pop();let report=null,canvas=null,loading=false;
  const status=document.getElementById('public-status'),target=document.getElementById('public-report'),download=document.getElementById('public-download');
  async function load(){
    if(loading)return;loading=true;
    try{const response=await fetch('/api/public/shares/'+encodeURIComponent(token),{cache:'no-store',credentials:'omit'});if(!response.ok)throw new Error(response.status===404?'分享不存在、已过期或已撤销。':'暂时无法验证分享状态，请稍后刷新。');const next=await response.json();if(!report){await document.fonts.ready;report=next;canvas=JournalShareRender.mount(target,report);document.title=report.nickname+' 的'+report.title+' · Trader Dashboard';}status.textContent='这是一份固定时点的分享报告，数据范围与来源见卡片。';download.disabled=false;}catch(e){report=null;canvas=null;target.replaceChildren();download.disabled=true;status.textContent=e.message;}finally{loading=false;}
  }
  download.addEventListener('click',async()=>{if(!canvas)return;download.disabled=true;try{await JournalShareRender.download(canvas,report);}catch(e){status.textContent=e.message;}finally{download.disabled=!report;}});
  load();setInterval(()=>{if(!document.hidden)load();},30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
})();
