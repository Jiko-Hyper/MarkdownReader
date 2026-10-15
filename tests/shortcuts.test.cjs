const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../webui/app.js'),'utf8').replace(/\r\n/g,'\n');
const start = source.indexOf("  document.addEventListener('keydown', e => {\n    const mod");
const end = source.indexOf('\n  });',start)+6;
assert.ok(start>=0 && end>start);
function setup(overlay=false) {
  const calls=[]; let handler;
  const context={document:{addEventListener:(type,fn)=>handler=fn,body:{classList:{contains:()=>false,toggle:()=>calls.push('sidebar')}}},
    $:()=>({classList:{contains:()=>overlay}}),saveDoc:()=>calls.push('save'),newDoc:()=>calls.push('new'),
    switchMode:()=>calls.push('mode'),openFind:()=>calls.push('find'),renderOutline:()=>calls.push('outline'),
    applyFormat:kind=>calls.push(kind),insertTableDialog:()=>calls.push('table')};
  vm.runInNewContext(source.slice(start,end),context);
  return {calls, press:(key,props={})=>{let prevented=false;handler({key,ctrlKey:true,shiftKey:false,preventDefault:()=>prevented=true,...props});return prevented;}};
}
test('web shortcuts route both letter cases and Ctrl/Meta correctly',()=>{
  for(const meta of [false,true]) for(const upper of [false,true]) {
    for(const [key,expected,shiftKey] of [['s','save',false],['n','new',false],['b','sidebar',false],['e','mode',false],['f','find',false],['l','outline',false],['t','table',true],['m','formula_inline',true]]) {
      const h=setup();assert.equal(h.press(upper?key.toUpperCase():key,{ctrlKey:!meta,metaKey:meta,shiftKey}),true);
      assert.deepEqual(h.calls,[expected]);
    }
  }
});
test('modal dialog shortcuts never save or modify the document behind it',()=>{
  const h=setup(true);for(const key of ['s','n','b','e','f','l','m','t']) assert.equal(h.press(key,{shiftKey:['m','t'].includes(key)}),false);
  assert.deepEqual(h.calls,[]);
});
test('composition, AltGr and already handled events do not trigger app actions',()=>{
  for(const props of [{isComposing:true},{altKey:true},{defaultPrevented:true},{ctrlKey:false}]){
    const h=setup();assert.equal(h.press('s',props),false);assert.deepEqual(h.calls,[]);
  }
});
test('unassigned Shift combinations do not fall back to unshifted actions',()=>{
  const h=setup();for(const key of ['s','n','b','e','f','l','v']) assert.equal(h.press(key,{shiftKey:true}),false);
  assert.deepEqual(h.calls,[]);
});
