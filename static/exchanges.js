(function(root){
  'use strict';
  const names={binance:'Binance',okx:'OKX',bybit:'Bybit',variational:'Variational'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label=j=>names[typeof j==='string'?j:(j?.exchange||'binance')]||'交易所';
  const account=(state,exchange)=>exchange==='binance'?state:(state?.exchanges?.[exchange]||{snapshot:{},trades:[],orders:[],totals:{},trade_context:{},sync:{}});
  const status=(a,now=Date.now())=>!a?.configured?'未连接':!a.enabled?'已暂停':a.sync?.status==='running'?'正在同步':a.sync?.status==='retrying'?'连接异常 · 自动重试':a.sync?.status==='partial'?'部分数据待核对':a.sync?.status==='complete'&&now-(a.snapshot?.captured_at||0)<=90000?'自动记录中':a.snapshot?.captured_at?'快照已过期':'等待首次同步';
  function picker(selected){return `<div class="exchange-selector"><label>账户<select id="ledger-exchange">${['binance','okx','bybit'].map(x=>`<option value="${x}" ${x===selected?'selected':''}>${names[x]} · USDT 永续</option>`).join('')}</select></label><span class="helper">每个交易所独立统计</span></div>`;}
  function fields(exchange){return `<details class="exchange-connect" id="connect-${exchange}"><summary>${names[exchange]} · USDT 永续 <span id="${exchange}-badge" class="badge">未连接</span></summary><form data-exchange-form="${exchange}"><p class="helper">${exchange==='okx'?'使用 OKX 主账户或子账户的只读 API，需填写创建密钥时设置的 Passphrase。':'支持 Bybit 统一交易账户（UTA）的系统生成 HMAC API Key。'}</p><label>API Key<input name="key" type="password" required autocomplete="off" spellcheck="false" maxlength="512"></label><label>API Secret<input name="secret" type="password" required autocomplete="off" spellcheck="false" maxlength="512"></label>${exchange==='okx'?'<label>API Passphrase<input name="passphrase" type="password" required autocomplete="off" spellcheck="false" maxlength="512"></label>':''}<p class="helper">请选择只读权限。密钥仅保存在服务器，不回传浏览器或发送给 AI。支持同一账户换密钥，一个交易所绑定一个账户。</p><button class="button primary" type="submit">验证并保存连接</button></form><p class="setting-status" id="${exchange}-status" role="status"></p><div class="exchange-controls" id="${exchange}-controls" hidden><label>补读成交范围<select id="${exchange}-days"><option value="7">近 7 天</option><option value="30" selected>近 30 天</option><option value="90">近 90 天</option></select></label><button type="button" class="button" data-action="exchange-sync" data-exchange="${exchange}">立即同步</button><button type="button" class="button quiet" data-action="exchange-toggle" data-exchange="${exchange}">暂停自动记录</button></div><p class="helper">默认每 60 秒读取持仓、成交、账单与挂单，首次补读近 30 天。连接验证成功后，首次完整同步仍需等待。${exchange==='bybit'?'不支持经典账户、RSA 密钥及独立地区站点。':'当前使用 OKX 全球站接口。'}此连接不会创建或修改订单。</p></details>`;}
  function show(exchanges){
    const node=document.getElementById('additional-exchanges');
    if(!node.children.length)node.innerHTML=['okx','bybit'].map(fields).join('');
    for(const x of ['okx','bybit']){
      const a=exchanges?.[x]||{};
      document.getElementById(x+'-badge').textContent=status(a);
      document.getElementById(x+'-status').textContent=a.sync?.error||(a.configured?'凭证已验证。'+(a.snapshot?.captured_at?'已保存账户快照。':'等待首次完整同步。'):'填写后将验证账户身份，再开始自动记录。');
      document.getElementById(x+'-controls').hidden=!a.configured;
      node.querySelector(`[data-action="exchange-toggle"][data-exchange="${x}"]`).textContent=a.enabled===false?'恢复自动记录':'暂停自动记录';
    }
  }
  function init({api,refresh,state,toast}){
    document.addEventListener('submit',async event=>{
      const form=event.target;if(!form.dataset.exchangeForm)return;event.preventDefault();
      const x=form.dataset.exchangeForm,button=form.querySelector('button'),output=document.getElementById(x+'-status');
      button.disabled=true;output.textContent='正在验证账户身份…';
      try{await api(`/api/exchanges/${x}/credentials`,Object.fromEntries(new FormData(form)));form.reset();await refresh(false);show(state().exchanges);toast(names[x]+' 凭证已验证并保存，后台开始同步。');}
      catch(error){output.textContent=error.message;}
      finally{button.disabled=false;}
    });
    document.addEventListener('click',async event=>{
      const b=event.target.closest('[data-exchange][data-action]');if(!b||!['exchange-sync','exchange-toggle'].includes(b.dataset.action))return;
      const x=b.dataset.exchange;b.disabled=true;
      try{
        if(b.dataset.action==='exchange-sync')await api(`/api/exchanges/${x}/sync`,{days:Number(document.getElementById(x+'-days')?.value||30)});
        else await api(`/api/exchanges/${x}/enabled`,{enabled:state().exchanges[x].enabled===false});
        await refresh(false);if(document.getElementById('settings-dialog').open)show(state().exchanges);
        toast(b.dataset.action==='exchange-sync'?names[x]+' 开始同步，完成后页面自动更新。':'自动记录设置已更新。');
      }catch(error){toast(error.message);}finally{b.disabled=false;}
    });
  }
  const api={names,label,account,status,picker,fields,show,init};
  if(typeof module!=='undefined')module.exports=api;else root.JournalExchanges=api;
})(typeof window!=='undefined'?window:globalThis);
