const test=require('node:test');
const assert=require('node:assert/strict');
const recorder=require('../static/recorder.js');
test('equal timestamp partial fills keep the opening label and combine quantities',()=>{
 const events=[{kind:'add',occurred:100,order_key:'2',quantity:'16',price:'.3448'},{kind:'open',occurred:100,order_key:'2',quantity:'15',price:'.3447'}];
 const groups=recorder.groupEvents(events);
 assert.equal(groups.length,1);assert.equal(groups[0].kind,'open');assert.equal(groups[0].quantity,31);assert.equal(groups[0].count,2);
});
test('sub-dollar entry and cost protection prices retain useful precision',()=>{
 assert.equal(recorder.price('.344799'),'0.344799');assert.equal(recorder.price('.3452'),'0.345200');
});
