const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('realtime_quotes.js', 'utf8');

async function run(mode) {
  let loadHandler, pageShowHandler, visibilityHandler, clickHandler;
  const status = { textContent: '', className: '', dataset: {} };
  const button = { disabled: false, textContent: '↻ リアルタイム株価を取得', addEventListener: (name, fn) => { if (name === 'click') clickHandler = fn; } };
  const cells = {
    '.live-price': { textContent: '100' },
    '.live-change': { textContent: '0%', className: '' },
    '.live-time': { textContent: '旧値' }
  };
  const row = { dataset: { liveCode: '7203' }, classList: { add() {} }, querySelector: s => cells[s] || null };
  const store = () => { const m = new Map(); return { setItem: (k,v)=>m.set(k,v), getItem:k=>m.get(k)||null, removeItem:k=>m.delete(k) }; };
  const context = {
    console, Date, Number, String, Object, Array, Math, Intl, JSON, Promise,
    setTimeout, clearTimeout, performance: { now: (()=>{let n=0;return ()=>++n;})() },
    localStorage: store(), sessionStorage: store(),
    window: { addEventListener: (n,fn)=>{ if(n==='load')loadHandler=fn; if(n==='pageshow')pageShowHandler=fn; }, kabuScoreApplyRealtime: () => { if(!status.dataset.statusOwner) status.textContent='保存済みリアルタイム値 1銘柄'; } },
    document: {
      getElementById: id => id==='realtimeStatus'?status:id==='realtimeRefresh'?button:null,
      querySelectorAll: sel => sel==='[data-live-code]'?[row]:[],
      addEventListener: (n,fn)=>{ if(n==='visibilitychange')visibilityHandler=fn; },
      visibilityState: 'visible'
    },
    fetch: async url => {
      const ok = (body, statusCode=200) => ({ok:statusCode>=200&&statusCode<300,status:statusCode,json:async()=>body});
      if (String(url).includes('decision_ranking.json')) return ok({updated_at:'2026-10-16T17:10:00+09:00',ranking:[{code:'7203',date:'2026-10-16',details:{price:100,change:0,date:'2026-10-16'}}]});
      if (String(url).includes('raw.githubusercontent.com')) {
        if (mode==='fail') return ok({},503);
        return ok({updated_at:'2026-10-16T17:10:00+09:00',market_date:'2026-10-16',requested:1,fallback:false,source:'Yahoo Finance spark API',quotes:{'7203':{price:123,change:2,change_pct:1.65,market_time:'2026-10-16T15:00:00+09:00',quote_type:'yahoo'}}});
      }
      if (String(url).includes('realtime_quotes.json')) return ok({}, mode==='fail'?503:200);
      return ok({},404);
    }
  };
  vm.runInNewContext(source, context, {filename:'realtime_quotes.js'});
  loadHandler();
  await new Promise(r=>setTimeout(r,10));
  await clickHandler();
  const finalStatus = status.textContent;
  // Emulate the inline page's restore/apply function firing after the button.
  context.window.kabuScoreApplyRealtime();
  if (status.textContent !== finalStatus) throw new Error(`status overwritten: ${finalStatus} -> ${status.textContent}`);
  if (mode==='success' && (!finalStatus.includes('取得 1/1') || cells['.live-price'].textContent!=='123')) throw new Error('success scenario failed: '+finalStatus);
  if (mode==='fail' && !finalStatus.includes('リアルタイム取得失敗')) throw new Error('failure scenario failed: '+finalStatus);
  return finalStatus;
}
(async()=>{
  console.log('SUCCESS:', await run('success'));
  console.log('FAILURE:', await run('fail'));
  console.log('REALTIME STATUS PERSISTENCE TEST OK');
})().catch(e=>{console.error(e);process.exit(1);});
