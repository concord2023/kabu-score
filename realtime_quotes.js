/* Instant quote display from the latest GitHub Actions Yahoo Finance snapshot.
 * Browser-side Yahoo/CORS requests are intentionally removed: they were the
 * source of the repeated "取得できませんでした" failures on GitHub Pages.
 * This button only reads data/realtime_quotes.json and never changes score data.
 */
(function(){
  const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const fmt=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('ja-JP',{maximumFractionDigits:2});
  const pct=v=>v==null||!Number.isFinite(Number(v))?'—':`${Number(v)>=0?'+':''}${Number(v).toFixed(2)}%`;
  const cls=v=>{const n=Number(v);if(!Number.isFinite(n))return 'zero';const s=n>0?'pos':n<0?'neg':'zero';const a=Math.abs(n);return s+(a>=10?' extreme':a>=5?' strong':'');};
  function updateStatus(text,kind){const el=document.getElementById('realtimeStatus');if(!el)return;el.textContent=text;el.className='realtime-status '+(kind||'');}
  async function refresh(){
    const btn=document.getElementById('realtimeRefresh');
    const rows=[...document.querySelectorAll('[data-live-code]')];
    const codes=[...new Set(rows.map(x=>String(x.dataset.liveCode||'').trim()).filter(Boolean))];
    if(!codes.length){updateStatus('表示銘柄がありません。','warn');return;}
    btn.disabled=true; btn.textContent='取得中…';
    const started=performance.now();
    try{
      let payload=null;
      try{
        const r=await fetch('./data/realtime_quotes.json?ts='+Date.now(),{cache:'no-store'});
        if(r.ok) payload=await r.json();
      }catch(_e){}
      // Snapshot may not exist immediately after deployment. In that case,
      // fall back to the already-published daily analysis instead of showing
      // the old hard error. This is explicitly NOT labelled realtime.
      if(!payload){
        try{
          const r=await fetch('./data/stocks.json?ts='+Date.now(),{cache:'no-store'});
          if(r.ok){
            const stocks=await r.json();
            const quotes={};
            for(const code of codes){
              const x=(stocks.stocks||{})[code];
              if(x&&x.price!=null) quotes[code]={price:x.price,change:x.change,change_pct:x.change!=null&&Number(x.price)-Number(x.change)!==0?Number(x.change)/(Number(x.price)-Number(x.change))*100:null,market_time:x.date,quote_type:'daily_close_fallback'};
            }
            payload={updated_at:stocks.updated_at,quotes,fallback:true,source:'保存済み日次終値'};
          }
        }catch(_e){}
      }
      if(!payload)throw new Error('株価データを読み込めませんでした');
      const quotes=payload?.quotes||{};
      let ok=0;
      rows.forEach(row=>{
        const code=String(row.dataset.liveCode||'').trim();
        const q=quotes[`${code}.T`]||quotes[code];
        if(!q)return;
        ok++;
        const p=row.querySelector('.live-price'),c=row.querySelector('.live-change'),t=row.querySelector('.live-time');
        if(p)p.textContent=fmt(q.price);
        if(c){c.textContent=pct(q.change_pct);c.className=`quote-change live-change ${cls(q.change_pct)}`;}
        if(t)t.textContent=q.market_time?`更新 ${String(q.market_time).replace('T',' ').slice(0,16)}`:'更新時刻不明';
        row.classList.add('live-updated');
      });
      if(!ok){
        const stamp=payload.updated_at?String(payload.updated_at).replace('T',' ').slice(0,16):'時刻不明';
        updateStatus(`市場時間外または未取得：保存済みスナップショット ${stamp}`,'warn');
        return;
      }
      const sec=((performance.now()-started)/1000).toFixed(2);
      const stamp=payload.updated_at?String(payload.updated_at).replace('T',' ').slice(0,16):'時刻不明';
      if(payload.fallback){
        updateStatus(`最新リアルタイム値は未取得｜保存済み日次終値 ${ok}/${codes.length}銘柄｜${stamp}`,'warn');
      }else{
        updateStatus(`取得 ${ok}/${codes.length}銘柄｜${sec}秒｜サーバー更新 ${stamp}｜保存スコアは変更しません`,'ok');
      }
    }catch(e){updateStatus(`取得できませんでした：${e.message}`,'error');}
    finally{btn.disabled=false;btn.textContent='↻ リアルタイム株価を取得';}
  }
  window.addEventListener('load',()=>{const btn=document.getElementById('realtimeRefresh');if(btn)btn.addEventListener('click',refresh);});
})();
