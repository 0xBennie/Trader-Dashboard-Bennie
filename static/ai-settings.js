(function(root){
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function profileFor(ai,id){return ai.profiles?.[id]||{...ai.presets?.find(p=>p.id===id),provider:id,has_key:false};}
  function payload(form){const f=Object.fromEntries(new FormData(form));return {provider:f.provider,model:f.model.trim(),base_url:f.base_url.trim(),protocol:f.protocol,api_key:f.api_key,supports_images:form.elements.supports_images.checked,json_mode:form.elements.json_mode.checked};}
  let snapshot;
  const form=()=>document.querySelector('#ai-settings-form');
  function choose(id){
    const f=form(),p=profileFor(snapshot,id);
    for(const k of ['provider','model','base_url','protocol'])f.elements[k].value=p[k]||'';
    for(const k of ['supports_images','json_mode'])f.elements[k].checked=Boolean(p[k]);
    f.elements.api_key.value='';f.elements.base_url.readOnly=id!=='custom';
    f.elements.api_key.placeholder=p.has_key?'当前服务商已保存 Key；留空保留':'填写此服务商的 API Key';
    document.querySelector('#ai-key-hint').textContent=p.has_key?'此服务商已有独立密钥。留空保留；更换自定义地址需重新填写。':'此服务商尚未保存密钥。其他服务商的密钥不会带入。';
    document.querySelector('#ai-model-list').innerHTML='';
    document.querySelector('#ai-advanced').open=id==='custom';
    document.querySelector('#ai-setting-status').textContent=id===snapshot.provider_id?(snapshot.configured?'当前配置：'+snapshot.provider+' · '+snapshot.model+'。可以读取模型列表检查连接。':'填写模型与 API Key，保存后用于后续审核和复盘。'):'正在编辑 '+p.label+'。保存后切换为当前 AI 服务。';
    imageHint();
  }
  function imageHint(){document.querySelector('#ai-image-hint').textContent=form().elements.supports_images.checked?'生成时发送所选交易的文字、成交摘要和截图。请确认模型支持图片。':'仅发送文字与结构化证据，报告会注明截图未读取。';}
  function show(ai){
    snapshot=ai;
    const f=form();
    f.elements.provider.innerHTML=ai.presets.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('');
    f.elements.protocol.innerHTML=Object.entries(ai.protocols).map(([k,v])=>`<option value="${esc(k)}">${esc(v)}</option>`).join('');
    choose(ai.provider_id);
  }
  function busy(value){form().querySelectorAll('input,select,button').forEach(el=>el.disabled=value);document.querySelector('#ai-setting-status').setAttribute('aria-busy',String(value));}
  function modelResult(result){document.querySelector('#ai-model-list').innerHTML=result.models.map(m=>`<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('');document.querySelector('#ai-setting-status').textContent=result.message+(result.models.length?' 已读取 '+result.models.length+' 个模型，可在模型输入框选择。':' 未列出模型，可手动填写模型 ID。');}
  function bind(){const f=form();f.elements.provider.addEventListener('change',()=>choose(f.elements.provider.value));f.elements.supports_images.addEventListener('change',imageHint);}
  const api={profileFor,payload,show,choose,busy,modelResult,bind};
  if(typeof module!=='undefined')module.exports=api;else root.JournalAISettings=api;
})(typeof window!=='undefined'?window:globalThis);
