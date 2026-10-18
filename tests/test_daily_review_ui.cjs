const test=require('node:test'),assert=require('node:assert/strict');
const daily=require('../static/daily-review.js');
const report={day:'2026-10-18',scope:'scope',holdings_scope:'historical',generated_at:1,closed:[],recorded:[],pending:[],missing_reason:[],needs_verification:[],holdings:[]};
test('missing key and login failure stay visible without fabricated result',()=>{
 const html=daily.render({review_automation:{latest:report,healthy:true,ai_state:'not_configured',queue:{pending:2},browser:{status:'login_required'}}});
 assert.match(html,/需要登录 Variational/);assert.match(html,/事实摘要照常保存/);assert.match(html,/2 份 AI 复盘待生成/);assert.match(html,/不代表没有交易/);
});
test('source-bounded results escape reason and do not call unknown net zero',()=>{
 const r={...report,closed:[{id:'x',symbol:'TEST',exchange:'variational',thesis:'<script>alert(1)</script>',result:{realized:'-2',asset:'USDC',net:null}}]};
 const html=daily.report(r);assert.match(html,/净额待核对/);assert.match(html,/USDC/);assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);
});
test('compact entry links to persisted daily reports',()=>{
 const html=daily.render({review_automation:{latest:report,queue:{},browser:{},ai_state:'ready'}},true);
 assert.match(html,/data-view="progress"/);assert.match(html,/2026-10-18/);assert.doesNotMatch(html,/连接 AI 服务/);
});
