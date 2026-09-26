/* On-demand quote refresh.
 * GitHub Pages cannot call Yahoo Finance directly from browser JavaScript because
 * Yahoo does not expose the required CORS headers. Use several public CORS
 * transports in parallel, with one Yahoo batch request only (never 20 serial calls).
 * Display-only: saved score data is never changed.
 */
(function(){
  const YAHOO_HOSTS=['https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'];
  const PROXIES=[
    {name:'CORS.lol', make:u=>'https://api.cors.lol/?url='+encodeURIComponent(u), mode:'raw'},
    {name:'CorsProxy', make:u=>'https://corsproxy.io/?url='+encodeURIComponent(u), mode:'raw'},
    {name:'AllOrigins', make:u=>'https://api.allorigins.win/raw?url='+encodeURIComponent(u), mode:'raw'},
    {name:'Jina', make:u=>'https://r.jina.ai/'+u, mode:'jina'}
  ];
  const ROUTE_TIMEOUT=4500, BATCH_TIMEOUT=9500;
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
  async function fetchTimed(url,ms=ROUTE_TIMEOUT){
    const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),ms);
    try{return await fetch(url,{cache:'no-store',signal:ctl.signal,headers:{Accept:'application/json,text/plain,*/*'}})}finally{clearTimeout(timer);}
  }
  async function parseResponse(r,mode){
    if(!r.ok)throw new Error('HTTP '+r.status);
    const text=await r.text();
    let t=String(text||'').trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'').trim();
    if(mode==='allorigins'){try{const wrap=JSON.parse(t);t=String(wrap.contents||'');}catch(_){} }
    try{return JSON.parse(t);}catch(_){
      const a=t.indexOf('{'),b=t.lastIndexOf('}');
      if(a>=0&&b>a){try{return JSON.parse(t.slice(a,b+1));}catch(__){}}
    }
    throw new Error('JSON parse failed');
  }
  async function fetchBatch(codes){
    const symbols=codes.map(c=>`${String(c).trim()}.T`).join(',');
    let errors=[];
    for(const host of YAHOO_HOSTS){
      const yahoo=`${host}/v7/finance/spark?symbols=${encodeURIComponent(symbols)}&range=1d&interval=1m&indicators=close&includeTimestamps=false&includePrePost=false&corsDomain=finance.yahoo.com&.tsrc=finance`;
      const jobs=[
        {name:'Yahoo直接',p:fetchTimed(yahoo).then(r=>parseResponse(r,'raw'))},
        ...PROXIES.map(x=>({name:x.name,p:fetchTimed(x.make(yahoo)).then(r=>parseResponse(r,x.mode))}))
      ];
      try{
        const settled=await Promise.any(jobs.map(j=>j.p));
        const map=parseSpark(settled);
        if(map.size)return {map,route:'Yahoo Finance batch'};
        errors.push('Yahooから銘柄データが空でした');
      }catch(e){errors.push(e?.message||String(e));}
    }
    throw new Error(errors.slice(0,2).join(' / ')||'Yahoo Financeから現在値を取得できませんでした');
  }
  function updateStatus(text,kind){const el=document.getElementById('realtimeStatus');if(!el)return;el.textContent=text;el.className='realtime-status '+(kind||'');}
  async function refresh(){
    const btn=document.getElementById('realtimeRefresh'),rows=[...document.querySelectorAll('[data-live-code]')],codes=[...new Set(rows.map(x=>String(x.dataset.liveCode||'').trim()).filter(Boolean))];
    if(!codes.length){updateStatus('表示銘柄がありません。','warn');return;}
    btn.disabled=true;btn.textContent='取得中…';updateStatus(`現在値を取得中… ${codes.length}銘柄（最大約10秒）`,'loading');
    const started=performance.now();
    try{
      const {map}=await Promise.race([fetchBatch(codes),new Promise((_,rej)=>setTimeout(()=>rej(new Error('10秒で応答しませんでした。通信経路を確認してください。')),BATCH_TIMEOUT))]);
      let ok=0; rows.forEach(row=>{
        const code=String(row.dataset.liveCode||'').trim(),q=map.get(`${code}.T`)||map.get(code); if(!q)return; ok++;
        const p=row.querySelector('.live-price'),c=row.querySelector('.live-change'),t=row.querySelector('.live-time');
        if(p)p.textContent=fmt(q.price);
        if(c){c.textContent=pct(q.changePct);c.className=`quote-change live-change ${cls(q.changePct)}`;}
        if(t)t.textContent=q.marketTime?`更新 ${q.marketTime.toLocaleString('ja-JP',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Tokyo'})}`:'更新時刻不明';
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
