const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../static/ai-settings.js'),'utf8');
function setup(){
 const fields=Object.fromEntries(['provider','model','base_url','protocol','api_key','supports_images','json_mode'].map(k=>[k,{value:'',checked:false,addEventListener(){}}]));
 const nodes=Object.fromEntries(['#ai-key-hint','#ai-model-list','#ai-advanced','#ai-setting-status','#ai-image-hint'].map(k=>[k,{}]));
 const form={elements:fields,querySelectorAll:()=>Object.values(fields)};nodes['#ai-settings-form']=form;
 const context={module:{exports:{}},document:{querySelector:k=>nodes[k]},FormData:class {constructor(f){this.f=f;}*[Symbol.iterator](){for(const [k,v] of Object.entries(this.f.elements))yield [k,v.value];}}};context.globalThis=context;vm.createContext(context);vm.runInContext(source,context);
 const ai={provider_id:'deepseek',provider:'DeepSeek',model:'flash',configured:true,protocols:{openai_chat:'Chat'},presets:[{id:'deepseek',label:'DeepSeek'},{id:'custom',label:'Custom'}],profiles:{deepseek:{provider:'deepseek',label:'DeepSeek',model:'flash',base_url:'https://api.deepseek.com',protocol:'openai_chat',has_key:true,supports_images:true,json_mode:true},custom:{provider:'custom',label:'Custom',model:'',base_url:'',protocol:'openai_chat',has_key:false,supports_images:false,json_mode:true}}};
 return {app:context.module.exports,fields,nodes,form,ai};
}
test('provider change clears unsaved key and exposes explicit custom endpoint and image behavior',()=>{
 const {app,fields,nodes,ai}=setup();app.show(ai);fields.api_key.value='not-a-real-key';app.choose('custom');
 assert.equal(fields.api_key.value,'');assert.equal(fields.model.value,'');assert.equal(fields.base_url.readOnly,false);assert.equal(nodes['#ai-advanced'].open,true);assert.match(nodes['#ai-image-hint'].textContent,/仅发送文字/);
 app.choose('deepseek');assert.equal(fields.api_key.value,'');assert.equal(fields.base_url.readOnly,true);assert.match(fields.api_key.placeholder,/留空保留/);
});
test('settings serialize capabilities as booleans and model listing cannot inject markup',()=>{
 const {app,fields,nodes,form,ai}=setup();app.show(ai);fields.model.value=' selected/model ';fields.supports_images.checked=false;
 const payload=app.payload(form);assert.equal(payload.model,'selected/model');assert.equal(payload.supports_images,false);assert.equal(payload.json_mode,true);
 app.modelResult({models:[{id:'user/model',label:'<img onerror="bad()">'}],message:'连接成功'});assert.doesNotMatch(nodes['#ai-model-list'].innerHTML,/<img/);assert.match(nodes['#ai-model-list'].innerHTML,/&lt;img/);
});
