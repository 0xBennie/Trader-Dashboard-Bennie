const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8').split('function render()')[0];
function emptyPage(name){
 const context=vm.createContext({window:{localStorage:{getItem:()=>null},fetch:()=>{throw Error('Empty page should not submit anything');}},JournalClient:{draftStore:()=>({}),createApi:()=>{}}});
 return vm.runInContext(source+`;state={journals:[],analyses:[],snapshot:{}};${name}()`,context);
}
test('a fresh journal directs the user to create or connect, without a phantom record',()=>{
 const html=emptyPage('journalView');
 assert.match(html,/记录你的第一笔交易/);assert.match(html,/data-action="new"/);assert.match(html,/data-action="settings"/);
 assert.doesNotMatch(html,/plan-form|clear-journal-filters/);
});
test('AI review has no empty selection or generation action before a journal exists',()=>{
 const html=emptyPage('reviewView');
 assert.match(html,/先留下一笔交易记录/);assert.match(html,/data-action="new"/);
 assert.doesNotMatch(html,/review-journal|data-action="analyze"|undefined/);
});
