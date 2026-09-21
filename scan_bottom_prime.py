"""Prime-market-only '禁断の大底' candidate scan.

This scan is intentionally separate from the daily update.  It uses IRBANK's
screening API for a cheap first pass, then fetches price history only for a
bounded number of Prime candidates.  Weekly margin data is fetched only for
the final deep candidates.

Hard safety goal: keep this scan below 1,000 IRBANK requests per run.  The
script stops before the budget is exhausted rather than silently exceeding it.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import update_data as ud

OUT = Path('data/bottom_prime_candidates.json')
REQUEST_BUDGET = 900       # leave a 100-request safety reserve under 1,000
STAGE1_LIMIT = 350         # max Prime names receiving 500-row price history
DEEP_LIMIT = 100            # max names receiving the second price page
SCREEN_PAGE_LIMIT = 8       # 8 x 100 per profile; prevents runaway pagination
PRICE_PAGE_SIZE = 500
MARGIN_PERIOD = '26w'

SCREEN_PROFILES = [
    # Different routes reduce the chance that one narrow filter misses a
    # bottom candidate.  Results are unioned and later scored locally.
    {
        'name': 'RSI×25MA',
        'filters': 'rsi:lte:35,rsi:gte:0,sma25GapPct:lte:-5',
        'sort_by': 'rsi', 'sort_order': 'asc',
    },
    {
        'name': '深い25MA乖離',
        'filters': 'rsi:lte:40,rsi:gte:0,sma25GapPct:lte:-8',
        'sort_by': 'sma25GapPct', 'sort_order': 'asc',
    },
    {
        'name': 'RSI×BB下側',
        'filters': 'rsi:lte:35,rsi:gte:0,bbp:lte:0.15',
        'sort_by': 'rsi', 'sort_order': 'asc',
    },
]


def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))


def metric_map(item):
    return {m.get('field'): m.get('value') for m in item.get('metrics') or [] if m.get('field')}


def budget_guard(extra=1):
    """Return False before a logical request could consume the hard budget."""
    return int(getattr(ud, '_request_count', 0)) + extra <= REQUEST_BUDGET


def api_get(path, params=None, retries=1):
    if not budget_guard(retries):
        raise RuntimeError('REQUEST_BUDGET_REACHED')
    return ud.get(path, params or {}, retries=retries)


def usage_snapshot():
    # /usage does not consume the daily quota, according to IRBANK docs, but it
    # is still counted by our local safety counter because it is an HTTP call.
    try:
        data = api_get('/usage', {}, retries=1)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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
            raise RuntimeError('Prime銘柄マスターのページ数が想定上限を超えました。安全のため停止しました。')
    return list(companies.values()), pages


def screening_candidates(prime_codes):
    prime_set = set(prime_codes)
    found = {}
    profile_stats = []
    for profile in SCREEN_PROFILES:
        cursor = None
        pages = 0
        total = 0
        while True:
            params = {
                'filters': profile['filters'],
                'sort_by': profile['sort_by'],
                'sort_order': profile['sort_order'],
                'as_of': 'now',
                'limit': 100,
            }
            if cursor:
                params['cursor'] = cursor
            data = api_get('/screening', params, retries=1)
            pages += 1
            total = int(data.get('total_count') or total or 0)
            for item in data.get('securities') or []:
                code = str(item.get('security_code') or '').strip().upper()
                if code not in prime_set:
                    continue
                found.setdefault(code, {
                    'code': code,
                    'name': item.get('name') or code,
                    'screen_hits': [],
                    'screen_metrics': {},
                })
                mm = metric_map(item)
                found[code]['screen_metrics'].update(mm)
                found[code]['screen_hits'].append(profile['name'])
            cursor = data.get('next_cursor')
            if not cursor or pages >= SCREEN_PAGE_LIMIT:
                break
        profile_stats.append({'profile': profile['name'], 'pages': pages, 'matching_total': total})
    return found, profile_stats


def fetch_price_page(code, cursor=None):
    params = {'limit': PRICE_PAGE_SIZE}
    if cursor:
        params['cursor'] = cursor
    return api_get(f'/securities/{code}/prices', params, retries=1)


def fetch_prices(code):
    rows = []
    seen = set()
    data = fetch_price_page(code)
    attribution = data.get('attribution') or {}
    for x in data.get('prices') or []:
        d = x.get('date')
        if not d or d in seen:
            continue
        if x.get('close') is None and x.get('adj_close') is None:
            continue
        seen.add(d)
        rows.append(x)
    rows.sort(key=lambda x: x['date'], reverse=True)
    return rows, attribution, data.get('next_cursor')


def fetch_older_price_page(code, cursor):
    if not cursor:
        return [], {}, None
    data = fetch_price_page(code, cursor)
    rows = []
    seen = set()
    for x in data.get('prices') or []:
        d = x.get('date')
        if not d or d in seen:
            continue
        if x.get('close') is None and x.get('adj_close') is None:
            continue
        seen.add(d)
        rows.append(x)
    return rows, data.get('attribution') or {}, data.get('next_cursor')


def local_bottom_score(stock, rows):
    score = 0
    reasons = []
    rsi = stock.get('rsi14')
    wrsi = stock.get('rsi14_weekly')
    vs25 = stock.get('vs25')
    vs75 = stock.get('vs75')
    vs200 = stock.get('vs200')
    ret20 = stock.get('ret20')
    vr = stock.get('volume_ratio')
    if rsi is not None:
        if rsi <= 25: score += 3; reasons.append('日足RSI25以下')
        elif rsi <= 30: score += 2; reasons.append('日足RSI30以下')
        elif rsi <= 35: score += 1; reasons.append('日足RSI35以下')
    if wrsi is not None:
        if wrsi <= 25: score += 4; reasons.append('週足RSI25以下')
        elif wrsi <= 30: score += 3; reasons.append('週足RSI30以下')
        elif wrsi <= 35: score += 2; reasons.append('週足RSI35以下')
    if vs25 is not None and vs25 <= -10: score += 2; reasons.append('25MAから10%以上下')
    elif vs25 is not None and vs25 <= -5: score += 1; reasons.append('25MAから5%以上下')
    if vs75 is not None and vs75 <= -10: score += 3; reasons.append('75MAから10%以上下')
    elif vs75 is not None and vs75 <= -5: score += 2; reasons.append('75MAから5%以上下')
    if vs200 is not None and vs200 <= -20: score += 3; reasons.append('200MAから20%以上下')
    elif vs200 is not None and vs200 <= -10: score += 2; reasons.append('200MAから10%以上下')
    if ret20 is not None and ret20 <= -15: score += 2; reasons.append('20日で15%以上下落')
    elif ret20 is not None and ret20 <= -8: score += 1; reasons.append('20日で8%以上下落')
    if vr is not None and vr >= 2: score += 2; reasons.append('出来高2倍以上')
    elif vr is not None and vr >= 1.5: score += 1; reasons.append('出来高増加')
    for icon in stock.get('signal_icons') or []:
        code = icon.get('code')
        if code == 'BOLLINGER_DAILY_LOWER': score += 2; reasons.append('日足BB-2σ割れ')
        elif code == 'BOLLINGER_WEEKLY_LOWER': score += 2; reasons.append('週足BB-2σ割れ')
    if stock.get('candle_signal'):
        score += 1
        reasons.append('反転候補ローソク')
    return score, reasons


def compact_result(company, stock, margin, bottom_signal, screen_hits, score, reasons):
    supply = None
    if margin:
        nb = margin.get('net_balance')
        nc = margin.get('net_change')
        cr = margin.get('credit_ratio')
        supply = {
            'status': margin.get('status'),
            'date': margin.get('date'),
            'buy_balance': margin.get('buy_balance'),
            'sell_balance': margin.get('sell_balance'),
            'buy_change': margin.get('buy_change'),
            'sell_change': margin.get('sell_change'),
            'credit_ratio': cr,
            'net_balance': nb,
            'net_change': nc,
            'improving': nc is not None and nc < 0,
        }
        if nc is not None and nc < 0:
            score += 2
            reasons.append('信用需給改善')
        elif nc is not None and nc > 0:
            score -= 1
            reasons.append('信用需給は悪化')
    return {
        'code': company['code'],
        'name': company['name'],
        'market': 'Prime',
        'industry': company.get('industry'),
        'bottom_score': score,
        'bottom_reasons': reasons,
        'screen_hits': screen_hits,
        'price': stock.get('price'),
        'rsi14': stock.get('rsi14'),
        'rsi14_weekly': stock.get('rsi14_weekly'),
        'vs25': stock.get('vs25'),
        'vs75': stock.get('vs75'),
        'vs200': stock.get('vs200'),
        'ret20': stock.get('ret20'),
        'volume_ratio': stock.get('volume_ratio'),
        'candle_signal': stock.get('candle_signal'),
        'monthly_bottom': bottom_signal,
        'supply': supply,
        'weekly_points': stock.get('weekly_points'),
        'data_points': stock.get('data_points'),
        'as_of': rows_last_date(stock),
    }


def rows_last_date(stock):
    # calc() does not carry a canonical source date in every release; price is
    # enough for display. Kept as a nullable field for future use.
    return stock.get('date')


def main():
    if not os.environ.get('IRBANK_API_KEY'):
        raise SystemExit('IRBANK_API_KEY is not set')

    start = time.monotonic()
    before = usage_snapshot()
    initial_remaining = before.get('remaining')
    if initial_remaining is not None and int(initial_remaining) < 550:
        raise SystemExit(f'IRBANK残り枠が少ないため安全停止: remaining={initial_remaining}。禁断の大底探索は最低550件程度の余裕を確保します。')

    prime, prime_pages = load_prime_universe()
    prime_codes = [x['code'] for x in prime]
    prime_map = {x['code']: x for x in prime}
    screened, profile_stats = screening_candidates(prime_codes)

    # Stage 1: only screen hits get a 500-row price request.
    stage1_pool = sorted(
        screened.values(),
        key=lambda x: (
            min([v for k, v in x['screen_metrics'].items() if k == 'rsi'] or [999]),
            min([v for k, v in x['screen_metrics'].items() if k == 'sma25GapPct'] or [999]),
            x['code'],
        )
    )[:STAGE1_LIMIT]

    stage1 = []
    errors = 0
    for i, hit in enumerate(stage1_pool, 1):
        try:
            rows, attribution, cursor = fetch_prices(hit['code'])
            if len(rows) < 15:
                continue
            stock = ud.calc(rows, breadth_info={}, supply_info=None)
            score, reasons = local_bottom_score(stock, rows)
            hit2 = dict(hit)
            hit2.update({'rows': rows, 'cursor': cursor, 'stock': stock, 'attribution': attribution,
                         'score': score, 'reasons': reasons})
            stage1.append(hit2)
        except Exception as exc:
            errors += 1
            print(f'Stage1 error {hit["code"]}: {exc}')
        if i % 25 == 0:
            print(f'Stage1 progress {i}/{len(stage1_pool)}; requests={getattr(ud,"_request_count",0)}')
        if not budget_guard(2):
            print('Request budget guard stopped Stage1 safely.')
            break

    stage1.sort(key=lambda x: (-x['score'], -(x['stock'].get('rsi14_weekly') or -999), x['code']))
    deep_pool = stage1[:DEEP_LIMIT]

    # Stage 2: fetch one older page only for the strongest local bottom shapes.
    deep = []
    for i, hit in enumerate(deep_pool, 1):
        rows = list(hit['rows'])
        try:
            if hit.get('cursor'):
                older, attribution, _ = fetch_older_price_page(hit['code'], hit['cursor'])
                if older:
                    seen = {r.get('date') for r in rows}
                    rows.extend([r for r in older if r.get('date') not in seen])
                    rows.sort(key=lambda x: x['date'], reverse=True)
            stock = ud.calc(rows, breadth_info={}, supply_info=None)
            weekly = ud.rsi14(ud.weekly_closes(rows))
            monthly = ud.monthly_closes(rows)
            bottom_signal = ud.monthly_macd_bottom_signal(monthly, weekly)
            score = hit['score'] + (4 if bottom_signal.get('active') else 0)
            reasons = list(hit['reasons'])
            if bottom_signal.get('active'):
                reasons.append(bottom_signal.get('state') or '月足MACD大底シグナル')
            deep.append({**hit, 'rows': rows, 'stock': stock, 'score': score,
                         'reasons': reasons, 'bottom_signal': bottom_signal})
        except Exception as exc:
            errors += 1
            print(f'Deep error {hit["code"]}: {exc}')
        if i % 20 == 0:
            print(f'Deep progress {i}/{len(deep_pool)}; requests={getattr(ud,"_request_count",0)}')
        if not budget_guard(2):
            print('Request budget guard stopped Deep stage safely.')
            break

    deep.sort(key=lambda x: (-x['score'], x['code']))
    final_pool = deep[:DEEP_LIMIT]

    # Stage 3: weekly margin is the expensive per-name endpoint, so only the
    # bounded final pool gets it.
    margins = {}
    margin_errors = 0
    for i, hit in enumerate(final_pool, 1):
        code = hit['code']
        try:
            data = api_get(f'/securities/{code}/weekly-margin-balance', {'period': MARGIN_PERIOD}, retries=1)
            weeks = [w for w in (data.get('weeks') or []) if w.get('report_date')]
            if weeks:
                w = weeks[-1]
                row = {
                    'status': 'complete', 'date': w.get('report_date'),
                    'buy_balance': w.get('buy_balance_total'),
                    'sell_balance': w.get('sell_balance_total'),
                    'buy_change': w.get('buy_balance_change'),
                    'sell_change': w.get('sell_balance_change'),
                    'credit_ratio': w.get('margin_ratio'),
                }
                if row['buy_balance'] is not None and row['sell_balance'] is not None:
                    row['net_balance'] = row['buy_balance'] - row['sell_balance']
                if row['buy_change'] is not None and row['sell_change'] is not None:
                    row['net_change'] = row['buy_change'] - row['sell_change']
                margins[code] = row
        except Exception as exc:
            margin_errors += 1
            print(f'Margin error {code}: {exc}')
        if i % 20 == 0:
            print(f'Margin progress {i}/{len(final_pool)}; requests={getattr(ud,"_request_count",0)}')
        if not budget_guard(2):
            print('Request budget guard stopped margin stage safely.')
            break

    results = []
    for hit in final_pool:
        results.append(compact_result(
            prime_map[hit['code']], hit['stock'], margins.get(hit['code']),
            hit.get('bottom_signal') or {}, hit.get('screen_hits') or [],
            hit['score'], list(hit['reasons']),
        ))
    results.sort(key=lambda x: (-x['bottom_score'], x['code']))

    after = usage_snapshot()
    requests = int(getattr(ud, '_request_count', 0))
    output = {
        'updated_at': now_jst().isoformat(),
        'source': 'IRBANK API / Prime market',
        'status': 'complete' if requests < 1000 else 'budget_guard_stopped',
        'request_budget': 1000,
        'request_safety_target': REQUEST_BUDGET,
        'request_count': requests,
        'daily_remaining_before': initial_remaining,
        'daily_remaining_after': after.get('remaining'),
        'elapsed_seconds': round(time.monotonic() - start, 1),
        'universe_count': len(prime),
        'universe_pages': prime_pages,
        'screen_profile_stats': profile_stats,
        'screen_candidate_count': len(screened),
        'stage1_price_count': len(stage1),
        'deep_price_count': len(deep),
        'margin_count': len(margins),
        'error_count': errors,
        'margin_error_count': margin_errors,
        'rule': 'Prime限定。IRBANKスクリーニング3経路→最大350銘柄を500本日足で一次判定→最大100銘柄だけ追加履歴＋月足MACD→最終100銘柄だけ週次信用残を取得。日次更新とは完全分離。',
        'candidates': results,
        'note': 'これは「買い推奨」ではなく、底値圏の深さと反転兆候を拾うための探索候補。最終判断は別途確認が必要。',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'request_count': requests,
        'daily_remaining_before': initial_remaining,
        'daily_remaining_after': after.get('remaining'),
        'prime': len(prime), 'screen_candidates': len(screened),
        'stage1': len(stage1), 'deep': len(deep), 'margin': len(margins),
        'results': len(results), 'errors': errors + margin_errors,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
