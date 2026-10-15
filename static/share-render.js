(function(root){
  'use strict';
  const W=900, C={ink:'#202124',muted:'#6c727a',line:'#e3e7e5',green:'#32734a',red:'#bf5660',blue:'#3159d9',empty:'#edf0ee'};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=v=>Number(v).toLocaleString('en-US',{maximumFractionDigits:2});
  const money=v=>(Number(v)>0?'+':'')+number(v);
  const price=v=>Number(v).toLocaleString('en-US',{maximumFractionDigits:8});
  const direction=v=>({LONG:'做多',SHORT:'做空',UNKNOWN:'方向待核对'}[v]||'方向待核对');
  const coverageText=d=>({full:'全天已采集',partial:'仅部分时段',unknown:'未采集',future:'未来日期'}[d.coverage]);
  function dayText(d,r){
    const pnl=d.coverage==='full'||d.coverage==='partial'?(r.show_amounts&&d.net!==undefined?money(d.net)+' '+r.asset:({1:'净盈利','-1':'净亏损',0:'净额持平'}[d.sign]||'金额待核对')):'盈亏未知';
    return `${d.date} · ${coverageText(d)} · ${d.reviews} 次复盘 · ${d.closed} 笔平仓 · ${pnl}`;
  }
  function cellColor(d,metric){
    if(d.coverage==='future')return '#f7f8f7';
    if(metric==='reviews'&&d.reviews)return ['#c4e4c8','#8bc697','#519762','#24613b'][Math.min(d.reviews-1,3)];
    if(metric==='pnl'&&d.sign)return d.sign>0?'#519762':'#c86c73';
    return d.coverage==='unknown'?'#fff':C.empty;
  }
  function geometry(r){
    const year=r.mode==='year',offset=r.days.length?(new Date(r.days[0].date+'T00:00:00Z').getUTCDay()+6)%7:0;
    const columns=year?Math.ceil((offset+r.days.length)/7):7, step=year?760/columns:106;
    const gridY=year?552:550, gridX=year?94:81, size=year?Math.min(10.5,step-3):38;
    const cells=r.days.map((d,i)=>({d,x:gridX+(year?Math.floor((offset+i)/7):(offset+i)%7)*step,y:gridY+(year?(offset+i)%7:Math.floor((offset+i)/7))*(year?17:56),w:size,h:size}));
    return {year,cells,activityH:year?290:Math.ceil((offset+r.days.length)/7)*56+140,gridY,gridX,step};
  }
  function height(r){const g=geometry(r);return r.mode==='trade'?1020:450+g.activityH+220+(r.note?160:48)+(r.details?.length?104+r.details.length*49:0)+138;}
  function draw(canvas,r){
    const H=height(r),g=geometry(r);canvas.width=W*2;canvas.height=H*2;
    const x=canvas.getContext('2d');x.scale(2,2);
    const font=(s=20,b=400)=>`${b} ${s}px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif`;
    function rect(a,b,w,h,fill='#fff',border=false,radius=12){x.beginPath();x.roundRect(a,b,w,h,radius);x.fillStyle=fill;x.fill();if(border){x.strokeStyle=C.line;x.lineWidth=1;x.stroke();}}
    function txt(t,a,b,s=20,color=C.ink,weight=400,max){x.font=font(s,weight);if(max)while(x.measureText(String(t)).width>max&&s>12)x.font=font(--s,weight);x.fillStyle=color;x.fillText(String(t),a,b);}
    function wrap(t,a,b,width,s=20,color=C.ink,line=32){let row='',y=b;for(const char of String(t)){x.font=font(s);if(char==='\n'||x.measureText(row+char).width>width){txt(row,a,y,s,color);y+=line;row=char==='\n'?'':char;}else row+=char;}if(row)txt(row,a,y,s,color);return y;}
    function line(a,b,w){x.strokeStyle=C.line;x.lineWidth=1;x.beginPath();x.moveTo(a,b);x.lineTo(a+w,b);x.stroke();}
    function fact(label,value,a,b,small='',max=340){txt(label,a,b,17,C.muted);txt(value,a,b+40,32,C.ink,600,max);if(small)txt(small,a,b+67,14,C.muted,400,344);}
    rect(0,0,W,H,'#fff',false,0);
    const wash=x.createLinearGradient(0,0,W,310);wash.addColorStop(0,'#f6faf5');wash.addColorStop(1,'#fff');rect(0,0,W,310,wash,false,0);
    rect(40,36,44,44,C.green,false,22);txt(Array.from(r.nickname||'T')[0].toUpperCase(),54,67,25,'#fff',500);
    txt(r.nickname,98,57,21,C.ink,600,470);txt('trading journal',98,80,15,C.muted);txt(r.venue,710,60,18,C.green,500,150);
    txt(r.mode==='trade'?r.trade.symbol:r.period,40,205,r.mode==='trade'?80:r.mode==='month'?100:132,C.ink,700,760);
    txt(r.mode==='trade'?`${direction(r.trade.direction)} · ${r.trade.status}`:`${r.nickname} 的${r.title}`,44,260,30,C.ink,600,790);
    txt(r.mode==='trade'?r.trade.source:`${r.start} — ${r.through}${r.mode==='year'?(r.through.endsWith('12-31')?' · 全年':' · 年度至今'):''}`,44,291,17,C.muted);
    if(r.mode==='trade'){
      const t=r.trade;rect(40,324,820,168,'#fff',true);
      fact('平仓盈亏 · 未扣费',!r.show_amounts?'••••':t.gross_pnl===undefined?'尚未对账':money(t.gross_pnl),64,357,r.show_amounts&&t.gross_pnl!==undefined?r.asset:'金额与价格默认隐藏');
      fact('成交手续费',!r.show_amounts?'••••':t.fees_usdt===undefined?'尚未对账':number(t.fees_usdt),470,357,'资金费未分配到单笔');
      rect(40,510,820,188,'#fff',true);fact('入场均价',r.show_amounts&&t.entry_price!==undefined?price(t.entry_price):'—',64,546);fact('离场均价',r.show_amounts&&t.exit_price!==undefined?price(t.exit_price):'—',470,546);
      txt('开仓 '+(t.opened||'时间待核对'),64,661,17,C.muted);txt('平仓 '+(t.closed||'尚未确认'),470,661,17,C.muted);
      if(t.quantity!==undefined)txt('累计入场数量 '+price(t.quantity),64,685,15,C.muted,400,750);
      if(r.note){rect(40,722,820,156,'#f6f9f5');txt('给下次的自己',64,756,20,C.green,600);wrap(r.note,64,791,766,19,C.ink,27);}
      else {txt('每一笔交易，都值得留下一次复盘。',64,777,22,C.muted);}
      wrap(r.method,44,925,808,15,C.muted,25);txt('Trader Dashboard  /  交易手记',44,991,16,C.ink,500);return {height:H,cells:[]};
    }
    rect(40,320,398,112,'#fff',true);rect(450,320,410,112,'#fff',true);
    fact('已实现净额',!r.show_amounts?'••••':r.net===undefined?'尚未对账':money(r.net),60,347,r.show_amounts&&r.net!==undefined?r.asset:'以已采集账单为准',205);
    txt('完整平仓',284,347,16,C.muted);txt(r.summary.closed+' 笔',284,387,30,C.ink,600);
    txt('复盘完成',470,347,16,C.muted);txt(r.summary.reviewed+' / '+r.summary.closed,470,387,30,C.ink,600);
    txt('复盘天数',704,347,16,C.muted);txt(r.summary.review_days+' 天',704,387,30,C.ink,600);
    rect(470,405,192,5,'#edf0ee',false,2);if(r.summary.closed&&r.summary.reviewed)rect(470,405,192*r.summary.reviewed/r.summary.closed,5,'#519762',false,2);
    rect(40,450,820,g.activityH,'#fff',true);txt(r.mode==='year'?'一年的交易足迹':'这个月的交易足迹',62,488,23,C.ink,600);txt(r.metric==='reviews'?'复盘活动':'每日盈亏',741,488,16,C.green,500);
    if(g.year){for(let m=1;m<=12;m+=2){const i=r.days.findIndex(d=>Number(d.date.slice(5,7))===m);if(i>=0)txt(m+'月',g.cells[i].x,532,13,C.muted);}['一','三','五','日'].forEach((s,i)=>txt(s,64,g.gridY+10+[0,2,4,6][i]*17,12,C.muted));}
    else ['一','二','三','四','五','六','日'].forEach((s,i)=>txt(s,g.gridX+i*g.step+10,530,16,C.muted));
    g.cells.forEach(c=>{rect(c.x,c.y,c.w,c.h,cellColor(c.d,r.metric),false,g.year?2:7);if(c.d.coverage==='unknown'){x.strokeStyle='#dfe4df';x.strokeRect(c.x+.5,c.y+.5,c.w-1,c.h-1);x.beginPath();x.moveTo(c.x+2,c.y+c.h-2);x.lineTo(c.x+c.w-2,c.y+2);x.stroke();}if(c.d.coverage==='partial'){x.fillStyle='#aa8a52';x.fillRect(c.x,c.y+c.h-2,c.w,2);}if(!g.year)txt(c.d.date.slice(-2),c.x+7,c.y+25,16,c.d.coverage==='future'?'#aeb3ae':C.ink);});
    const bottom=450+g.activityH;txt('斜线 未采集   ·   底线 部分时段',62,bottom-37,14,C.muted);
    txt(r.metric==='reviews'?'绿色越深，完成复盘越多':'绿 盈利  /  红 亏损  /  灰 持平',450,bottom-37,14,C.muted);
    const coverage=r.coverage.length?r.coverage.map(c=>c.start+' — '+c.end).join('；'):'尚无完整账单覆盖';txt('账单覆盖 '+coverage,62,bottom-14,12,C.muted,400,775);
    const by=bottom+18;rect(40,by,820,196,'#fff',true);txt(r.mode==='year'?'月度已实现净额':'每日已实现净额',62,by+35,21,C.ink,600);txt(r.show_amounts?'单位 '+r.asset:'隐藏金额，仅表示正负',632,by+35,14,C.muted);
    const bx=72,bw=756,baseline=by+112,step=bw/Math.max(r.bars.length,1),max=Math.max(1,...r.bars.map(b=>Math.abs(Number(b.net||0))));line(bx,baseline,bw);
    r.bars.forEach((b,i)=>{const h=b.known?(r.show_amounts?Math.abs(Number(b.net||0))/max*47:(b.sign?28:0)):0;rect(bx+i*step+2,baseline-(b.sign>0?h:0),Math.max(3,step-6),h||2,b.known?(b.sign>0?'#8fc99a':b.sign<0?'#dc969b':'#c9ceca'):'#edf0ee',false,1);if(r.mode==='year'||i%5===0)txt(r.mode==='year'?Number(b.key.slice(-2))+'月':b.key.slice(-2),bx+i*step+2,by+174,12,C.muted);});
    let y=by+214;if(r.note){rect(40,y,820,144,'#f6f9f5');txt('给下次的自己',62,y+34,20,C.green,600);wrap(r.note,62,y+69,774,19,C.ink,26);y+=160;}else y+=48;
    if(r.details.length){rect(40,y,820,88+r.details.length*49,'#fff',true);txt(`最近 ${r.details.length} 笔 / 共 ${r.summary.closed} 笔完整平仓`,62,y+36,21,C.ink,600);if(r.show_amounts)txt('平仓盈亏 · 未扣费',632,y+56,12,C.muted);r.details.forEach((t,i)=>{const yy=y+80+i*49;txt(t.symbol,62,yy,18,C.ink,500);txt(direction(t.direction),265,yy,16,C.muted);txt(t.closed||'待核对',375,yy,16,C.muted);txt(r.show_amounts&&t.gross_pnl!==undefined?money(t.gross_pnl)+' '+r.asset:'金额隐藏',632,yy,16,C.muted,400,200);});y+=104+r.details.length*49;}
    line(40,y,820);txt('Trader Dashboard  /  交易手记',44,y+32,16,C.ink,500);txt('记录 · 复盘 · 改进',640,y+32,16,C.muted);
    wrap(r.source+' · 香港时间 · '+r.method,44,y+62,812,13,C.muted,22);
    txt('完整平仓排除 '+r.summary.excluded+' 笔不完整轮次；复盘天数按首次完成日期计。',44,y+110,12,C.muted);return {height:H,cells:g.cells};
  }
  function mount(container,report,onDay){
    container.innerHTML='<div class="share-canvas-wrap"><canvas role="img" aria-label="交易分享报告"></canvas><div class="share-cell-layer"></div></div><button type="button" class="share-zoom-button">放大阅读报告</button><p class="share-day-description" role="status">选择格子查看日期记录；可用键盘方向键移动。</p><details class="share-text-report"><summary>阅读文字版报告</summary><div></div></details><dialog class="share-zoom"><header><span>报告原尺寸 · 可滚动查看</span><button type="button">关闭</button></header><div></div></dialog>';
    const canvas=container.querySelector('canvas'),layout=draw(canvas,report),layer=container.querySelector('.share-cell-layer');
    const zoom=container.querySelector('.share-zoom');
    container.querySelector('.share-zoom-button').addEventListener('click',()=>{const img=document.createElement('img');img.src=canvas.toDataURL('image/png');img.alt='当前分享报告的放大版本';zoom.querySelector('div').replaceChildren(img);zoom.showModal();});
    zoom.querySelector('button').addEventListener('click',()=>zoom.close());
    if(matchMedia('(max-width:700px)').matches)container.querySelector('.share-text-report').open=true;
    layer.innerHTML=layout.cells.map((c,i)=>`<button type="button" class="share-day" data-day="${i}" tabindex="${i===0?'0':'-1'}" style="left:${c.x/W*100}%;top:${c.y/layout.height*100}%;width:${c.w/W*100}%;height:${c.h/layout.height*100}%" aria-label="${esc(dayText(c.d,report))}"></button>`).join('');
    function select(button){if(!button)return;layer.querySelectorAll('button').forEach(b=>b.tabIndex=-1);button.tabIndex=0;const day=report.days[Number(button.dataset.day)];container.querySelector('.share-day-description').textContent=dayText(day,report);if(onDay)onDay(day);}
    layer.addEventListener('focusin',e=>select(e.target.closest('button')));layer.addEventListener('click',e=>select(e.target.closest('button')));
    layer.addEventListener('keydown',e=>{const b=e.target.closest('button');if(!b)return;const horizontal=report.mode==='year'?7:1,vertical=report.mode==='year'?1:7,delta={ArrowLeft:-horizontal,ArrowRight:horizontal,ArrowUp:-vertical,ArrowDown:vertical}[e.key];if(delta){e.preventDefault();const i=Math.max(0,Math.min(report.days.length-1,Number(b.dataset.day)+delta));layer.querySelector(`[data-day="${i}"]`)?.focus();}});
    const summary=report.mode==='trade'?`${report.trade.symbol} ${direction(report.trade.direction)} ${report.trade.status}；${report.trade.source}`:`${report.start} — ${report.through}；完整平仓 ${report.summary.closed} 笔，本人复盘 ${report.summary.reviewed} 笔，复盘 ${report.summary.review_days} 天；已实现净额 ${report.show_amounts?(report.net===undefined?'待对账':money(report.net)+' '+report.asset):'已隐藏'}`;
    const tradeText=report.trade?`<p>开仓 ${esc(report.trade.opened||'待核对')} · 平仓 ${esc(report.trade.closed||'待核对')}</p>${[['entry_price','入场均价'],['exit_price','离场均价'],['quantity','累计入场数量'],['gross_pnl','平仓盈亏（未扣费）'],['fees_usdt','手续费（USDT）']].filter(([k])=>report.trade[k]!==undefined).map(([k,label])=>`<p>${label}：${esc(report.trade[k])}</p>`).join('')}`:'';
    container.querySelector('.share-text-report div').innerHTML=`<p>${esc(report.nickname)} · ${esc(report.venue)}</p><p>${esc(summary)}</p>${tradeText}<p>${esc(report.note)}</p><p>${esc(report.method)}</p>${report.details?.map(t=>`<p>${esc(t.symbol)} · ${direction(t.direction)} · ${esc(t.closed)}${t.gross_pnl!==undefined?' · 平仓盈亏（未扣费） '+esc(t.gross_pnl)+' '+esc(report.asset):''}</p>`).join('')||''}${report.days.length?`<label>按日期阅读<select class="share-accessible-day"><option value="">选择日期</option>${report.days.filter(d=>d.coverage!=='future').map(d=>`<option value="${d.date}">${esc(dayText(d,report))}</option>`).join('')}</select></label>`:''}`;
    container.querySelector('.share-accessible-day')?.addEventListener('change',e=>{const d=report.days.find(d=>d.date===e.target.value);if(d){container.querySelector('.share-day-description').textContent=dayText(d,report);if(onDay)onDay(d);}});
    if(!report.days.length)container.querySelector('.share-day-description').hidden=true;
    return canvas;
  }
  async function download(canvas,report){
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('图片导出失败，请重试。')),'image/png'));
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='trading-'+report.mode+'-'+report.period+'.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
  }
  const api={draw,mount,download,geometry,height,dayText,cellColor};if(typeof module!=='undefined')module.exports=api;else root.JournalShareRender=api;
})(typeof window!=='undefined'?window:globalThis);
