/* Fast on-demand quote refresh. Tries Yahoo directly first, then short-timeout relays. */
(function(){
  const YAHOO_HOSTS=['https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'];
  const PROXIES=[u=>'https://api.allorigins.win/raw?url='+encodeURIComponent(u),u=>'https://corsproxy.io/?url='+encodeURIComponent(u)];
  const JINA=u=>'https://r.jina.ai/'+u;
  const TIMEOUT=1800;
  const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const fmt=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('ja-JP',{maximumFractionDigits:2});
  const pct=v=>v==null||!Number.isFinite(Number(v))?'—':`${Number(v)>=0?'+':''}${Number(v).toFixed(2)}%`;
  const cls=v=>{const n=Number(v);if(!Number.isFinite(n))return 'zero';const s=n>0?'pos':n<0?'neg':'zero';const a=Math.abs(n);return s+(a>=10?' extreme':a>=5?' strong':'');};
  const jstNow=()=>new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Tokyo'}));
  function marketPhase(){const d=jstNow(),day=d.getDay(),mins=d.getHours()*60+d.getMinutes();if(day===0||day===6)return '休場';if(mins>=540&&mins<690)return '前場';if(mins>=750&&mins<930)return '後場';return '取引時間外';}
  function parseSpark(data){const result=data?.spark?.result||[];const out=new Map();for(const item of result){const response=item?.response?.[0]||item?.response||{};const meta=response?.meta||{};const symbol=meta.symbol||item.symbol;if(!symbol)continue;const price=Number(meta.regularMarketPrice);const prev=Number(meta.previousClose??meta.chartPreviousClose);const change=Number.isFinite(price)&&Number.isFinite(prev)&&prev!==0?price-prev:null;out.set(symbol,{symbol,price:Number.isFinite(price)?price:null,previousClose:Number.isFinite(prev)?prev:null,change,changePct:change==null?null:change/prev*100,marketTime:meta.regularMarketTime?new Date(Number(meta.regularMarketTime)*1000):null});}return out;}
  function parseChart(data,symbol){const r=data?.chart?.result?.[0],m=r?.meta||{};const price=Number(m.regularMarketPrice),prev=Number(m.previousClose??m.chartPreviousClose);if(!Number.isFinite(price))return null;const change=Number.isFinite(prev)&&prev!==0?price-prev:null;return {symbol:m.symbol||symbol,price,previousClose:Number.isFinite(prev)?prev:null,change,changePct:change==null?null:change/prev*100,marketTime:m.regularMarketTime?new Date(Number(m.regularMarketTime)*1000):null};}
  async function fetchTimed(url,init={},ms=TIMEOUT){const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),ms);try{return await fetch(url,{...init,cache:'no-store',signal:ctl.signal});}finally{clearTimeout(timer);}}
  async function getJson(url){
    const targets=[url,PROXIES[0](url),PROXIES[1](url)];
    const jobs=targets.map(async target=>{const r=await fetchTimed(target,{},TIMEOUT);if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});
    try{return await Promise.any(jobs);}catch(e){throw new Error('Yahoo取得タイムアウト/接続失敗');}
  }
  async function fetchBatch(codes){
    const symbols=codes.map(c=>`${String(c).trim()}.T`).join(',');
    const targets=[];
    for(const host of YAHOO_HOSTS){
      const target=`${host}/v7/finance/spark?symbols=${encodeURIComponent(symbols)}&range=1d&interval=1m&indicators=close&includeTimestamps=false&includePrePost=false`;
      targets.push(target);
      for(const proxy of PROXIES) targets.push(proxy(target));
    }
    const jobs=targets.map(async target=>{
      const r=await fetchTimed(target,{},TIMEOUT);
      if(!r.ok)throw new Error('HTTP '+r.status);
      const map=parseSpark(await r.json());
      if(!map.size)throw new Error('empty');
      return map;
    });
    try{
      return await Promise.any(jobs);
    }catch(e){
      throw new Error(`Yahoo Financeの一括取得に失敗（約${(TIMEOUT/1000).toFixed(1)}秒で打ち切り）`);
    }
  }
  function updateStatus(text,kind){const el=document.getElementById('realtimeStatus');if(!el)return;el.textContent=text;el.className='realtime-status '+(kind||'');}
  async function refresh(){const btn=document.getElementById('realtimeRefresh'),rows=[...document.querySelectorAll('[data-live-code]')],codes=[...new Set(rows.map(x=>String(x.dataset.liveCode||'').trim()).filter(Boolean))];if(!codes.length){updateStatus('表示銘柄がありません。','warn');return;}btn.disabled=true;btn.textContent='取得中…';updateStatus(`リアルタイム値を取得中… ${codes.length}銘柄（最大約2秒）`,'loading');try{const map=await fetchBatch(codes);let ok=0;rows.forEach(row=>{const q=map.get(`${row.dataset.liveCode}.T`)||map.get(String(row.dataset.liveCode));if(!q)return;ok++;const p=row.querySelector('.live-price'),c=row.querySelector('.live-change'),t=row.querySelector('.live-time');if(p)p.textContent=fmt(q.price);if(c){c.textContent=pct(q.changePct);c.className=`quote-change live-change ${cls(q.changePct)}`;}if(t)t.textContent=q.marketTime?`更新 ${q.marketTime.toLocaleString('ja-JP',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}`:'更新時刻不明';row.classList.add('live-updated');});if(!ok)throw new Error('Yahoo Financeから表示銘柄の値を受け取れませんでした');updateStatus(`取得 ${ok}/${codes.length}銘柄｜${marketPhase()}｜Yahoo Financeデータ｜保存データは変更しません`,'ok');}catch(e){updateStatus(`取得できませんでした：${e.message}`,'error');}finally{btn.disabled=false;btn.textContent='↻ リアルタイム株価を取得';}}
  window.addEventListener('load',()=>{const btn=document.getElementById('realtimeRefresh');if(btn)btn.addEventListener('click',refresh);});
})();
