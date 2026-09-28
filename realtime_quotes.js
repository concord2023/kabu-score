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
      // The quote snapshot is supplementary data. Never let an older snapshot
      // overwrite a newer Daily stock update. This was the cause of the
      // 9/25 "time travel" regression after pressing the button.
      let ranking=null;
      try{
        const rr=await fetch('./data/decision_ranking.json?ts='+Date.now(),{cache:'no-store'});
        if(rr.ok) ranking=await rr.json();
      }catch(_e){}
      const dailyDays=(ranking?.ranking||[]).map(x=>(x?.details?.date||x?.date||'').slice(0,10)).filter(Boolean);
      // decision_ranking.updated_at is the authoritative date when individual
      // rows do not carry a date. Never let a quote from an older market day
      // replace the currently published Daily stock update.
      const rankingUpdatedDay=ranking?.updated_at?String(ranking.updated_at).slice(0,10):null;
      const dailyDay=dailyDays.length?dailyDays.sort().slice(-1)[0]:rankingUpdatedDay;

      let payload=null;
      try{
        const r=await fetch('./data/realtime_quotes.json?ts='+Date.now(),{cache:'no-store'});
        if(r.ok) payload=await r.json();
      }catch(_e){}

      const payloadDay=payload?.market_date?String(payload.market_date).slice(0,10):(
        Object.values(payload?.quotes||{}).map(q=>q?.market_time?String(q.market_time).slice(0,10):'').filter(Boolean).sort().slice(-1)[0]||null
      );
      const snapshotStale=!!(dailyDay&&payloadDay&&payloadDay<dailyDay);
      const rankingRows=ranking?.ranking||[];

      // If the snapshot is missing or stale, use the SAME daily ranking that
      // drives the page, not data/stocks.json. This keeps the date consistent.
      if(!payload || snapshotStale){
        const quotes={};
        for(const code of codes){
          const x=rankingRows.find(v=>String(v.code)===code);
          const d=x?.details||{};
          if(x&&d.price!=null) quotes[code]={price:d.price,change:d.change,change_pct:d.change,market_time:d.date||x.date||ranking.updated_at,quote_type:'daily_ranking_fallback'};
        }
        if(Object.keys(quotes).length){
          payload={updated_at:ranking.updated_at,quotes,fallback:true,stale_snapshot:snapshotStale,source:'現在表示中のDaily stock update',errors:payload?.errors||[]};
        }
      }
      if(!payload)throw new Error('株価データを読み込めませんでした');

      const quotes=payload?.quotes||{};
      let ok=0, applied=0, usedFallback=false;
      rows.forEach(row=>{
        const code=String(row.dataset.liveCode||'').trim();
        const q=quotes[`${code}.T`]||quotes[code];
        if(!q)return;
        ok++;
        // Never apply a quote whose own timestamp predates the daily data.
        const qStamp=q.market_time?new Date(q.market_time):null;
        const qDay=qStamp&&!Number.isNaN(qStamp.getTime())?qStamp.toLocaleDateString('en-CA',{timeZone:'Asia/Tokyo'}):null;
        let use=q;
        if(dailyDay&&(!qDay || qDay<dailyDay)){
          const daily=rankingRows.find(v=>String(v.code)===code);
          const dd=daily?.details||{};
          if(daily&&dd.price!=null){
            use={price:dd.price,change:dd.change,change_pct:dd.change_pct,market_time:dd.date||daily.date||ranking?.updated_at,quote_type:'daily_ranking_fallback'};
            usedFallback=true;
          }else{
            // Do not show an unverified old quote. Keep the row unchanged.
            return;
          }
        }
        const p=row.querySelector('.live-price'),c=row.querySelector('.live-change'),t=row.querySelector('.live-time');
        if(p)p.textContent=fmt(use.price);
        if(c){c.textContent=pct(use.change_pct);c.className=`quote-change live-change ${cls(use.change_pct)}`;}
        if(t)t.textContent=use.market_time?`更新 ${String(use.market_time).replace('T',' ').slice(0,16)}`:'更新時刻不明';
        row.classList.add('live-updated');
        applied++;
      });
      if(!applied){
        updateStatus(`古い株価スナップショットは適用せず、日次データ ${dailyDay||'—'} を維持しました。`,'warn');
        return;
      }
      const sec=((performance.now()-started)/1000).toFixed(2);
      const stamp=payload.updated_at?String(payload.updated_at).replace('T',' ').slice(0,16):'時刻不明';
      if(payload.fallback||snapshotStale||usedFallback){
        updateStatus(`リアルタイム未取得｜日次データ ${applied}/${codes.length}銘柄を維持｜${stamp}`,'warn');
      }else{
        updateStatus(`取得 ${applied}/${codes.length}銘柄｜${sec}秒｜サーバー更新 ${stamp}｜日次スコアは変更しません`,'ok');
      }
    }catch(e){updateStatus(`取得できませんでした：${e.message}`,'error');}
    finally{btn.disabled=false;btn.textContent='↻ リアルタイム株価を取得';}
  }
  window.addEventListener('load',()=>{const btn=document.getElementById('realtimeRefresh');if(btn)btn.addEventListener('click',refresh);});
})();
