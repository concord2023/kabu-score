/*
 * Stable quote refresh for GitHub Pages.
 *
 * The previously reliable design is server-side GitHub Actions -> Yahoo Finance
 * -> data/realtime_quotes.json.  GitHub Pages cannot reliably call Yahoo from
 * the browser because the request may be blocked by CORS.  The button therefore
 * fetches the newest published snapshot, validates its market date against the
 * current daily ranking, and never presents daily fallback values as live quotes.
 */
(function () {
  'use strict';

  const RAW_SNAPSHOT = 'https://raw.githubusercontent.com/concord2023/kabu-score/main/data/realtime_quotes.json';
  const STORAGE_KEYS = ['kabuScoreRealtimeQuotesV2', 'kabuScoreRealtimeQuotes'];
  const fmt = v => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('ja-JP', { maximumFractionDigits: 2 });
  const pct = v => v == null || !Number.isFinite(Number(v)) ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
  const cls = v => {
    const n = Number(v);
    if (!Number.isFinite(n)) return 'zero';
    const s = n > 0 ? 'pos' : n < 0 ? 'neg' : 'zero';
    const a = Math.abs(n);
    return s + (a >= 10 ? ' extreme' : a >= 5 ? ' strong' : '');
  };
  const jstDay = value => {
    if (!value) return null;
    const s = String(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit'
    }).formatToParts(d);
    const p = Object.fromEntries(parts.map(x => [x.type, x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  };
  const formatJst = value => {
    if (!value) return '時刻不明';
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return `${value}（時刻なし）`;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).replace('T', ' ').slice(0, 16);
    return d.toLocaleString('ja-JP', {
      timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false
    });
  };

  function updateStatus(text, kind) {
    const el = document.getElementById('realtimeStatus');
    if (!el) return;
    el.textContent = text;
    el.className = 'realtime-status ' + (kind || '');
    // Mark the button's final result as authoritative. The inline page script
    // also applies saved quotes and used to overwrite this message immediately.
    el.dataset.statusOwner = 'realtime-button';
    el.dataset.statusUpdatedAt = String(Date.now());
  }

  async function fetchJson(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function fetchRanking() {
    try {
      const d = await fetchJson('./data/decision_ranking.json?ts=' + Date.now());
      return d && Array.isArray(d.ranking) ? d : null;
    } catch (_) {
      return null;
    }
  }

  async function fetchSnapshot() {
    const errors = [];
    for (const url of [RAW_SNAPSHOT + '?ts=' + Date.now(), './data/realtime_quotes.json?ts=' + Date.now()]) {
      try {
        const d = await fetchJson(url);
        if (d && d.quotes && typeof d.quotes === 'object') return d;
        throw new Error('株価データ形式が不正です');
      } catch (e) {
        errors.push(`${e.message}`);
      }
    }
    throw new Error(errors.join(' / ') || '最新スナップショットを取得できません');
  }

  // Fetch genuinely current quotes on demand, as the previously working
  // version did. The published JSON snapshot is only a fallback: re-reading a
  // static file cannot itself refresh market prices.
  async function fetchLiveQuotes(codes) {
    const symbols = codes.map(c => c.endsWith('.T') ? c : `${c}.T`).join(',');
    const url = 'https://query1.finance.yahoo.com/v7/finance/spark?' + new URLSearchParams({
      symbols, range: '1d', interval: '1m', indicators: 'close',
      includeTimestamps: 'false', includePrePost: 'false', corsDomain: 'finance.yahoo.com'
    });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    let response;
    try {
      response = await fetch(url, { cache: 'no-store', mode: 'cors', signal: controller.signal });
    } finally {
      clearTimeout(timeout);
    }
    if (!response.ok) throw new Error(`Yahoo Finance HTTP ${response.status}`);
    const data = await response.json();
    const out = {};
    for (const item of (data?.spark?.result || [])) {
      const meta = (item?.response || [])[0]?.meta || {};
      const symbol = meta.symbol || item.symbol;
      const price = Number(meta.regularMarketPrice);
      if (!symbol || !Number.isFinite(price) || price <= 0) continue;
      const prev = Number(meta.previousClose ?? meta.chartPreviousClose);
      const change = Number.isFinite(prev) && prev > 0 ? price - prev : null;
      const stamp = Number(meta.regularMarketTime);
      out[String(symbol).replace(/\.T$/, '')] = {
        symbol, price, previous_close: Number.isFinite(prev) ? prev : null,
        change, change_pct: change != null && prev > 0 ? change / prev * 100 : null,
        market_time: stamp ? new Date(stamp * 1000).toISOString() : null,
        quote_type: 'yahoo_browser_live'
      };
    }
    if (!Object.keys(out).length) throw new Error('Yahoo Financeから有効な株価が返りませんでした');
    return out;
  }

  function dailyQuoteFor(code, ranking) {
    const row = (ranking?.ranking || []).find(x => String(x.code) === String(code));
    if (!row) return null;
    const d = row.details || {};
    const price = d.price ?? row.price;
    if (price == null) return null;
    const change = d.change ?? row.change ?? null;
    return {
      price, change, change_pct: d.change_pct ?? row.change_pct ?? change,
      market_time: d.date || row.date || ranking.updated_at,
      quote_type: 'daily_ranking_fallback'
    };
  }

  function isActualQuote(q) {
    const type = String(q?.quote_type || '').toLowerCase();
    return q?.price != null && !type.includes('fallback') && (type === 'yahoo' || type.includes('yahoo_'));
  }

  function storeQuotes(quotes, marketDay, payload) {
    const actual = {};
    for (const [rawCode, q] of Object.entries(quotes || {})) {
      if (!isActualQuote(q)) continue;
      const qDay = jstDay(q.market_time) || jstDay(payload?.market_date);
      if (marketDay && (!qDay || qDay < marketDay)) continue;
      actual[String(rawCode).replace(/\.T$/, '')] = { ...q };
    }
    const saved = {
      saved_at: new Date().toISOString(), market_day: marketDay || payload?.market_date || null,
      quotes: actual, source: payload?.source || 'GitHub Actions quote snapshot',
      snapshot_updated_at: payload?.updated_at || null
    };
    for (const storage of [localStorage, sessionStorage]) {
      for (const key of STORAGE_KEYS) {
        try { storage.setItem(key, JSON.stringify(saved)); } catch (_) { /* storage can be disabled */ }
      }
    }
    return Object.keys(actual).length;
  }

  function applyActualQuotes(rows, payload, dailyDay) {
    const quotes = payload?.quotes || {};
    let applied = 0;
    rows.forEach(row => {
      const code = String(row.dataset.liveCode || '').trim();
      const q = quotes[code] || quotes[`${code}.T`];
      if (!isActualQuote(q)) return;
      const qDay = jstDay(q.market_time) || jstDay(payload?.market_date);
      // Never allow a quote from an older market day to replace today's ranking.
      if (dailyDay && (!qDay || qDay < dailyDay)) return;
      const p = row.querySelector('.live-price');
      const c = row.querySelector('.live-change');
      const t = row.querySelector('.live-time');
      if (p) p.textContent = fmt(q.price);
      if (c) { c.textContent = pct(q.change_pct); c.className = `quote-change live-change ${cls(q.change_pct)}`; }
      if (t) t.textContent = `更新 ${formatJst(q.market_time)}`;
      row.classList.add('live-updated');
      applied++;
    });
    return applied;
  }

  async function refresh() {
    const btn = document.getElementById('realtimeRefresh');
    const rows = [...document.querySelectorAll('[data-live-code]')];
    const codes = [...new Set(rows.map(x => String(x.dataset.liveCode || '').trim()).filter(Boolean))];
    if (!codes.length) { updateStatus('表示銘柄がありません。', 'warn'); return; }
    if (btn) { btn.disabled = true; btn.textContent = '取得中…'; }
    const started = performance.now();
    try {
      const ranking = await fetchRanking();
      const rowDays = (ranking?.ranking || []).map(x => jstDay(x?.details?.date || x?.date)).filter(Boolean).sort();
      const dailyDay = rowDays.length ? rowDays[rowDays.length - 1] : jstDay(ranking?.updated_at);

      // First attempt a fresh Yahoo request on the button click. This is the
      // actual live path; the static snapshot below is only a backup.
      let liveQuotes = {};
      let liveError = null;
      try { liveQuotes = await fetchLiveQuotes(codes); } catch (e) { liveError = e; }
      if (Object.keys(liveQuotes).length) {
        const livePayload = {
          updated_at: new Date().toISOString(), market_date: dailyDay,
          source: 'Yahoo Finance browser live', requested: codes.length,
          quotes: liveQuotes, fallback: false
        };
        const appliedLive = applyActualQuotes(rows, livePayload, dailyDay);
        storeQuotes(liveQuotes, dailyDay, livePayload);
        if (appliedLive && typeof window.kabuScoreApplyRealtime === 'function') window.kabuScoreApplyRealtime();
        if (appliedLive === codes.length) {
          const elapsed = ((performance.now() - started) / 1000).toFixed(1);
          updateStatus(`ライブ取得 ${appliedLive}/${codes.length}銘柄｜Yahoo Finance｜${elapsed}秒｜日本時間`, 'ok');
          return;
        }
      }

      let payload = null;
      let snapshotError = null;
      try { payload = await fetchSnapshot(); } catch (e) { snapshotError = e; }
      // If Yahoo returned some symbols but the snapshot is unavailable, retain
      // and display those fresh quotes rather than discarding them.
      if (!payload && Object.keys(liveQuotes).length) {
        payload = { updated_at: new Date().toISOString(), market_date: dailyDay,
          requested: codes.length, quotes: liveQuotes, source: 'Yahoo Finance browser live', fallback: false };
      }

      if (!payload && !Object.keys(liveQuotes).length) {
        if (!ranking) throw snapshotError || new Error('株価データも日次データも取得できませんでした');
        updateStatus(`リアルタイム取得失敗｜日次データを維持（${dailyDay || '日付不明'}）｜${snapshotError?.message || 'スナップショットなし'}`, 'error');
        return;
      }

      const payloadDay = jstDay(payload.market_date) || Object.values(payload.quotes || {})
        .map(q => jstDay(q?.market_time)).filter(Boolean).sort().slice(-1)[0] || null;
      if (!Object.keys(liveQuotes).length && dailyDay && payloadDay && payloadDay < dailyDay) {
        updateStatus(`古い株価スナップショットのため適用しません｜日次データ ${dailyDay} を維持｜スナップショット ${payloadDay}`, 'warn');
        return;
      }

      if (Object.keys(liveQuotes).length) {
        payload = { ...payload, quotes: { ...(payload.quotes || {}), ...liveQuotes }, source: 'Yahoo Finance browser live + snapshot' };
      }
      const applied = applyActualQuotes(rows, payload, dailyDay);
      storeQuotes(payload.quotes, dailyDay, payload);
      if (applied && typeof window.kabuScoreApplyRealtime === 'function') window.kabuScoreApplyRealtime();

      const actualCount = Object.values(payload.quotes || {}).filter(isActualQuote).length;
      const stamp = formatJst(payload.updated_at);
      const elapsed = ((performance.now() - started) / 1000).toFixed(1);
      const requested = Number(payload.requested) || codes.length;
      const fallback = Boolean(payload.fallback) || actualCount < requested || applied < codes.length || Boolean(liveError);
      const ageMinutes = payload.updated_at ? Math.max(0, Math.floor((Date.now() - new Date(payload.updated_at).getTime()) / 60000)) : null;
      const stale = ageMinutes == null || !Number.isFinite(ageMinutes) || ageMinutes >= 10;
      const freshness = stale ? `｜⚠ スナップショットが古い（${ageMinutes == null || !Number.isFinite(ageMinutes) ? '更新時刻不明' : `${ageMinutes}分前`}）` : `｜${ageMinutes}分前`;
      if (applied > 0) {
        updateStatus(`${fallback ? '一部取得' : '取得'} ${applied}/${codes.length}銘柄｜サーバー更新 ${stamp}（日本時間）${freshness}｜${elapsed}秒${fallback ? `｜未取得分は日次データ${liveError ? `（直接取得: ${liveError.message}）` : ''}` : ''}`, stale ? 'error' : fallback ? 'warn' : 'ok');
      } else {
        updateStatus(`リアルタイム値なし｜日次データを維持｜スナップショット更新 ${stamp}（日本時間）｜Yahoo取得 ${actualCount}/${requested}${freshness}`, 'warn');
      }
    } catch (e) {
      updateStatus(`取得できませんでした：${e?.message || e}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '↻ リアルタイム株価を取得'; }
    }
  }

  async function restoreSnapshot() {
    // Restore values after a tab switch without touching the status message.
    try {
      const ranking = await fetchRanking();
      const dailyDay = jstDay(ranking?.updated_at);
      const payload = await fetchSnapshot();
      if (dailyDay && jstDay(payload.market_date) && jstDay(payload.market_date) < dailyDay) return;
      storeQuotes(payload.quotes, dailyDay, payload);
      if (typeof window.kabuScoreApplyRealtime === 'function') window.kabuScoreApplyRealtime();
    } catch (_) { /* keep current table and status if network is unavailable */ }
  }

  window.addEventListener('load', () => {
    const btn = document.getElementById('realtimeRefresh');
    if (btn) btn.addEventListener('click', refresh);
    restoreSnapshot();
  });
  window.addEventListener('pageshow', () => { if (!document.getElementById('realtimeStatus')?.dataset.statusOwner) restoreSnapshot(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !document.getElementById('realtimeStatus')?.dataset.statusOwner) restoreSnapshot();
  });
})();
