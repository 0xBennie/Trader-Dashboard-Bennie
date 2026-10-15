const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8').split('function tradesView()')[0];
const journals=[{id:'a',symbol:'ETHUSDT',status:'持仓中',created:1},{id:'b',exchange:'variational',symbol:'DEMO',status:'持仓中',created:2},{id:'c',symbol:'TESTUSDT',status:'已平仓',created:3}];
function run(code,overrides={}){const c=vm.createContext({JournalLearning:require('../static/learning.js'),fixture:{journals:journals.map(j=>({...j})),analyses:[],snapshot:{positions:[]}},window:{localStorage:{getItem:()=>null},fetch:()=>{}},JournalClient:{draftStore:()=>({read:()=>null}),createApi:()=>{}},...overrides});return vm.runInContext(source+';state=fixture;'+code,c);}
test('exchange and status filters intersect; legacy records default to Binance',()=>{
 assert.equal(run('journalFilters={exchange:"binance",status:"持仓中"};filteredJournals().map(j=>j.id).join(",")'),'a');
 assert.equal(run('journalFilters={exchange:"variational",status:"已平仓"};filteredJournals().length'),0);
 assert.equal(run('filteredJournals().map(j=>j.id).join(",")'),'b,a,c');
});
test('zero results has a reset action, no hidden fallback to a different journal',()=>{
 const html=run('journalFilters={exchange:"variational",status:"已平仓"};journalView()');assert.match(html,/没有符合条件/);assert.doesNotMatch(html,/plan-form/);assert.match(html,/clear-journal-filters/);
});
test('segmented form retains every editable field and opens personal review for closed records',()=>{
 const html=run('selectedId="c";notesForm(fixture.journals[2])');
 for(const field of ['title','symbol','direction','status','opened_at','entry_price','quantity','stop_loss','take_profit','thesis','invalidation','exit_plan','emotion','tags','reflection','protect_trigger_pct','protect_price'])assert.equal((html.match(new RegExp(`name="${field}"`,'g'))||[]).length,1,field);
 assert.match(html,/data-note-section="reflection" open/);assert.match(html,/data-note-section="risk" >/);
 assert.equal((html.match(/<form /g)||[]).length,1);
});

test('search matches symbols, tags and exchange case-insensitively while retaining filters',()=>{
 assert.equal(run('filteredJournals({exchange:"all",status:"all"},"  eth binance  ").map(j=>j.id).join(",")'),'a');
 assert.equal(run('filteredJournals({exchange:"variational",status:"all"},"eth").length'),0);
 assert.equal(run('fixture.journals[0].tags="支撑反弹";filteredJournals(journalFilters,"支撑").length'),1);
 assert.equal(run('filteredJournals(journalFilters,"absent").length'),0);
});
test('trade navigation disables boundaries and escapes user-supplied symbols',()=>{
 const first=run('journalNavigator(filteredJournals(),fixture.journals[1])');
 assert.match(first,/aria-label="上一笔交易" disabled/);assert.doesNotMatch(first,/aria-label="下一笔交易" disabled/);
 const last=run('fixture.journals[2].symbol="<img src=x>";journalNavigator(filteredJournals(),fixture.journals[2])');
 assert.match(last,/aria-label="下一笔交易" disabled/);assert.match(last,/&lt;img src=x&gt;/);assert.doesNotMatch(last,/<img/);
});
test('a failed draft save cannot switch trades or commit picker filters',async()=>{
 const result=await run('selectedId="a";selectJournal("b",{exchange:"variational",status:"all"}).then(()=>[selectedId,journalFilters.exchange].join(","))',{saveNotes:async()=>false,document:{querySelector:()=>({close:()=>{}})}});
 assert.equal(result,'a,all');
});
test('unchanged notes show a disabled saved button and retain fields for editing',()=>{
 const html=run('notesForm(fixture.journals[0])');
 assert.match(html,/type="submit" disabled>已保存/);assert.match(html,/name="thesis"/);
});

test('structured learning fields are optional and retain their saved values',()=>{
 const html=run('fixture.journals[2].review_next="先核对仓位";notesForm(fixture.journals[2])');
 assert.match(html,/optional-review-fields" data-note-section="advanced" >/);
 assert.match(html,/深入复盘（可选） · 已有记录/);assert.match(html,/先核对仓位/);
 assert.doesNotMatch(html,/保存后计入本人复盘/);
 const expanded=run('noteSections.set("c",["reflection","advanced"]);notesForm(fixture.journals[2])');
 assert.match(expanded,/data-note-section="advanced" open/);
});
