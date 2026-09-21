"""Prime-only direct scan for the user's strict "トラさん" bottom condition.

The scan is deliberately separate from the daily update.  IRBANK's screening
API is used only as a request-saving prefilter; every stock that reaches the
price stage is checked against the exact multi-timeframe rule locally:

  weekly RSI(14) <= 30
  AND monthly MACD golden cross OR pre-golden histogram contraction.

The final output contains only stocks that satisfy that rule.  No bottom score,
ranking, or credit-supply score is used for the result.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import update_data as ud

OUT = Path('data/bottom_prime_candidates.json')
REQUEST_BUDGET = 900
PRICE_TARGET = 800  # enough for the existing strict 30-month MACD calculation
MAX_PRICE_CANDIDATES = 400  # 2 price pages each leaves a safe request reserve
SCREEN_LIMIT = 100


def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))


def budget_guard(extra=1):
    return int(getattr(ud, '_request_count', 0)) + extra <= REQUEST_BUDGET


def api_get(path, params=None, retries=1):
    if not budget_guard(retries):
        raise RuntimeError('REQUEST_BUDGET_REACHED')
    return ud.get(path, params or {}, retries=retries)


def load_prime_universe():
    companies = {}
    cursor = None
    pages = 0
    while True:
        params = {'market': 'Prime', 'limit': 500}
        if cursor:
            params['cursor'] = cursor
        data = api_get('/securities', params, retries=1)
        pages += 1
        for item in data.get('securities') or []:
            code = str(item.get('code') or '').strip().upper()
            if code:
                companies[code] = {
                    'code': code,
                    'name': item.get('name') or code,
                    'market': item.get('market') or 'Prime',
                    'industry': item.get('industry'),
                }
        cursor = data.get('next_cursor')
        if not cursor:
            break
        if pages >= 10:
            raise RuntimeError('Prime銘柄マスターのページ数が想定上限を超えました。安全停止しました。')
    return companies, pages


def screening_prefilter(prime_codes):
    """Broad, request-efficient prefilter before exact weekly/monthly checks.

    A daily RSI ceiling plus non-positive daily MACD histogram is used only to
    keep the exact price-history stage under the request budget.  The final
    displayed result is still based solely on the strict weekly/monthly rule.
    """
    prime_set = set(prime_codes)
    # Start broad, then tighten only if the candidate pool would exceed the
    # two-pages-per-name request budget.  This avoids ever spending >900 calls.
    thresholds = [50, 45, 40, 35, 30]
    chosen = None
    found = {}
    for rsi_max in thresholds:
        params = {
            'filters': f'rsi:lte:{rsi_max},macdHist:lte:0',
            'sort_by': 'rsi',
            'sort_order': 'asc',
            'as_of': 'now',
            'limit': SCREEN_LIMIT,
        }
        # One request gets the total.  Only paginate once we know the pool fits.
        data = api_get('/screening', params, retries=1)
        total = int(data.get('total_count') or 0)
        chosen = {'rsi_max': rsi_max, 'total_count': total}
        if total <= MAX_PRICE_CANDIDATES:
            found = {}
            cursor = None
            while True:
                p = dict(params)
                if cursor:
                    p['cursor'] = cursor
                if cursor is None:
                    page = data
                else:
                    page = api_get('/screening', p, retries=1)
                for item in page.get('securities') or []:
                    code = str(item.get('security_code') or '').strip().upper()
                    if code in prime_set:
                        found[code] = {
                            'code': code,
                            'name': item.get('name') or code,
                        }
                cursor = page.get('next_cursor')
                if not cursor:
                    break
            break
    if chosen is None:
        raise RuntimeError('スクリーニング条件を確定できませんでした。')
    return found, chosen


def fetch_prices(code):
    rows = []
    seen = set()
    cursor = None
    while True:
        data = api_get(f'/securities/{code}/prices', {'limit': 500, **({'cursor': cursor} if cursor else {})}, retries=1)
        for x in data.get('prices') or []:
            d = x.get('date')
            if not d or d in seen:
                continue
            if x.get('close') is None and x.get('adj_close') is None:
                continue
            seen.add(d)
            rows.append(x)
        cursor = data.get('next_cursor')
        if len(rows) >= PRICE_TARGET or not cursor:
            break
    rows.sort(key=lambda x: x['date'], reverse=True)
    if len(rows) < 15:
        raise ValueError(f'Not enough price history for {code}: {len(rows)} rows')
    return rows


def tora_check(rows):
    weekly = ud.weekly_closes(rows)
    # Tora uses one RSI definition only: standard Wilder RSI(14).
    # The app's legacy simple RSI remains untouched, but it is deliberately
    # excluded from this scanner so there is no ambiguity about the gate.
    weekly_rsi = ud.rsi14_wilder(weekly)
    monthly = ud.monthly_closes(rows)
    signal = ud.monthly_macd_bottom_signal(monthly, weekly_rsi)
    return weekly_rsi, signal, len(weekly), len(monthly)


def main():
    if not os.environ.get('IRBANK_API_KEY'):
        raise SystemExit('IRBANK_API_KEY is not set')

    start = time.monotonic()
    usage_before = api_get('/usage', {}, retries=1)
    remaining = usage_before.get('remaining')
    if remaining is not None and int(remaining) < 450:
        raise SystemExit(f'IRBANK残り枠が少ないため安全停止: remaining={remaining}')

    prime, prime_pages = load_prime_universe()
    screened, prefilter = screening_prefilter(list(prime))

    results = []
    errors = 0
    incomplete = False
    for i, hit in enumerate(screened.values(), 1):
        try:
            if not budget_guard(2):
                incomplete = True
                break
            rows = fetch_prices(hit['code'])
            weekly_rsi, signal, weekly_points, monthly_points = tora_check(rows)
            if signal.get('active') and weekly_rsi is not None and weekly_rsi <= 30:
                results.append({
                    'code': hit['code'],
                    'name': hit['name'],
                    'market': 'Prime',
                    'weekly_rsi': weekly_rsi,
                    'weekly_rsi_method': 'Wilder',
                    'monthly_macd_state': signal.get('state'),
                    'monthly_macd': signal.get('macd'),
                    'monthly_signal': signal.get('signal'),
                    'monthly_hist': signal.get('hist'),
                    'prev_hist': signal.get('prev_hist'),
                    'prev2_hist': signal.get('prev2_hist'),
                    'weekly_points': weekly_points,
                    'monthly_points': monthly_points,
                    'reason': signal.get('reason'),
                    'as_of': rows[0].get('date'),
                })
        except Exception as exc:
            errors += 1
            print(f'Tora scan error {hit["code"]}: {exc}')
        if i % 25 == 0:
            print(f'Tora progress {i}/{len(screened)}; requests={getattr(ud,"_request_count",0)}')

    usage_after = api_get('/usage', {}, retries=1) if budget_guard(1) else {}
    requests = int(getattr(ud, '_request_count', 0))
    results.sort(key=lambda x: (x['weekly_rsi'], x['code']))
    output = {
        'updated_at': now_jst().isoformat(),
        'source': 'IRBANK API / Prime market',
        'status': 'complete' if not incomplete else 'budget_guard_stopped',
        'request_budget': 1000,
        'request_safety_target': REQUEST_BUDGET,
        'request_count': requests,
        'daily_remaining_before': remaining,
        'daily_remaining_after': usage_after.get('remaining') if isinstance(usage_after, dict) else None,
        'elapsed_seconds': round(time.monotonic() - start, 1),
        'universe_count': len(prime),
        'universe_pages': prime_pages,
        'prefilter': prefilter,
        'prefilter_count': len(screened),
        'checked_count': len(screened) if not incomplete else None,
        'error_count': errors,
        'rule': 'トラさん：標準Wilder RSI(14)の週足RSI <= 30 AND （月足MACDゴールデンクロス OR 月足MACD GC手前でヒストグラムが2か月連続縮小）。',
        'candidates': results,
        'note': '表示するのはトラさん条件を同時に満たした銘柄だけ。大底Score・信用需給・通常BUYスコアは判定に使用しない。IRBANKのRequest上限を守るため、日足RSI＋日足MACDヒストグラムで候補を事前に絞ってから、週足/月足を厳密判定する。',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'request_count': requests,
        'prime': len(prime),
        'prefilter_count': len(screened),
        'checked': len(screened) if not incomplete else 'incomplete',
        'tora_candidates': len(results),
        'errors': errors,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
