(function(root) {
  'use strict';
  function createApi(fetcher, getToken, setToken, onUnauthorized = () => {}) {
    return async function request(path, body) {
      const send = () => fetcher(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Journal-Token':getToken()}, body:JSON.stringify(body)});
      let response = await send(), result = await response.json();
      if(response.status === 401) onUnauthorized();
      // The server rejects this code before any mutation. Never retry arbitrary errors.
      if(body !== undefined && response.status === 403 && result.code === 'SESSION_EXPIRED') {
        const fresh = await fetcher('/api/state'), session = await fresh.json();
        if(fresh.status === 401) onUnauthorized();
        if(!fresh.ok || !session.csrf) throw new Error('暂时无法恢复连接，草稿仍保留在本机。');
        setToken(session.csrf);
        response = await send(); result = await response.json();
      }
      if(!response.ok) {const error=new Error(result.error || '操作失败，草稿仍保留在本机。');error.status=response.status;error.code=result.code;throw error;}
      return result;
    };
  }
  function changedFields(base, values) {
    return Object.fromEntries(Object.entries(values).filter(([key,value]) => String(value??'') !== String(base[key]??'')));
  }
  function draftStore(storage) {
    const key = id => 'trade-journal:draft:v1:'+id;
    return {
      read(id) { try {const d=JSON.parse(storage.getItem(key(id)));return d&&d.base&&d.values?d:null;}catch{return null;} },
      write(id,base,values) {try {storage.setItem(key(id),JSON.stringify({base,values,savedAt:Date.now()}));return true;}catch{return false;}},
      remove(id) {try {storage.removeItem(key(id));}catch{}},
      restore(current,draft) {return {...current,...changedFields(draft.base,draft.values)};}
    };
  }
  const api={createApi,changedFields,draftStore};
  if(typeof module!=='undefined')module.exports=api;else root.JournalClient=api;
})(typeof window!=='undefined'?window:globalThis);
