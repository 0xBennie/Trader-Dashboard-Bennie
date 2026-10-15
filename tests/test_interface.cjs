const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../static/interface.js');
function fixture(){
  const handlers={},closeHandlers={};let focused=null,modal=false;
  const tabs=['exchanges','ai'].map(name=>({dataset:{settingsPanel:name},attrs:{},tabIndex:0,setAttribute(k,v){this.attrs[k]=v;},focus(){focused=this;},closest(s){return s==='[data-settings-panel]'?this:null;}}));
  const panels=['exchanges','ai'].map(name=>({dataset:{settingsContent:name},hidden:false,input:{value:'unsaved value'}}));
  const dialog={scrollTop:90,querySelectorAll(s){return s==='[data-settings-panel]'?tabs:panels;},addEventListener(k,fn){closeHandlers[k]=fn;}};
  tabs.forEach(t=>t.parentElement={querySelectorAll:()=>tabs});
  const summary={focus(){focused=this;}},menu={open:false,contains:target=>target.insideMenu,querySelector:()=>summary};
  const doc={getElementById(id){return id==='settings-dialog'?dialog:id==='page-tools'?menu:null;},querySelector(){return modal?dialog:null;},addEventListener(k,fn){handlers[k]=fn;}};
  ui.bind(doc);
  return {tabs,panels,dialog,doc,handlers,menu,summary,closeHandlers,get focused(){return focused;},set modal(v){modal=v;}};
}
test('settings sections keep unsubmitted input while making only the selected section reachable',()=>{
  const f=fixture();ui.settings('ai',f.doc);
  assert.equal(f.panels[0].hidden,true);assert.equal(f.panels[1].hidden,false);
  assert.equal(f.tabs[0].tabIndex,-1);assert.equal(f.tabs[1].attrs['aria-selected'],'true');
  ui.settings('exchanges',f.doc);
  assert.equal(f.panels[1].input.value,'unsaved value');assert.equal(f.panels[1].hidden,true);
  assert.equal(f.dialog.scrollTop,0);
});
test('settings keyboard navigation wraps and supports Home and End',()=>{
  const f=fixture();let prevented=false;
  f.handlers.keydown({target:f.tabs[0],key:'ArrowLeft',preventDefault(){prevented=true;}});
  assert.equal(f.focused,f.tabs[1]);assert.equal(f.panels[1].hidden,false);assert.equal(prevented,true);
  for(const [key,index] of [['Home',0],['End',1]]){
    f.handlers.keydown({target:f.tabs[1],key,preventDefault(){}});assert.equal(f.focused,f.tabs[index]);
  }
});
test('closing a dialog returns overflow-menu focus to the visible summary',()=>{
  const f=fixture(),target={closest:()=>null};f.menu.open=true;f.modal=true;
  f.handlers.click({target});assert.equal(f.menu.open,true);
  f.modal=false;f.closeHandlers.close();assert.equal(f.menu.open,false);assert.equal(f.focused,f.summary);
});
test('Escape dismisses the tools disclosure but leaves an open modal in charge',()=>{
  const f=fixture(),target={closest:()=>null};f.menu.open=true;f.modal=true;
  f.handlers.keydown({target,key:'Escape'});assert.equal(f.menu.open,true);
  f.modal=false;f.handlers.keydown({target,key:'Escape'});assert.equal(f.menu.open,false);assert.equal(f.focused,f.summary);
});
test('an outside click dismisses tools without interfering with an action inside',()=>{
  const f=fixture();f.menu.open=true;
  f.handlers.click({target:{insideMenu:true,closest:()=>null}});assert.equal(f.menu.open,true);
  f.handlers.click({target:{insideMenu:false,closest:()=>null}});assert.equal(f.menu.open,false);
});
