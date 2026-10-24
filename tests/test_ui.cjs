// Exercise the actual embedded UI with controlled network responses.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const html = fs.readFileSync(require('path').join(__dirname, '../console/index.html'), 'utf8');
let script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
script = script.replace('})();', `globalThis.testUI = {mergeNow, renderNow, pollNow, control,
  invalidate: () => ++pollEpoch, busy: () => pollBusy};})();`);
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {style:{}, value:'', textContent:'', className:'', disabled:false,
    appendChild(){}, insertBefore(){}, getBoundingClientRect(){return {left:0,width:100}}});
  return elements.get(id);
};
let resolvePoll, nowRequests=0;
const context = {console, Promise, Date, Object, Math, parseInt, parseFloat,
  document:{hidden:false, getElementById:element, addEventListener(){}, createElement(){return {}}},
  setInterval(){return 1}, clearInterval(){}, setTimeout(){return 1},
  fetch(path){
    if (path==='/api/now') {nowRequests++; return new Promise(resolve => {resolvePoll=resolve;});}
    return new Promise(()=>{}); // suppress automatic bootstrap
  }
};
vm.createContext(context);vm.runInContext(script,context);
const ui=context.testUI;
const good={running:true,state:'PLAYING',has_media:true,has_media_info:true,uri:'song1',title:'Song',artist:'Artist',album:'Album',has_position:true,position:45,duration:180,has_volume:true,volume:70};
ui.renderNow(ui.mergeNow(good));
assert.equal(element('vol').value,70);
assert.equal(element('tpos').textContent,'0:45');
const partial=ui.mergeNow({...good,has_volume:false,volume:0,has_media_info:false,has_media:false,title:'',has_position:false,position:0,duration:0});
ui.renderNow(partial);
assert.equal(element('vol').value,70,'failed GetVolume must not render zero');
assert.equal(element('title').textContent,'Song','failed media query must not clear title');
assert.equal(element('tpos').textContent,'0:45','failed position query must not reset progress');
assert.equal(ui.mergeNow({...good,error:'timeout',state:'STOPPED'}),null);
element('vol').onpointerdown();element('vol').value=83;element('vol').oninput();
ui.renderNow(ui.mergeNow(good));
assert.equal(element('vol').value,83,'poll must not interrupt drag');
element('vol').onpointerup();
ui.renderNow(ui.mergeNow({...good,state:'STOPPED',has_media:false}));
assert.equal(element('icoPause').style.display,'none','stop must reset play button');
const switched=ui.mergeNow({...good,uri:'song2',title:'Second song',has_position:false,position:120});
assert.equal(switched.position,0,'new track must not inherit old progress');
ui.renderNow(ui.mergeNow({...good,state:'PAUSED_PLAYBACK'}));
assert.equal(element('title').textContent,'Song');
async function checkNetwork(){
  ui.pollNow();ui.pollNow();
  assert.equal(nowRequests,1,'only one poll may be in flight');
  ui.invalidate();
  resolvePoll({ok:true,json:()=>Promise.resolve({...good,title:'Stale response'})});
  for(let i=0;i<8;i++) await Promise.resolve();
  assert.equal(element('title').textContent,'Song','pre-control response must be discarded');
  assert.equal(ui.busy(),false);
  console.log('UI snapshot, drag, stopped-state, single-flight and stale-response checks passed');
}
checkNetwork().catch(e=>{console.error(e);process.exitCode=1;});
