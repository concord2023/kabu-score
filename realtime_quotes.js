/* On-demand quote refresh for the main stock list.
 * Source: Yahoo Finance's chart/spark endpoint via AllOrigins CORS proxy.
 * This does not use IRBANK and does not alter stored score data.
 */
(function(){
  const PROXY='https://api.allorigins.win/raw?url=';
  const YAHOO='https://query1.finance.yahoo.com/v7/finance/spark';
  const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const fmt=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('ja-JP',{maximumFractionDigits:2});
  const pct=v=>v==null||!Number.isFinite(Number(v))?'—':`${Number(v)>=0?'+':''}${Number(v).toFixed(2)}%`;
  const cls=v=>{const n=Number(v);if(!Number.isFinite(n))return 'zero';const s=n>0?'pos':n<0?'neg':'zero';const a=Math.abs(n);return s+(a>=10?' extreme':a>=3?' strong':'');};
  const jstNow=()=>new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Tokyo'}));
  function marketPhase(){
    const d=jstNow(), day=d.getDay(), mins=d.getHours()*60+d.getMinutes();
    if(day===0||day===6)return '休場';
    if(mins>=540&&mins<690)return '前場';
    if(mins>=750&&mins<930)return '後場';
    return '取引時間外';
  }
  function parseSpark(data){
    const result=data?.spark?.result||[];
    const out=new Map();
    for(const item of result){
      const response=item?.response?.[0]||item?.response||{};
      const meta=response?.meta||{};
      const symbol=meta.symbol||item.symbol;
      if(!symbol)continue;
      const price=Number(meta.regularMarketPrice);
      const prev=Number(meta.previousClose ?? meta.chartPreviousClose);
      const change=Number.isFinite(price)&&Number.isFinite(prev)&&prev!==0?price-prev:null;
      const changePct=change==null?null:change/prev*100;
      out.set(symbol,{symbol,price:Number.isFinite(price)?price:null,previousClose:Number.isFinite(prev)?prev:null,change,changePct,marketTime:meta.regularMarketTime?new Date(Number(meta.regularMarketTime)*1000):null});
    }
    return out;
  }
  async function fetchBatch(codes){
    const symbols=codes.map(c=>`${String(c).trim()}.T`).join(',');
    const target=`${YAHOO}?symbols=${encodeURIComponent(symbols)}&range=1d&interval=1m&indicators=close&includeTimestamps=false&includePrePost=false&corsDomain=finance.yahoo.com`;
    const r=await fetch(PROXY+encodeURIComponent(target),{cache:'no-store'});
    if(!r.ok)throw new Error(`リアルタイム株価取得に失敗（HTTP ${r.status}）`);
    return parseSpark(await r.json());
  }
  function updateStatus(text,kind){
    const el=document.getElementById('realtimeStatus'); if(!el)return;
    el.textContent=text;el.className='realtime-status '+(kind||'');
  }
  async function refresh(){
    const btn=document.getElementById('realtimeRefresh');
    const rows=[...document.querySelectorAll('[data-live-code]')];
    const codes=[...new Set(rows.map(x=>String(x.dataset.liveCode||'').trim()).filter(Boolean))];
    if(!codes.length){updateStatus('表示銘柄がありません。','warn');return;}
    btn.disabled=true;btn.textContent='取得中…';updateStatus(`リアルタイム値を取得中… ${codes.length}銘柄`,'loading');
    try{
      const map=await fetchBatch(codes);
      let ok=0;
      rows.forEach(row=>{
        const q=map.get(`${row.dataset.liveCode}.T`); if(!q)return;
        ok++;
        const p=row.querySelector('.live-price'),c=row.querySelector('.live-change'),t=row.querySelector('.live-time');
        if(p)p.textContent=fmt(q.price);
        if(c){c.textContent=`前日比 ${pct(q.changePct)}`;c.className=`quote-change live-change ${cls(q.changePct)}`;}
        if(t)t.textContent=q.marketTime?`更新 ${q.marketTime.toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`:'更新時刻不明';
        row.classList.add('live-updated');
      });
      const phase=marketPhase();
      updateStatus(`取得 ${ok}/${codes.length}銘柄｜${phase}｜Yahoo Financeデータ｜保存データは変更しません`,'ok');
    }catch(e){
      updateStatus(`取得できませんでした：${e.message}`,'error');
    }finally{btn.disabled=false;btn.textContent='↻ リアルタイム株価を取得';}
  }
  window.addEventListener('load',()=>{
    const btn=document.getElementById('realtimeRefresh'); if(btn)btn.addEventListener('click',refresh);
  });
})();
