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
  const fetchAttempts = [];
  const context = {
    console, Date, Number, String, Object, Array, Math, Intl, JSON, Promise, URLSearchParams, AbortController,
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
      fetchAttempts.push(String(url));
      const ok = (body, statusCode=200) => ({ok:statusCode>=200&&statusCode<300,status:statusCode,json:async()=>body});
      if (String(url).includes('query1.finance.yahoo.com/v7/finance/spark')) {
        if (mode === 'live') return ok({spark:{result:[{symbol:'7203.T',response:[{meta:{symbol:'7203.T',regularMarketPrice:3010,previousClose:3000,regularMarketTime:Math.floor(Date.now()/1000)}}]}]}});
        return ok({},404);
      }
      if (String(url).includes('query1.finance.yahoo.com/v8/finance/chart/7203.T')) {
        if (mode === 'chart') return ok({chart:{result:[{meta:{symbol:'7203.T',regularMarketPrice:3025,previousClose:3000,regularMarketTime:Math.floor(Date.now()/1000)}}],error:null}});
        return ok({},404);
      }
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
  if (mode === 'live' && !fetchAttempts.some(u => u.startsWith('https://proxy.cors.dev/https://query1.finance.yahoo.com/'))) throw new Error('CORS relay was not attempted');
  // Emulate the inline page's restore/apply function firing after the button.
  context.window.kabuScoreApplyRealtime();
  if (status.textContent !== finalStatus) throw new Error(`status overwritten: ${finalStatus} -> ${status.textContent}`);
  if (mode==='success' && (!finalStatus.includes('取得 1/1') || cells['.live-price'].textContent!=='123')) throw new Error('success scenario failed: '+finalStatus);
  if (mode==='success' && !finalStatus.includes('スナップショットが古い')) throw new Error('stale snapshot must be flagged clearly: '+finalStatus);
  if (mode==='live' && (!finalStatus.includes('ライブ取得 1/1') || cells['.live-price'].textContent!=='3,010')) throw new Error('live quote scenario failed: '+finalStatus+' / '+cells['.live-price'].textContent);
  if (mode==='chart' && (!finalStatus.includes('ライブ取得 1/1') || cells['.live-price'].textContent!=='3,025')) throw new Error('chart fallback scenario failed: '+finalStatus+' / '+cells['.live-price'].textContent);
  if (mode==='fail' && !finalStatus.includes('リアルタイム取得失敗')) throw new Error('failure scenario failed: '+finalStatus);
  return finalStatus;
}
// --- Cross-midnight restore cases -------------------------------------------
// Regression: the daily ranking file can be generated after midnight JST
// (updated_at = next day 01:xx) while its rows still carry the previous
// market day. Quotes saved for that market day must be restored; only
// genuinely older quotes may be discarded. The market day must come from
// the ranking rows (details.date), never from updated_at.
const CROSSDAY_RANKING = {updated_at:'2026-11-14T01:11:00+09:00',ranking:[{code:'7203',date:'2026-11-13',details:{price:100,change:0,date:'2026-11-13'}}]};

async function runRestore(mode) {
  let loadHandler;
  const snapshotDay = mode === 'stale' ? '2026-11-12' : '2026-11-13';
  const store = () => { const m = new Map(); return { setItem: (k,v)=>m.set(k,v), getItem:k=>m.get(k)||null, removeItem:k=>m.delete(k) }; };
  const localStorage = store();
  const context = {
    console, Date, Number, String, Object, Array, Math, Intl, JSON, Promise, URLSearchParams, AbortController,
    setTimeout, clearTimeout, performance: { now: () => 0 },
    localStorage, sessionStorage: store(),
    window: { addEventListener: (n,fn)=>{ if(n==='load')loadHandler=fn; }, kabuScoreApplyRealtime: () => {} },
    document: {
      getElementById: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      visibilityState: 'visible'
    },
    fetch: async url => {
      const ok = (body, statusCode=200) => ({ok:statusCode>=200&&statusCode<300,status:statusCode,json:async()=>body});
      if (String(url).includes('decision_ranking.json')) return ok(CROSSDAY_RANKING);
      if (String(url).includes('raw.githubusercontent.com') || String(url).includes('realtime_quotes.json')) {
        return ok({updated_at:'2026-11-14T01:16:00+09:00',market_date:snapshotDay,requested:1,fallback:false,source:'Yahoo Finance spark API',quotes:{'7203':{price:456,change:2,change_pct:0.44,market_time:`${snapshotDay}T15:00:00+09:00`,quote_type:'yahoo'}}});
      }
      return ok({},404);
    }
  };
  vm.runInNewContext(source, context, {filename:'realtime_quotes.js'});
  loadHandler();
  await new Promise(r=>setTimeout(r,50));
  const raw = localStorage.getItem('kabuScoreRealtimeQuotesV2');
  const saved = raw ? JSON.parse(raw) : null;
  if (mode === 'crossday') {
    if (!saved || !saved.quotes || !saved.quotes['7203']) throw new Error('crossday restore failed: quote for the ranking market day was discarded');
    if (saved.quotes['7203'].price !== 456) throw new Error('crossday restore failed: unexpected stored price ' + saved.quotes['7203'].price);
    if (saved.market_day !== '2026-11-13') throw new Error('crossday restore failed: market_day must come from ranking rows, got ' + saved.market_day);
  } else if (saved && saved.quotes && Object.keys(saved.quotes).length) {
    throw new Error('stale restore failed: quote older than the ranking market day was stored');
  }
  return saved ? `stored market_day=${saved.market_day} quotes=${Object.keys(saved.quotes||{}).length}` : 'nothing stored';
}

// Same cross-midnight scenario through the inline restore path in index.html
// (applyRealtimeToRows). The helper block is extracted from the real page so
// the test exercises the shipped code, with only formatting helpers stubbed.
async function runInlineRestore(mode) {
  const html = fs.readFileSync('index.html', 'utf8');
  const start = html.indexOf('const REALTIME_STORAGE_KEY=');
  const endMarker = 'window.kabuScoreApplyRealtime=applyRealtimeToRows;';
  const end = html.indexOf(endMarker);
  if (start < 0 || end < 0) throw new Error('inline realtime helpers not found in index.html');
  const snippet = html.slice(start, end + endMarker.length);
  const quoteDay = mode === 'stale' ? '2026-11-12' : '2026-11-13';
  const saved = { saved_at: '2026-11-14T01:20:00.000Z', market_day: quoteDay, source: 'test',
    quotes: { '7203': { price: 3010, change_pct: 0.33, market_time: `${quoteDay}T15:00:00+09:00`, quote_type: 'yahoo_browser_live' } } };
  const store = () => { const m = new Map(); return { setItem: (k,v)=>m.set(k,v), getItem:k=>m.get(k)||null, removeItem:k=>m.delete(k) }; };
  const localStorage = store(), sessionStorage = store();
  localStorage.setItem('kabuScoreRealtimeQuotesV2', JSON.stringify(saved));
  const cells = { '.live-price': { textContent: '100' }, '.live-change': { textContent: '0%', className: '' }, '.live-time': { textContent: '旧値' } };
  const addedClasses = [];
  const row = { dataset: { liveCode: '7203' }, classList: { add: c => addedClasses.push(c) }, querySelector: s => cells[s] || null };
  const context = {
    console, Date, Number, String, Object, Array, Math, Intl, JSON,
    localStorage, sessionStorage,
    allRanking: [{ code: '7203', details: { date: '2026-11-13' } }],
    window: { __rankingUpdatedAt: '2026-11-14T01:11:32+09:00' },
    document: { querySelectorAll: sel => sel === '[data-live-code]' ? [row] : [], getElementById: () => null },
    esc: v => String(v ?? ''),
    fmtPrice: v => v == null ? '—' : Number(v).toLocaleString('ja-JP', { maximumFractionDigits: 2 }),
    pct: v => v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`,
    changeClass: () => 'zero'
  };
  vm.runInNewContext(snippet, context, {filename:'index.html#inline-realtime'});
  const applied = context.window.kabuScoreApplyRealtime();
  if (mode === 'crossday') {
    if (applied !== 1) throw new Error(`inline crossday restore failed: applied=${applied}`);
    if (cells['.live-price'].textContent !== '3,010') throw new Error('inline crossday restore failed: price cell = ' + cells['.live-price'].textContent);
    if (!addedClasses.includes('live-updated')) throw new Error('inline crossday restore failed: row not marked live-updated');
  } else {
    if (applied !== 0) throw new Error(`inline stale restore failed: applied=${applied}`);
    if (cells['.live-price'].textContent !== '100') throw new Error('inline stale restore failed: price cell changed to ' + cells['.live-price'].textContent);
  }
  return `applied=${applied}`;
}

(async()=>{
  console.log('LIVE SPARK:', await run('live'));
  console.log('LIVE CHART FALLBACK:', await run('chart'));
  console.log('SUCCESS:', await run('success'));
  console.log('FAILURE:', await run('fail'));
  console.log('RESTORE CROSSDAY:', await runRestore('crossday'));
  console.log('RESTORE STALE:', await runRestore('stale'));
  console.log('INLINE RESTORE CROSSDAY:', await runInlineRestore('crossday'));
  console.log('INLINE RESTORE STALE:', await runInlineRestore('stale'));
  console.log('REALTIME STATUS PERSISTENCE TEST OK');
})().catch(e=>{console.error(e);process.exit(1);});
