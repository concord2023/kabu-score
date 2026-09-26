/* Fast on-demand quote refresh.
 * Browser -> Yahoo Finance needs a CORS bridge.  Use one batch request first;
 * never make 20 serial requests.  The result is display-only and never saved.
 */
(function(){
  const YAHOO_HOSTS=['https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'];
  const PROXY_TARGETS=[
    {name:'CORS.lol', make:u=>'https://api.cors.lol/?url='+encodeURIComponent(u), mode:'raw'},
    {name:'AllOrigins', make:u=>'https://api.allorigins.win/get?url='+encodeURIComponent(u), mode:'allorigins'},
    {name:'Jina', make:u=>'https://r.jina.ai/'+u, mode:'jina'}
  ];
  const TIMEOUT=1800;
  const BATCH_TIMEOUT=6500;
  const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const fmt=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('ja-JP',{maximumFractionDigits:2});
  const pct=v=>v==null||!Number.isFinite(Number(v))?'—':`${Number(v)>=0?'+':''}${Number(v).toFixed(2)}%`;
  const cls=v=>{const n=Number(v);if(!Number.isFinite(n))return 'zero';const s=n>0?'pos':n<0?'neg':'zero';const a=Math.abs(n);return s+(a>=10?' extreme':a>=5?' strong':'');};
  const jstNow=()=>new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Tokyo'}));
  function marketPhase(){const d=jstNow(),day=d.getDay(),mins=d.getHours()*60+d.getMinutes();if(day===0||day===6)return '休場';if(mins>=540&&mins<690)return '前場';if(mins>=750&&mins<930)return '後場';return '取引時間外';}
  function parseSpark(data){
    const result=data?.spark?.result||[]; const out=new Map();
    for(const item of result){
      const response=item?.response?.[0]||item?.response||{}; const meta=response?.meta||{}; const symbol=meta.symbol||item.symbol; if(!symbol)continue;
      const price=Number(meta.regularMarketPrice), prev=Number(meta.previousClose??meta.chartPreviousClose);
      if(!Number.isFinite(price))continue;
      const change=Number.isFinite(prev)&&prev!==0?price-prev:null;
      out.set(symbol,{symbol,price,previousClose:Number.isFinite(prev)?prev:null,change,changePct:change==null?null:change/prev*100,marketTime:meta.regularMarketTime?new Date(Number(meta.regularMarketTime)*1000):null});
    }
    return out;
  }
  function parseChart(data,symbol){
    const r=data?.chart?.result?.[0],m=r?.meta||{}; const price=Number(m.regularMarketPrice),prev=Number(m.previousClose??m.chartPreviousClose);
    if(!Number.isFinite(price))return null;
    const change=Number.isFinite(prev)&&prev!==0?price-prev:null;
    return {symbol:m.symbol||symbol,price,previousClose:Number.isFinite(prev)?prev:null,change,changePct:change==null?null:change/prev*100,marketTime:m.regularMarketTime?new Date(Number(m.regularMarketTime)*1000):null};
  }
  async function fetchTimed(url,ms=TIMEOUT){
    const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),ms);
    try{return await fetch(url,{cache:'no-store',signal:ctl.signal,headers:{'Accept':'application/json,text/plain,*/*'}})}finally{clearTimeout(timer);}
  }
  async function parseResponse(r,mode){
    if(!r.ok)throw new Error('HTTP '+r.status);
    if(mode==='allorigins'){
      const wrap=await r.json();
      if(!wrap||typeof wrap.contents!=='string')throw new Error('AllOrigins response invalid');
      return parseText(wrap.contents);
    }
    const text=await r.text();
    return parseText(text);
  }
  function parseText(text){
    let t=String(text||'').trim();
    // Jina may add a short markdown/code fence around JSON.
    t=t.replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'').trim();
    try{return JSON.parse(t);}catch(_){
      const a=t.indexOf('{'),b=t.lastIndexOf('}');
      if(a>=0&&b>a){try{return JSON.parse(t.slice(a,b+1));}catch(__){}}
    }
    throw new Error('JSON parse failed');
  }
  async function tryTarget(target,mode){
    const r=await fetchTimed(target,TIMEOUT); return parseResponse(r,mode);
  }
  async function fetchBatch(codes){
    const symbols=codes.map(c=>`${String(c).trim()}.T`).join(',');
    let last='';
    // One Yahoo endpoint + several transport paths, all raced in parallel.
    for(const host of YAHOO_HOSTS){
      const yahoo=`${host}/v7/finance/spark?symbols=${encodeURIComponent(symbols)}&range=1d&interval=1m&indicators=close&includeTimestamps=false&includePrePost=false`;
      const jobs=[
        {name:'Yahoo直接',p:fetchTimed(yahoo,TIMEOUT).then(r=>parseResponse(r,'raw'))},
        ...PROXY_TARGETS.map(x=>({name:x.name,p:tryTarget(x.make(yahoo),x.mode)}))
      ];
      try{
        const winner=await Promise.any(jobs.map(x=>x.p));
        const map=parseSpark(winner);
        if(map.size)return {map,route:'Yahoo Finance batch'};
        last='Yahooから銘柄データが返りませんでした';
      }catch(e){last=e?.message||String(e);}
    }
    // Do not fan out to one request per stock here. A 20-stock fallback was the
    // main source of the old 'spinning/no response' feeling. If the single
    // batch route fails, return promptly with a clear error.
    throw new Error(last||'Yahoo Financeから現在値を取得できませんでした');
  }
  function updateStatus(text,kind){const el=document.getElementById('realtimeStatus');if(!el)return;el.textContent=text;el.className='realtime-status '+(kind||'');}
  async function refresh(){
    const btn=document.getElementById('realtimeRefresh'),rows=[...document.querySelectorAll('[data-live-code]')],codes=[...new Set(rows.map(x=>String(x.dataset.liveCode||'').trim()).filter(Boolean))];
    if(!codes.length){updateStatus('表示銘柄がありません。','warn');return;}
    btn.disabled=true;btn.textContent='取得中…';updateStatus(`現在値を取得中… ${codes.length}銘柄（最大約6秒）`,'loading');
    const started=performance.now();
    try{
      const {map,route}=await Promise.race([
        fetchBatch(codes),
        new Promise((_,rej)=>setTimeout(()=>rej(new Error('取得がタイムアウトしました（6秒）')),BATCH_TIMEOUT))
      ]);
      let ok=0; rows.forEach(row=>{
        const code=String(row.dataset.liveCode||'').trim(),q=map.get(`${code}.T`)||map.get(code)||map.get(`${code}`); if(!q)return; ok++;
        const p=row.querySelector('.live-price'),c=row.querySelector('.live-change'),t=row.querySelector('.live-time');
        if(p)p.textContent=fmt(q.price);
        if(c){c.textContent=pct(q.changePct);c.className=`quote-change live-change ${cls(q.changePct)}`;}
        if(t)t.textContent=q.marketTime?`更新 ${q.marketTime.toLocaleString('ja-JP',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}`:'更新時刻不明';
        row.classList.add('live-updated');
      });
      if(!ok)throw new Error('現在値を1銘柄も受け取れませんでした');
      const sec=((performance.now()-started)/1000).toFixed(1);
      updateStatus(`取得 ${ok}/${codes.length}銘柄｜${sec}秒｜${marketPhase()}｜Yahoo Finance｜保存データは変更しません`,'ok');
    }catch(e){updateStatus(`取得できませんでした：${e.message}`,'error');}
    finally{btn.disabled=false;btn.textContent='↻ リアルタイム株価を取得';}
  }
  window.addEventListener('load',()=>{const btn=document.getElementById('realtimeRefresh');if(btn)btn.addEventListener('click',refresh);});
})();
