const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8').split('function tradesView()')[0];
const journals=[{id:'a',symbol:'ETHUSDT',status:'持仓中',created:1},{id:'b',exchange:'variational',symbol:'DEMO',status:'持仓中',created:2},{id:'c',symbol:'TESTUSDT',status:'已平仓',created:3}];
function run(code){const c=vm.createContext({fixture:{journals,analyses:[],snapshot:{positions:[]}},window:{localStorage:{getItem:()=>null},fetch:()=>{}},JournalClient:{draftStore:()=>({read:()=>null}),createApi:()=>{}}});return vm.runInContext(source+';state=fixture;'+code,c);}
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
