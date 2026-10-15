(function(root){
  'use strict';
  // Format the decimal string itself; Number is reserved for chart geometry.
  function format(value,digits=2){
    if(value===null||value===undefined||value==='')return '—';
    const m=String(value).match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
    if(String(value).length>160||!m||!Number.isInteger(digits)||digits<0||digits>30)return '—';
    const exponent=Number(m[4]||0);if(Math.abs(exponent)>100)return '—';
    const fraction=m[3]||'',shift=digits+exponent-fraction.length;
    let n=BigInt(m[2]+fraction);
    if(shift>=0)n*=10n**BigInt(shift);
    else{const divisor=10n**BigInt(-shift);n=(n+divisor/2n)/divisor;}
    const raw=n.toString().padStart(digits+1,'0'),whole=(digits?raw.slice(0,-digits):raw).replace(/\B(?=(\d{3})+(?!\d))/g,',');
    return (m[1]==='-'&&n!==0n?'-':'')+whole+(digits?'.'+raw.slice(-digits):'');
  }
  const api={format};if(typeof module!=='undefined')module.exports=api;else root.JournalMoney=api;
})(typeof window!=='undefined'?window:globalThis);
