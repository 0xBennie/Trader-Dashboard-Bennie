'use strict';
const form=document.querySelector('#login-form'),error=document.querySelector('#login-error');
form.addEventListener('submit',async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;error.textContent='';
  try{
    const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'登录失败，请稍后重试。');
    form.reset();location.replace('/'+location.hash);
  }catch(e){error.textContent=e.message||'暂时无法连接服务。';}finally{button.disabled=false;}
});
// Replace the former offline shell; private pages must always pass server authentication.
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
