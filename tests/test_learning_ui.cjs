const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../static/learning.js');
const empty={days:30,exchange:'all',closed_count:0,reviewed_count:0,structured_count:0,unknown_close_count:0,execution:[],patterns:[],actions:[],unstructured:[]};
test('structured review keeps selected values and escapes all free text',()=>{
 const html=ui.fields({review_keep:'<img src=x>',review_next:'</textarea><script>alert(1)</script>',review_mistakes:'stop,size',review_execution:'no_plan'});
 assert.match(html,/&lt;img src=x&gt;/);assert.doesNotMatch(html,/<script>|<img/);
 assert.match(html,/value="no_plan" selected/);assert.match(html,/value="stop" checked/);
 for(const name of ['review_keep','review_next','review_execution'])assert.equal((html.match(new RegExp('name="'+name+'"','g'))||[]).length,1);
});
test('checkbox form normalization preserves multiple selections and clears all',()=>{
 const original=global.FormData;
 try{
  global.FormData=class extends Map {constructor(){super([['reflection','text'],['_review_mistake','stop']]);}getAll(){return ['stop','size','stop'];}};
  assert.deepEqual(ui.values({}),{reflection:'text',review_mistakes:'size,stop'});
  global.FormData=class extends Map {constructor(){super();}getAll(){return [];}};
  assert.deepEqual(ui.values({}),{review_mistakes:''});
 }finally{global.FormData=original;}
});
test('no samples is an invitation to review, not a good behavior score',()=>{
 const html=ui.reportView(empty);assert.match(html,/先从一笔已平仓交易开始/);assert.match(html,/0 笔已核实平仓/);assert.doesNotMatch(html,/100%|<b>没有问题|表现优秀/);
});
test('patterns state denominators and link to source trades without inferring causality',()=>{
 const html=ui.reportView({...empty,closed_count:8,reviewed_count:5,structured_count:3,unknown_close_count:2,patterns:[{label:'追涨追跌',count:2,journals:[{id:'" onclick="evil',exchange:'okx',symbol:'<img>',closed_at:1}]}]});
 assert.match(html,/8 笔已核实平仓 · 5 笔已写复盘 · 3 笔/);assert.match(html,/2<small> \/ 3 笔/);
 assert.match(html,/2 笔记录缺少已核实平仓时间/);assert.match(html,/当前样本较少/);assert.doesNotMatch(html,/<img>|data-open-journal="" onclick=/);
});
test('reminders and historical practices remain distinguishable and reversible',()=>{
 const a={id:'a',journal_id:'j',symbol:'ETHUSDT',exchange:'binance',instruction:'先算好数量',created:1,archived:0,practices:[{id:'p',journal_id:'later',symbol:'BTCUSDT',outcome:'missed',note:'实际扩大了仓位 <script>',created:2}],eligible:[]};
 const html=ui.reportView({...empty,actions:[a]});assert.match(html,/已实践 1 笔 · 做到 0 笔/);assert.match(html,/撤销这次记录/);assert.match(html,/还没做到/);assert.doesNotMatch(html,/<script>/);assert.match(html,/不受上方时间范围限制/);
 assert.match(ui.reminder({review_learning:{actions:[a]}}),/先算好数量/);
 assert.equal(ui.reminder({review_learning:{actions:[{...a,archived:1}]}}),'');
});
test('changing a review refreshes a selected filtered report and ignores older responses',async()=>{
 const fs=require('node:fs'),vm=require('node:vm'),events={},pending=[],state={review_learning:empty};
 const c=vm.createContext({document:{addEventListener:(name,handler)=>events[name]=handler},module:{exports:{}},Date,console});
 vm.runInContext(fs.readFileSync('static/learning.js','utf8'),c);const mod=c.module.exports;
 mod.init({api:url=>new Promise(resolve=>pending.push({url,resolve})),state:()=>state,render:()=>{},toast:()=>{}});
 events.change({target:{dataset:{learningFilter:'days'},value:'90'}});
 events.change({target:{dataset:{learningFilter:'exchange'},value:'okx'}});
 pending[1].resolve({...empty,closed_count:7});await new Promise(resolve=>setImmediate(resolve));
 pending[0].resolve({...empty,closed_count:99});await new Promise(resolve=>setImmediate(resolve));
 assert.match(mod.render(state),/7 笔已核实平仓/);assert.doesNotMatch(mod.render(state),/99 笔/);
 const updating=mod.changed();assert.match(pending[2].url,/days=90&exchange=okx/);pending[2].resolve({...empty,closed_count:8});await updating;
 assert.match(mod.render(state),/8 笔已核实平仓/);
});
