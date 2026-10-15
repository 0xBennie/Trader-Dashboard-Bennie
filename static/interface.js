(function(root){
  'use strict';
  function settings(panel='exchanges', doc=document) {
    const selected=panel==='ai'?'ai':'exchanges';
    const dialog=doc.getElementById('settings-dialog');
    if(!dialog)return;
    dialog.querySelectorAll('[data-settings-panel]').forEach(button=>{
      const active=button.dataset.settingsPanel===selected;
      button.setAttribute('aria-selected',String(active));
      button.tabIndex=active?0:-1;
    });
    dialog.querySelectorAll('[data-settings-content]').forEach(section=>{
      section.hidden=section.dataset.settingsContent!==selected;
    });
    dialog.scrollTop=0;
  }
  function bind(doc=document) {
    doc.addEventListener('click',event=>{
      if(event.target.closest('.skip-link')){event.preventDefault();doc.getElementById('content')?.focus();}
      const tab=event.target.closest('[data-settings-panel]');
      if(tab)settings(tab.dataset.settingsPanel,doc);
      const menu=doc.getElementById('page-tools');
      // Let the browser open dialogs and restore focus to a visible trigger.
      if(menu?.open&&!menu.contains(event.target)&&!doc.querySelector('dialog[open]'))menu.open=false;
    });
    doc.addEventListener('keydown',event=>{
      const tab=event.target.closest('[data-settings-panel]');
      if(tab&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
        event.preventDefault();
        const tabs=[...tab.parentElement.querySelectorAll('[data-settings-panel]')];
        const index=event.key==='Home'?0:event.key==='End'?tabs.length-1:
          (tabs.indexOf(tab)+(event.key==='ArrowLeft'?-1:1)+tabs.length)%tabs.length;
        settings(tabs[index].dataset.settingsPanel,doc);tabs[index].focus();
      }
      const menu=doc.getElementById('page-tools');
      if(event.key==='Escape'&&menu?.open&&!doc.querySelector('dialog[open]')){
        menu.open=false;menu.querySelector('summary').focus();
      }
    });
    // The overflow trigger remains reachable when a dialog closes, including
    // after responsive layout changes while that dialog was open.
    ['settings-dialog','export-dialog'].forEach(id=>doc.getElementById(id)?.addEventListener('close',()=>{
      const menu=doc.getElementById('page-tools');
      if(menu?.open){menu.open=false;menu.querySelector('summary').focus();}
    }));
  }
  const api={settings,bind};
  if(typeof module!=='undefined')module.exports=api;
  else {root.JournalInterface=api;bind();}
})(typeof window!=='undefined'?window:globalThis);
