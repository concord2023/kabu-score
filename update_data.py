import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

from regime_model_v2 import classify_regime, decide

API = 'https://api.irbank.net/v1'
TOKEN = os.environ.get('IRBANK_API_KEY')
if not TOKEN:
    raise SystemExit('IRBANK_API_KEY is not set')

with open('watchlist.json', encoding='utf-8') as f:
    watch_data = json.load(f)
watch = watch_data.get('stocks', [])

# User-added stocks are kept separately so replacing the source ZIP does not
# silently erase additions that were made through the app/GitHub workflow.
CUSTOM_WATCHLIST = 'data/custom_watchlist.json'
try:
    with open(CUSTOM_WATCHLIST, encoding='utf-8') as f:
        custom_items = json.load(f).get('stocks', [])
except (FileNotFoundError, json.JSONDecodeError):
    custom_items = []
existing_watch_codes = {
    re.sub(r'[^0-9A-Z]', '', str(x.get('code') if isinstance(x, dict) else x).strip().upper())
    for x in watch
}
for item in custom_items:
    code = re.sub(r'[^0-9A-Z]', '', str(item.get('code') if isinstance(item, dict) else item).strip().upper())
    if code and code not in existing_watch_codes:
        watch.append(item)
        existing_watch_codes.add(code)
watch_data['stocks'] = watch

# Keep API traffic comfortably below IRBANK's current 60 requests/minute limit.
# One daily run uses about 55 authenticated requests for 18 stocks plus one public
# breadth request. The small delay also makes transient 429s much less likely.
MIN_REQUEST_INTERVAL = 1.20
_last_request_at = 0.0


def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))


def rate_limit_wait():
    global _last_request_at
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def get(path, params=None, retries=4):
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(retries):
        rate_limit_wait()
        req = urllib.request.Request(
            url,
            headers={
                'Authorization': f'Bearer {TOKEN}',
                'User-Agent': 'kabu-score/10.0'
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = e.headers.get('Retry-After')
            try:
                delay = float(retry_after) if retry_after else min(30, 2 ** attempt)
            except ValueError:
                delay = min(30, 2 ** attempt)
            time.sleep(max(delay, 2.0))
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f'IRBANK request failed after retries: {url}: {last_error}')


# Optional manual stock addition from GitHub Actions workflow_dispatch.
# The input may be either a security code (e.g. 6323) or a company name
# (e.g. ローツェ). IRBANK resolves the official code/name automatically.
def resolve_security_query(query):
    raw = str(query or '').strip()
    if not raw:
        return None

    normalized_code = re.sub(r'[^0-9A-Z]', '', raw.upper())
    if re.fullmatch(r'[0-9A-Z]{4,5}', normalized_code):
        meta = get(f'/securities/{normalized_code}')
        return meta

    # IRBANK supports partial matching by company name or security code.
    result = get('/securities', {'q': raw, 'limit': 20})
    securities = result.get('securities') or []
    if not securities:
        raise SystemExit(f'銘柄が見つかりませんでした: {raw}')

    # Prefer an exact company-name match. Otherwise use the first matching
    # listed security returned by IRBANK's code/name search.
    exact = next((x for x in securities if str(x.get('name') or '').strip() == raw), None)
    meta = exact or securities[0]
    code = str(meta.get('code') or '').strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{4,5}', code):
        raise SystemExit(f'有効な証券コードを取得できませんでした: {raw}')
    return get(f'/securities/{code}')

add_query = os.environ.get('ADD_STOCK_QUERY', os.environ.get('ADD_STOCK_CODE', '')).strip()
if add_query:
    meta = resolve_security_query(add_query)
    add_code = str(meta.get('code') or '').strip().upper()
    resolved_name = str(meta.get('name') or '').strip()
    resolved_industry = meta.get('industry')
    listing_status = meta.get('listing_status')

    if not resolved_name or not add_code:
        raise SystemExit(f'銘柄情報を取得できませんでした: {add_query}')
    if listing_status == 'delisted':
        raise SystemExit(f'上場廃止銘柄のため追加できません: {add_code} {resolved_name}')

    existing_codes = {
        re.sub(r'[^0-9A-Z]', '', str(x.get('code') if isinstance(x, dict) else x).strip().upper())
        for x in watch
    }
    if add_code not in existing_codes:
        item = {'code': add_code, 'name': resolved_name}
        if resolved_industry:
            item['industry'] = resolved_industry
        watch.append(item)
        watch_data['stocks'] = watch
        Path(CUSTOM_WATCHLIST).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(CUSTOM_WATCHLIST, encoding='utf-8') as f:
                custom_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            custom_data = {'stocks': []}
        custom_stocks = custom_data.setdefault('stocks', [])
        custom_codes = {re.sub(r'[^0-9A-Z]', '', str(x.get('code') if isinstance(x, dict) else x).strip().upper()) for x in custom_stocks}
        if add_code not in custom_codes:
            custom_stocks.append(item)
        with open(CUSTOM_WATCHLIST, 'w', encoding='utf-8') as f:
            json.dump(custom_data, f, ensure_ascii=False, indent=2)
        with open('watchlist.json', 'w', encoding='utf-8') as f:
            json.dump(watch_data, f, ensure_ascii=False, indent=2)
        print(f'Added stock to watchlist: {add_code} {resolved_name}')
    else:
        print(f'Stock already exists in watchlist: {add_code} {resolved_name}')

def get_all_prices(code, minimum=75):
    # Fetch at least enough history for the 75MA, and continue pagination up to
    # 260 sessions when available so the 200MA can be calculated. Do NOT reject
    # a newly listed stock merely because it has fewer than 200 sessions: in that
    # case 25/75MA and the other available indicators should still work, while
    # 200MA remains explicitly unavailable. This is important for newer names
    # such as 485A (PowerX). IRBANK supports cursor pagination.
    rows = []
    attribution = {}
    cursor = None
    seen = set()
    while True:
        params = {'limit': 500}
        if cursor:
            params['cursor'] = cursor
        data = get(f'/securities/{code}/prices', params)
        attribution = data.get('attribution') or attribution
        page = data.get('prices') or []
        for x in page:
            d = x.get('date')
            if not d or d in seen:
                continue
            if x.get('close') is None and x.get('adj_close') is None:
                continue
            seen.add(d)
            rows.append(x)
        cursor = data.get('next_cursor')
        if len(rows) >= minimum or not cursor:
            break

    if not rows:
        raise ValueError(f'No price rows returned for {code}')
    rows.sort(key=lambda x: x['date'], reverse=True)
    if len(rows) < minimum:
        raise ValueError(f'Not enough price history for {code}: {len(rows)} rows (75MA requires 75+ sessions)')
    return rows, attribution


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._table = []
        elif tag == 'tr' and self._table is not None:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None and self._row is not None:
            self._row.append(' '.join(''.join(self._cell).split()))
            self._cell = None
        elif tag == 'tr' and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == 'table' and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def fetch_breadth_and_nikkei(target_date):
    """Fetch one public breadth table once per daily run.

    This is deliberately optional: a source-side delay must not invalidate all
    18 stock calculations. Market breadth is context, not a hard buy filter.
    """
    url = 'https://tofuhardboiled.com/updownratio/'
    req = urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0 (compatible; kabu-score/10.0)'}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode('utf-8', errors='ignore')

    p = TableParser()
    p.feed(html)
    daily = []
    for table in p.tables:
        for row in table:
            if len(row) >= 10 and re.fullmatch(r'\d{4}/\d{2}/\d{2}', row[0]):
                try:
                    daily.append((
                        row[0],
                        int(row[3].replace(',', '')),
                        int(row[4].replace(',', '')),
                        float(row[2].replace(',', '')),
                    ))
                except Exception:
                    pass
    daily.sort(key=lambda x: x[0], reverse=True)
    target = target_date.replace('-', '/')
    idx = next((i for i, x in enumerate(daily) if x[0] == target), None)
    if idx is None:
        raise RuntimeError(f'Breadth source has no row for {target_date}')

    def ratio(n):
        part = daily[idx:idx + n]
        if len(part) < n:
            return None
        up = sum(x[1] for x in part)
        down = sum(x[2] for x in part)
        return round(up / down * 100, 2) if down else None

    nikkei_value = daily[idx][3]
    nikkei_change = None
    if idx + 1 < len(daily) and daily[idx + 1][3]:
        nikkei_change = round((nikkei_value / daily[idx + 1][3] - 1) * 100, 2)

    return {
        'date': target_date,
        'breadth': {
            '6d': ratio(6), '10d': ratio(10),
            '15d': ratio(15), '25d': ratio(25),
        },
        'nikkei_value': nikkei_value,
        'nikkei_change': nikkei_change,
        'source_url': url,
        'source': '豆腐ハードボイルド（東証プライムの値上がり・値下がり銘柄数から算出）',
    }


def normalize_security_code(value):
    """Return a clean IRBANK security code from a watchlist item/value."""
    if isinstance(value, dict):
        value = value.get('code')
    if value is None:
        return None
    code = str(value).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{4,5}', code):
        raise ValueError(f'Invalid security code: {value!r}')
    return code


def screening_metric(code, name, target_date, field):
    """Read one supply metric for one exact security.

    The final V8 architecture intentionally keeps supply data independent from
    the BUY decision.  We query by name, then *strictly* match security_code.
    We never accept the first screening result because partial-name searches
    can otherwise attach another company's metric to the requested stock.
    """
    code = normalize_security_code(code)
    if not code:
        return None, None

    attempts = []
    if target_date:
        attempts.append(target_date)
    attempts.append('now')

    last_error = None
    for as_of in dict.fromkeys(attempts):
        try:
            data = get('/screening', {
                'name': name,
                'sort_by': field,
                'sort_order': 'desc',
                'as_of': as_of,
                'limit': 100,
            })
            matches = data.get('securities') or []
            match = next(
                (x for x in matches if normalize_security_code(x.get('security_code')) == code),
                None,
            )
            if match is None:
                continue
            for metric in match.get('metrics') or []:
                if metric.get('field') == field and metric.get('value') is not None:
                    return metric.get('value'), metric.get('as_of')
        except Exception as e:
            last_error = e

    if last_error:
        raise last_error
    return None, None


def fetch_supply_batch(target_date, watch_items):
    """Fetch the five validated supply metrics for every watchlist stock.

    This is deliberately per-stock rather than a market-wide top-100 query.
    A market-wide sort can omit perfectly valid watchlist stocks that are not
    in the first 100 rows.  The request pacing in get() keeps this below the
    API's rate limit while making the result deterministic for all watchlist
    names.

    Missing weekly fields remain missing; they are never guessed or copied from
    another security.
    """
    fields = [
        'marginBuyBalance',
        'marginSellBalance',
        'marginBuyBalanceChangeWow',
        'marginSellBalanceChangeWow',
        'marginRatio',
    ]

    entries = []
    for item in watch_items:
        if isinstance(item, dict):
            code = normalize_security_code(item.get('code'))
            name = str(item.get('name') or code or '').strip()
        else:
            code = normalize_security_code(item)
            name = code or ''
        if not code:
            continue
        entries.append((code, name))

    result = {}
    errors = []

    for code, name in entries:
        row = {
            'date': target_date,
            'status': 'unavailable',
            'source_url': 'https://api.irbank.net/v1/screening',
            'source_note': 'IRBANK API スクリーニング（信用買い残・信用売り残・前週比・信用倍率）',
            'missing_fields': list(fields),
            'field_dates': {},
            'api_errors': [],
        }

        for field in fields:
            try:
                value, as_of = screening_metric(code, name, target_date, field)
                if value is None:
                    continue
                row[field] = value
                row[field + '_as_of'] = as_of
                row['field_dates'][field] = as_of
                if field in row['missing_fields']:
                    row['missing_fields'].remove(field)
            except Exception as e:
                message = f'{code}:{field}: {e}'
                row['api_errors'].append(message)
                errors.append(message)

        row['buy_balance'] = row.get('marginBuyBalance')
        row['sell_balance'] = row.get('marginSellBalance')
        row['credit_ratio'] = row.get('marginRatio')
        row['buy_change'] = row.get('marginBuyBalanceChangeWow')
        row['sell_change'] = row.get('marginSellBalanceChangeWow')
        # Net credit balance = buy balance - sell balance.
        # Net weekly change = buy-balance change - sell-balance change.
        row['net_balance'] = (row['buy_balance'] - row['sell_balance']) if row.get('buy_balance') is not None and row.get('sell_balance') is not None else None
        row['net_change'] = (row['buy_change'] - row['sell_change']) if row.get('buy_change') is not None and row.get('sell_change') is not None else None

        if not row['missing_fields']:
            row['status'] = 'complete'
        elif len(row['missing_fields']) < len(fields):
            row['status'] = 'partial'

        available_dates = list(row['field_dates'].values())
        if available_dates:
            row['date'] = max(available_dates)

        row['api_partial'] = row['status'] != 'complete'
        result[code] = row

    return result, errors

def supply_points(s):
    """Supply context score using net credit-balance pressure.

    Net change = buy-balance change minus sell-balance change.
    Negative means the future sell-pressure side is shrinking relative to
    future buy-pressure side, so supply is improving.
    """
    if not s:
        return 0, {}
    p = 0
    detail = {}
    bc, sc, r = s.get('buy_change'), s.get('sell_change'), s.get('credit_ratio')
    net_change = s.get('net_change')
    detail['buy_balance_change'] = '減少（改善方向）' if bc is not None and bc < 0 else '増加（重い方向）' if bc is not None and bc > 0 else '横ばい/不明'
    detail['sell_balance_change'] = '増加（改善方向）' if sc is not None and sc > 0 else '減少（支え弱化）' if sc is not None and sc < 0 else '横ばい/不明'
    detail['net_change'] = net_change
    if net_change is not None:
        detail['net_change_direction'] = '改善' if net_change < 0 else '悪化' if net_change > 0 else '横ばい'
        if net_change < 0:
            p += 5
        elif net_change == 0:
            p += 1
    detail['credit_ratio_level'] = r
    if r is not None:
        p += 5 if r <= 3 else 3 if r <= 6 else 1 if r <= 10 else 0
    return min(p, 12), detail


def supply_status(s):
    if not s or s.get('credit_ratio') is None:
        return 'データ不足', '信用倍率データが取得できないため需給判定は保留。'
    r = float(s['credit_ratio'])
    bc, sc = s.get('buy_change'), s.get('sell_change')
    net_change = s.get('net_change')

    # The ratio describes the level; the net change describes the direction.
    # Both are used so that a small increase in sell-balance cannot mask a
    # much larger increase in buy-balance.
    if r >= 10:
        if net_change is not None and net_change < 0:
            return '重い', f'信用倍率{r:.2f}倍と高水準。ただしネット需給は改善（買い残前週比−売り残前週比={net_change:,.0f}）。まだ重さは残る。'
        return '重い', f'信用倍率{r:.2f}倍と高水準。買い残−売り残のネット需給も軽くなく、上値の重さに注意。'

    if net_change is not None and net_change > 0:
        return '重い', f'信用倍率{r:.2f}倍。ネット需給が悪化（買い残前週比−売り残前週比={net_change:,.0f}）しており、買い残増加が売り残増加を上回る。'

    if net_change is not None and net_change < 0:
        if r <= 3:
            return '軽い', f'信用倍率{r:.2f}倍で、ネット需給も改善（{net_change:,.0f}）。買い残−売り残の偏りが小さく、需給は軽い。'
        return '改善方向', f'信用倍率{r:.2f}倍。ネット需給が改善（買い残前週比−売り残前週比={net_change:,.0f}）。'

    if r <= 3:
        return '軽い', f'信用倍率{r:.2f}倍で、買い残−売り残の偏りは比較的小さい。'
    return '中立', f'信用倍率{r:.2f}倍。ネット需給の方向が確認できず、需給だけでは方向を決めにくい。'

def pct(a, b):
    return ((a / b) - 1) * 100 if a is not None and b not in (None, 0) else None


def signal_icons(candle_signal, breakout, supply_info, volume_ratio, price_change, vs20, ret5, range_position60, rsi_daily=None, rsi_weekly=None, bb_daily=None, bb_weekly=None):
    """Expose independent technical/supply clues as icons.

    These are clues, not recommendations. Each icon can light independently;
    the aggregate BUY decision remains separate in regime_model_v2.decide().
    """
    icons = []
    candle = candle_signal or {}
    supply = supply_info or {}

    # Aggregate BUY is deliberately not inferred here.
    # Bottom-volume: meaningful volume appearing in a genuine bottom zone.
    bottom_volume = bool(candle.get('bottom_zone')) and volume_ratio is not None and volume_ratio >= 1.5
    if bottom_volume:
        icons.append({
            'code': 'BOTTOM_VOLUME', 'icon': '💥', 'label': '大底出来高',
            'strength': 'strong' if volume_ratio >= 2.0 else 'medium',
            'reason': f"底値圏で出来高が{volume_ratio:.1f}倍。反転の裏付け候補。"
        })

    # Candlestick reversal clue.
    if candle.get('status') in ('大底反転サイン', '大底反転候補'):
        icons.append({
            'code': 'BOTTOM_REVERSAL', 'icon': '🕯️', 'label': '大底反転',
            'strength': 'strong' if candle.get('status') == '大底反転サイン' else 'medium',
            'reason': candle.get('reason', '')
        })

    # Breakout clue is independent from pullback/bottom logic.
    if breakout.get('confirmed'):
        icons.append({
            'code': 'RANGE_BREAKOUT', 'icon': '📈', 'label': 'レンジブレイク',
            'strength': 'strong', 'reason': breakout.get('reason', '')
        })
    elif breakout.get('status') == 'レンジ抜け候補':
        icons.append({
            'code': 'RANGE_BREAKOUT_WATCH', 'icon': '📈', 'label': 'ブレイク候補',
            'strength': 'medium', 'reason': breakout.get('reason', '')
        })

    # Supply-side buying clue: use BOTH the credit-ratio level and the net
    # weekly change.  A small increase in sell-balance must not trigger a BUY
    # icon when buy-balance is increasing much more.
    bc = supply.get('buy_change')
    sc = supply.get('sell_change')
    cr = supply.get('credit_ratio')
    net_change = supply.get('net_change')
    if net_change is not None and cr is not None and net_change < 0 and cr <= 6:
        icons.append({
            'code': 'SUPPLY_BUY', 'icon': '📦', 'label': '需給買いサイン',
            'strength': 'strong' if cr <= 3 else 'medium',
            'reason': f'ネット需給改善（買い残前週比−売り残前週比={net_change:,.0f}）＋信用倍率{cr:.2f}倍。買い残増加より売り残増加／買い残減少が優勢。'
        })
    elif net_change is not None and net_change < 0 and cr is not None:
        icons.append({
            'code': 'SUPPLY_IMPROVING', 'icon': '📦', 'label': '需給改善',
            'strength': 'medium',
            'reason': f'ネット需給は改善（買い残前週比−売り残前週比={net_change:,.0f}）だが、信用倍率{cr:.2f}倍のため「需給買い」までは付けない。'
        })

    # Explicit RSI threshold clues. These are independent warning/opportunity
    # icons and do not alter the aggregate BUY decision.
    if rsi_daily is not None and rsi_daily < 30:
        icons.append({
            'code': 'RSI_DAILY_OVERSOLD', 'icon': '🔵', 'label': '日足RSI30割れ',
            'strength': 'strong', 'reason': f'日足RSI(14)={rsi_daily:.1f}。30未満の売られ過ぎ水準。'
        })
    elif rsi_daily is not None and rsi_daily > 70:
        icons.append({
            'code': 'RSI_DAILY_OVERBOUGHT', 'icon': '🔴', 'label': '日足RSI70超え',
            'strength': 'strong', 'reason': f'日足RSI(14)={rsi_daily:.1f}。70超の買われ過ぎ水準。'
        })
    if rsi_weekly is not None and rsi_weekly < 30:
        icons.append({
            'code': 'RSI_WEEKLY_OVERSOLD', 'icon': '🔵', 'label': '週足RSI30割れ',
            'strength': 'strong', 'reason': f'週足RSI(14)={rsi_weekly:.1f}。30未満の売られ過ぎ水準。'
        })
    elif rsi_weekly is not None and rsi_weekly > 70:
        icons.append({
            'code': 'RSI_WEEKLY_OVERBOUGHT', 'icon': '🔴', 'label': '週足RSI70超え',
            'strength': 'strong', 'reason': f'週足RSI(14)={rsi_weekly:.1f}。70超の買われ過ぎ水準。'
        })

    # Bollinger-band breakout clues. These are independent signals and do not
    # automatically change the aggregate BUY decision. We use closing-price
    # breaks outside +/-2 sigma, not intraday touches.
    for bb in (bb_daily, bb_weekly):
        if bb:
            icons.append(bb)

    # Overextension warning is useful beside positive clues.
    if vs20 is not None and vs20 >= 15:
        icons.append({
            'code': 'EXTENDED', 'icon': '⚠️', 'label': '過熱警戒',
            'strength': 'medium', 'reason': f'20日MAから{vs20:.1f}%上方。追い買いは慎重に。'
        })

    return icons


def rsi14(vals):
    if len(vals) < 15:
        return None
    gains, losses = [], []
    for i in range(1, 15):
        d = vals[i - 1] - vals[i]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = sum(gains) / 14, sum(losses) / 14
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def weekly_closes(rows):
    """Return one closing price per ISO calendar week, newest first."""
    seen = set()
    closes = []
    for row in rows:
        date = row.get('date')
        if not date:
            continue
        try:
            key = __import__('datetime').date.fromisoformat(date).isocalendar()[:2]
        except Exception:
            continue
        if key in seen:
            continue
        raw = row.get('adj_close') if row.get('adj_close') is not None else row.get('close')
        if raw is None:
            continue
        seen.add(key)
        closes.append(float(raw))
    return closes


def breadth_points(b):
    if not b:
        return 0, {'6d': 0, '10d': 0, '15d': 0, '25d': 0}

    def pts6(x):
        if x <= 60: return 15
        if x <= 70: return 13
        if x <= 80: return 10
        if x <= 90: return 6
        if x <= 100: return 3
        return 0

    def pts10(x):
        if x <= 70: return 10
        if x <= 80: return 8
        if x <= 90: return 6
        if x <= 100: return 3
        return 0

    def pts15(x):
        if x <= 80: return 5
        if x <= 90: return 4
        if x <= 100: return 2
        return 0

    def pts25(x):
        if x <= 100: return 10
        if x <= 110: return 6
        if x <= 115: return 3
        return 0

    p = {
        '6d': pts6(b['6d']), '10d': pts10(b['10d']),
        '15d': pts15(b['15d']), '25d': pts25(b['25d'])
    }
    return min(sum(p.values()), 35), p


def relative_points(x):
    if x is None: return 0
    if x <= -7: return 10
    if x <= -5: return 8
    if x <= -3: return 6
    if x <= -2: return 3
    return 0



def candle_reversal_signals(rows):
    """Detect bottom/reversal candlesticks conservatively.

    A candlestick by itself is NOT a bottom signal.  "大底反転" is only
    exposed when the price is genuinely in a prior-decline/bottom context.
    This prevents ordinary hammers/dojis in healthy trends from lighting up
    most of the watchlist.
    """
    def n(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def candle(r):
        o, h, l, c = (n(r.get(k)) for k in ('open', 'high', 'low', 'close'))
        if None in (o, h, l, c):
            return None
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        upper = h - max(o, c)
        lower = min(o, c) - l
        return {
            'date': r.get('date'), 'open': o, 'high': h, 'low': l, 'close': c,
            'body': body, 'range': rng, 'upper': upper, 'lower': lower,
            'bull': c > o, 'bear': c < o,
        }

    cs = [candle(r) for r in rows[:6]]
    if not cs or cs[0] is None:
        return {'status': 'データ不足', 'patterns': [], 'recent_patterns': [],
                'bottom_zone': False, 'confirmation': '不明', 'lookback_days': 2}

    closes = []
    for r in rows[:61]:
        v = n(r.get('adj_close') if r.get('adj_close') is not None else r.get('close'))
        if v is not None:
            closes.append(v)

    current = cs[0]
    prev = cs[1] if len(cs) > 1 else None
    patterns = []

    # Pattern detection is intentionally strict.  The current/previous
    # 2 sessions are enough; older patterns are stale for a daily alert.
    for i, c in enumerate(cs[:2]):
        if c is None:
            continue
        p = cs[i + 1] if i + 1 < len(cs) else None
        if c['body'] <= c['range'] * 0.35 and c['lower'] >= max(c['body'] * 2.5, c['range'] * 0.45) \
                and c['upper'] <= max(c['body'] * 0.6, c['range'] * 0.10) \
                and c['close'] >= c['low'] + c['range'] * 0.65:
            patterns.append(('hammer', 'ハンマー'))
        if p and p['bear'] and c['bull'] and c['open'] <= p['close'] and c['close'] >= p['open'] \
                and c['body'] >= c['range'] * 0.45:
            patterns.append(('bullish_engulfing', '陽線の包み足'))
        if p and p['bear'] and c['bull'] and c['close'] > (p['open'] + p['close']) / 2 \
                and c['close'] < p['open'] and c['body'] >= c['range'] * 0.30:
            patterns.append(('piercing', '切り返し'))
        if p and abs(c['low'] / p['low'] - 1) <= 0.003 and c['bull'] and c['close'] >= c['open'] + c['body'] * 0.5:
            patterns.append(('tweezer_bottom', '毛抜き底'))
        if p and i == 0 and p['bear'] and p['body'] > p['range'] * 0.45 and c['bull'] \
                and c['close'] > (p['open'] + p['close']) / 2:
            patterns.append(('morning_star', '明けの明星型'))

    # Deduplicate.
    unique = []
    seen = set()
    for code, label in patterns:
        if code not in seen:
            seen.add(code)
            unique.append({'code': code, 'label': label})

    # A bottom zone requires BOTH proximity to a recent low and evidence of
    # a meaningful prior decline.  The old OR-based rule was too permissive.
    bottom_zone = False
    zone_reason = '大底圏の条件を満たしていない'
    decline_context = False
    pos20 = pos60 = dd60 = ret20 = None
    if len(closes) >= 21:
        low20 = min(closes[1:21])
        high20 = max(closes[1:21])
        pos20 = (closes[0] - low20) / low20 * 100 if low20 else None
        ret20 = (closes[0] / closes[20] - 1) * 100 if closes[20] else None
        decline_context = (ret20 is not None and ret20 <= -10) or (pos20 is not None and pos20 <= 5)
        if len(closes) >= 61:
            low60 = min(closes[1:61])
            high60 = max(closes[1:61])
            pos60 = (closes[0] - low60) / low60 * 100 if low60 else None
            dd60 = (closes[0] / high60 - 1) * 100 if high60 else None
            # Require meaningful drawdown + proximity to the 60d low.
            bottom_zone = decline_context and pos60 is not None and pos60 <= 15 and dd60 is not None and dd60 <= -15
        else:
            bottom_zone = decline_context and pos20 is not None and pos20 <= 8
        if bottom_zone:
            zone_reason = '大きな下落後で、直近安値に近い底値圏'
    
    # Confirmation means the current session actually reclaimed the prior
    # high.  It is intentionally separate from merely seeing a pattern.
    confirmed = bool(current and prev and current['bull'] and current['close'] > prev['high'])
    confirmation = '反転確認（前日高値を上抜け）' if confirmed else ('反発は出たが上値確認待ち' if current and current['bull'] else 'まだ陰線。反転確認待ち')

    if not unique:
        status = 'なし'
        strength = '—'
        reason = '直近2営業日に明確な強い反転ローソク足なし。'
    elif bottom_zone:
        # Do not call an unconfirmed candle a "signal".  It is a candidate.
        if confirmed:
            status = '大底反転サイン'
            strength = '強'
            reason = f"{', '.join(x['label'] for x in unique)}。{zone_reason}。{confirmation}。"
        else:
            status = '大底反転候補'
            strength = '中'
            reason = f"{', '.join(x['label'] for x in unique)}。{zone_reason}。{confirmation}。"
    else:
        status = '反転サイン' if confirmed else '反転候補'
        strength = '中' if confirmed else '弱'
        reason = f"{', '.join(x['label'] for x in unique)}。ただし大底圏の条件は未達。{confirmation}。"

    return {
        'status': status,
        'strength': strength,
        'patterns': unique,
        'recent_patterns': unique,
        'bottom_zone': bottom_zone,
        'zone_reason': zone_reason,
        'confirmation': confirmation,
        'reason': reason,
        'lookback_days': 2,
        'decline_context': decline_context,
        'position20': round(pos20, 1) if pos20 is not None else None,
        'position60': round(pos60, 1) if pos60 is not None else None,
        'drawdown60': round(dd60, 1) if dd60 is not None else None,
        'ret20': round(ret20, 1) if ret20 is not None else None,
    }

def breakout_signal(rows):
    """Detect a recent range breakout with price/volume confirmation.

    A breakout is different from a bottom reversal. We use the *intraday high*
    of the prior 20 sessions as the range ceiling, then require a closing
    breakout, strong breakout-day volume, and continued holding above that
    level. This catches bases that start a new up-leg instead of waiting for a
    bottom pattern.
    """
    def n(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    closes=[]; highs=[]; vols=[]; dates=[]
    for r in rows[:45]:
        c=n(r.get('adj_close') if r.get('adj_close') is not None else r.get('close'))
        h=n(r.get('high'))
        v=n(r.get('volume'))
        if c is not None:
            closes.append(c); highs.append(h if h is not None else c); vols.append(v); dates.append(r.get('date'))
    if len(closes) < 22:
        return {'status':'データ不足','is_breakout':False,'confirmed':False,'reason':'レンジ判定に必要な日足データ不足'}

    current=closes[0]
    ma20=sum(closes[1:21])/20
    current_range_high=max(highs[1:21])
    candidates=[]
    for i in range(min(6, len(closes)-20)):
        prior_highs=highs[i+1:i+21]
        if len(prior_highs)<20: continue
        level=max(prior_highs)
        c=closes[i]
        v=vols[i] if i < len(vols) else None
        base=[x for x in vols[i+1:i+21] if x is not None and x>0]
        vr=(v/(sum(base)/len(base))) if v is not None and base else None
        # Close at least 0.5% above the prior range ceiling.
        if c > level*1.005:
            candidates.append({'i':i,'date':dates[i],'close':c,'level':level,'volume_ratio':vr})
    if not candidates:
        return {
            'status':'なし','is_breakout':False,'confirmed':False,
            'breakout_level':round(current_range_high,2),'breakout_date':None,
            'breakout_volume_ratio':None,'distance_from_breakout':round((current/current_range_high-1)*100,2),
            'reason':'直近6営業日に20日レンジ高値を明確に終値突破した形跡なし'
        }

    b=candidates[0]
    held=current >= b['level']*0.99
    volume_ok=b['volume_ratio'] is not None and b['volume_ratio']>=1.5
    ma_ok=current > ma20
    ret5=(current/closes[5]-1)*100 if len(closes)>5 else None
    trend_ok=ret5 is not None and ret5>0
    dist=(current/b['level']-1)*100
    extension_ok=dist <= 15
    confirmed=held and volume_ok and ma_ok and trend_ok and extension_ok
    if confirmed:
        status='レンジ抜け・再上昇'
        strength='強'
        reason=f"{b['date']}に20日レンジ高値{b['level']:.0f}円を終値で上抜け。突破日の出来高比{b['volume_ratio']:.1f}倍、現在も突破水準を維持。"
    elif held:
        status='レンジ抜け候補'
        strength='中'
        reason=f"{b['date']}にレンジ高値を上抜け。突破後は水準を維持しているが、出来高/20MA/上昇継続/過熱度の確認待ち。"
    else:
        status='ブレイク失敗警戒'
        strength='弱'
        reason=f"一度レンジ高値を上抜けたが、現在は突破水準{b['level']:.0f}円を下回る。"
    return {
        'status':status,'strength':strength,'is_breakout':True,'confirmed':confirmed,
        'breakout_level':round(b['level'],2),'breakout_date':b['date'],
        'breakout_volume_ratio':round(b['volume_ratio'],2) if b['volume_ratio'] is not None else None,
        'distance_from_breakout':round(dist,2),'held':held,'volume_ok':volume_ok,
        'ma20_ok':ma_ok,'trend_ok':trend_ok,'extension_ok':extension_ok,
        'ret5':round(ret5,2) if ret5 is not None else None,
        'reason':reason
    }

def bollinger(vals, period=25, sigma=2):
    """Return Bollinger middle/upper/lower for newest-first close values."""
    if len(vals) < period:
        return None, None, None
    window = vals[:period]
    mid = statistics.mean(window)
    sd = statistics.stdev(window) if len(window) >= 2 else 0.0
    return mid, mid + sigma * sd, mid - sigma * sd

def bollinger_signal(current, mid, upper, lower, timeframe, period):
    if current is None or upper is None or lower is None:
        return None
    if current > upper:
        return {
            'code': f'BB_{timeframe}_UPPER_BREAK', 'icon': '🟠',
            'label': f'{"日足" if timeframe == "DAILY" else "週足"}BB+2σ超え',
            'strength': 'strong',
            'reason': f'{"日足" if timeframe == "DAILY" else "週足"}終値が{period}{"日" if timeframe == "DAILY" else "週"}ボリンジャー+2σを上抜け。強い上昇の可能性がある一方、過熱にも注意。'
        }
    if current < lower:
        return {
            'code': f'BB_{timeframe}_LOWER_BREAK', 'icon': '🔵',
            'label': f'{"日足" if timeframe == "DAILY" else "週足"}BB-2σ割れ',
            'strength': 'strong',
            'reason': f'{"日足" if timeframe == "DAILY" else "週足"}終値が{period}{"日" if timeframe == "DAILY" else "週"}ボリンジャー-2σを下抜け。強い下落圧力の可能性がある一方、売られ過ぎにも注意。'
        }
    return None

def calc(rows, breadth_info=None, supply_info=None):
    vals = []
    for x in rows:
        raw = x.get('adj_close') if x.get('adj_close') is not None else x.get('close')
        if raw is not None:
            vals.append(float(raw))
    if not vals:
        raise ValueError('No usable close data')

    close = float(rows[0].get('close') if rows[0].get('close') is not None else vals[0])
    change = pct(close, float(rows[1].get('close'))) if len(rows) > 1 and rows[1].get('close') is not None else None
    vols = [x.get('volume') for x in rows]
    base = [float(v) for v in vols[1:21] if v is not None]
    vr = (float(vols[0]) / statistics.mean(base)) if vols and vols[0] is not None and base else None

    # Standard moving averages include the current close.  The breakout/range
    # windows below intentionally remain prior-session windows where applicable.
    ma5 = statistics.mean(vals[:5]) if len(vals) >= 5 else None
    ma20 = statistics.mean(vals[:20]) if len(vals) >= 20 else None
    ma25 = statistics.mean(vals[:25]) if len(vals) >= 25 else None
    ma75 = statistics.mean(vals[:75]) if len(vals) >= 75 else None
    ma200 = statistics.mean(vals[:200]) if len(vals) >= 200 else None
    bb_daily_mid, bb_daily_upper, bb_daily_lower = bollinger(vals, 25, 2)
    high20 = max(vals[1:21]) if len(vals) >= 21 else None
    low20 = min(vals[1:21]) if len(vals) >= 21 else None
    high60 = max(vals[1:61]) if len(vals) >= 61 else None
    low60 = min(vals[1:61]) if len(vals) >= 61 else None
    r5 = pct(vals[0], vals[5]) if len(vals) > 5 else None
    r10 = pct(vals[0], vals[10]) if len(vals) > 10 else None
    r20 = pct(vals[0], vals[20]) if len(vals) > 20 else None
    rsi = rsi14(vals)
    weekly_vals = weekly_closes(rows)
    rsi_weekly = rsi14(weekly_vals)
    bb_weekly_mid, bb_weekly_upper, bb_weekly_lower = bollinger(weekly_vals, 13, 2)
    bb_daily_signal = bollinger_signal(vals[0], bb_daily_mid, bb_daily_upper, bb_daily_lower, 'DAILY', 25)
    bb_weekly_signal = bollinger_signal(weekly_vals[0] if weekly_vals else None, bb_weekly_mid, bb_weekly_upper, bb_weekly_lower, 'WEEKLY', 13)
    d5 = pct(vals[0], ma5)
    d20 = pct(vals[0], ma20)
    d25 = pct(vals[0], ma25)
    d75 = pct(vals[0], ma75)
    d200 = pct(vals[0], ma200)
    drawdown60 = pct(vals[0], high60)
    range_position60 = ((vals[0]-low60)/(high60-low60)*100) if high60 is not None and low60 is not None and high60 != low60 else None
    volatility20 = (statistics.stdev(vals[:20]) / statistics.mean(vals[:20]) * 100) if len(vals) >= 20 and statistics.mean(vals[:20]) else None

    daily = 15 if change is not None and change <= -7 else 12 if change is not None and change <= -5 else 9 if change is not None and change <= -3 else 6 if change is not None and change <= -2 else 3 if change is not None and change <= -1 else 0
    vol = 10 if vr is not None and vr >= 1.8 else 8 if vr is not None and vr >= 1.5 else 6 if vr is not None and vr >= 1.3 else 3 if vr is not None and vr >= 1.15 else 0
    weak = 10 if d20 is not None and d20 <= -12 else 8 if d20 is not None and d20 <= -8 else 6 if d20 is not None and d20 <= -5 else 3 if d20 is not None and d20 <= -3 else 0
    rp = 10 if rsi is not None and rsi <= 25 else 8 if rsi is not None and rsi <= 30 else 5 if rsi is not None and rsi <= 35 else 2 if rsi is not None and rsi <= 40 else 0
    tp = 10 if d75 is not None and d75 <= -10 else 7 if d75 is not None and d75 <= -5 else 4 if d75 is not None and d75 <= 0 else 0

    bp, bdetail = breadth_points((breadth_info or {}).get('breadth'))
    nikkei_change = (breadth_info or {}).get('nikkei_change')
    rel = (change - nikkei_change) if change is not None and nikkei_change is not None else None
    relp = relative_points(rel)
    sp, sdetail = supply_points(supply_info)
    sstatus, sreason = supply_status(supply_info)
    total = min(daily + vol + weak + rp + tp + bp + relp + sp, 100)

    def deviation_text(v, ma):
        if v is None: return '判定不可'
        if v <= -10: return f'{ma}から大きく下（弱い）'
        if v <= -5: return f'{ma}から下（押し目/下落）'
        if v < 0: return f'{ma}をやや下回る'
        if v < 5: return f'{ma}付近'
        if v < 10: return f'{ma}を上回る'
        return f'{ma}から大きく上（高値警戒）'
    if rsi is None: rsi_text = '判定不可'
    elif rsi < 30: rsi_text = f'RSI {rsi:.1f}（売られ過ぎ）'
    elif rsi <= 40: rsi_text = f'RSI {rsi:.1f}（弱め）'
    elif rsi < 60: rsi_text = f'RSI {rsi:.1f}（中立）'
    elif rsi < 70: rsi_text = f'RSI {rsi:.1f}（強め）'
    else: rsi_text = f'RSI {rsi:.1f}（過熱警戒）'
    if vr is None: volume_text = '判定不可'
    elif vr >= 1.8: volume_text = '出来高急増'
    elif vr >= 1.3: volume_text = '出来高増'
    elif vr >= 0.8: volume_text = '平常圏'
    else: volume_text = '出来高少なめ'
    if range_position60 is None: range_text = '判定不可'
    elif range_position60 <= 20: range_text = '60日レンジ下側（底値圏）'
    elif range_position60 <= 40: range_text = '60日レンジやや下'
    elif range_position60 < 60: range_text = '60日レンジ中間'
    elif range_position60 < 80: range_text = '60日レンジやや上'
    else: range_text = '60日レンジ上側（高値圏）'
    momentum_text = '反発' if change is not None and change > 0 else '下落' if change is not None and change < 0 else '横ばい'
    candle_signal = candle_reversal_signals(rows)
    breakout = breakout_signal(rows)
    icons = signal_icons(candle_signal, breakout, supply_info, vr, change, d20, r5, range_position60, rsi, rsi_weekly, bb_daily_signal, bb_weekly_signal)

    # Keep a compact recent history for the UI chart.  The chart is intentionally
    # presentation data only; decisions continue to use the full calculation above.
    chart_history = []
    for idx, r in enumerate(rows[:100]):
        raw_close = r.get('adj_close') if r.get('adj_close') is not None else r.get('close')
        if raw_close is None: continue
        close_i = float(raw_close)
        def adj_price(z, field):
            raw = z.get(field)
            if raw is None:
                return None
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                return None
            # IRBANK supplies adjusted close but OHLC can remain unadjusted around
            # stock splits.  Apply the same adjustment factor to OHLC so candles
            # and moving averages stay on one price scale (important for 485A).
            raw_close = z.get('close')
            adj_close = z.get('adj_close')
            try:
                raw_close = float(raw_close) if raw_close is not None else None
                adj_close = float(adj_close) if adj_close is not None else None
            except (TypeError, ValueError):
                raw_close = adj_close = None
            if raw_close and adj_close is not None:
                return raw * (adj_close / raw_close)
            return raw

        close_adj = adj_price(r, 'close')
        # Chart moving averages include the candle's current session, matching the
        # standard MA definition and the values shown in the detail metrics.
        win5 = [adj_price(z, 'close') for z in rows[idx:idx+5]]
        win25 = [adj_price(z, 'close') for z in rows[idx:idx+25]]
        win75 = [adj_price(z, 'close') for z in rows[idx:idx+75]]
        win200 = [adj_price(z, 'close') for z in rows[idx:idx+200]]
        win5 = [v for v in win5 if v is not None]
        win25 = [v for v in win25 if v is not None]
        win75 = [v for v in win75 if v is not None]
        win200 = [v for v in win200 if v is not None]
        bb_win = win25
        bb_mid, bb_upper, bb_lower = bollinger(bb_win, 25, 2)
        chart_history.append({
            'date': r.get('date'),
            'open': round(adj_price(r, 'open'), 2) if adj_price(r, 'open') is not None else None,
            'high': round(adj_price(r, 'high'), 2) if adj_price(r, 'high') is not None else None,
            'low': round(adj_price(r, 'low'), 2) if adj_price(r, 'low') is not None else None,
            'close': round(close_adj, 2) if close_adj is not None else round(close_i, 2),
            'ma5': round(statistics.mean(win5), 2) if len(win5) >= 5 else None,
            'ma25': round(statistics.mean(win25), 2) if len(win25) >= 25 else None,
            'ma75': round(statistics.mean(win75), 2) if len(win75) >= 75 else None,
            'ma200': round(statistics.mean(win200), 2) if len(win200) >= 200 else None,
            'bb25_mid': round(bb_mid, 2) if bb_mid is not None else None,
            'bb25_upper': round(bb_upper, 2) if bb_upper is not None else None,
            'bb25_lower': round(bb_lower, 2) if bb_lower is not None else None,
        })
    chart_history.reverse()

    return {
        'date': rows[0].get('date'), 'price': close,
        'change': round(change, 2) if change is not None else None,
        'volume': vols[0] if vols else None,
        'volume_ratio': round(vr, 2) if vr is not None else None,
        'ma5': round(ma5, 2) if ma5 is not None else None,
        'ma20': round(ma20, 2) if ma20 is not None else None,
        'ma25': round(ma25, 2) if ma25 is not None else None,
        'ma75': round(ma75, 2) if ma75 is not None else None,
        'ma200': round(ma200, 2) if ma200 is not None else None,
        'vs5': round(d5, 2) if d5 is not None else None,
        'vs20': round(d20, 2) if d20 is not None else None,
        'vs25': round(d25, 2) if d25 is not None else None,
        'vs75': round(d75, 2) if d75 is not None else None,
        'vs200': round(d200, 2) if d200 is not None else None,
        'bb_daily': {'period':25,'sigma':2,'middle':round(bb_daily_mid,2) if bb_daily_mid is not None else None,'upper':round(bb_daily_upper,2) if bb_daily_upper is not None else None,'lower':round(bb_daily_lower,2) if bb_daily_lower is not None else None,'status':bb_daily_signal['code'] if bb_daily_signal else 'なし'},
        'bb_weekly': {'period':13,'sigma':2,'middle':round(bb_weekly_mid,2) if bb_weekly_mid is not None else None,'upper':round(bb_weekly_upper,2) if bb_weekly_upper is not None else None,'lower':round(bb_weekly_lower,2) if bb_weekly_lower is not None else None,'status':bb_weekly_signal['code'] if bb_weekly_signal else 'なし'},
        'high20': round(high20, 2) if high20 is not None else None,
        'low20': round(low20, 2) if low20 is not None else None,
        'high60': round(high60, 2) if high60 is not None else None,
        'low60': round(low60, 2) if low60 is not None else None,
        'drawdown60': round(drawdown60, 2) if drawdown60 is not None else None,
        'range_position60': round(range_position60, 1) if range_position60 is not None else None,
        'volatility20': round(volatility20, 2) if volatility20 is not None else None,
        'ret5': round(r5, 2) if r5 is not None else None,
        'ret10': round(r10, 2) if r10 is not None else None,
        'ret20': round(r20, 2) if r20 is not None else None,
        'rsi14': round(rsi, 1) if rsi is not None else None,
        'rsi14_weekly': round(rsi_weekly, 1) if rsi_weekly is not None else None,
        'score': total, 'score_max': 100,
        'data_points': len(vals), 'rows_received': len(rows),
        'candle_signal': candle_signal,
        'breakout_signal': breakout,
        'signal_icons': icons,
        'chart_history': chart_history,
        'interpretation': {
            'vs5': deviation_text(d5, '5日MA'), 'vs20': deviation_text(d20, '20日MA'), 'vs25': deviation_text(d25, '25日MA'), 'vs75': deviation_text(d75, '75日MA'), 'vs200': deviation_text(d200, '200日MA'),
            'rsi': rsi_text, 'rsi_daily': rsi_text, 'rsi_weekly': (f'RSI {rsi_weekly:.1f}（売られ過ぎ）' if rsi_weekly is not None and rsi_weekly < 30 else f'RSI {rsi_weekly:.1f}（過熱警戒）' if rsi_weekly is not None and rsi_weekly > 70 else f'RSI {rsi_weekly:.1f}（中立）' if rsi_weekly is not None else '判定不可'), 'volume': volume_text, 'range60': range_text, 'momentum': momentum_text,
        },
        'diagnostic': {
            'usable_points': len(vals), 'rsi_ready': len(vals) >= 15, 'weekly_rsi_ready': len(weekly_vals) >= 15, 'weekly_points': len(weekly_vals),
            'ma20_ready': len(vals) >= 20, 'ma25_ready': len(vals) >= 25, 'ma75_ready': len(vals) >= 75, 'ma200_ready': len(vals) >= 200,
        },
        'breadth': (breadth_info or {}).get('breadth') or {'6d': None, '10d': None, '15d': None, '25d': None},
        'breadth_points': bp, 'breadth_breakdown': bdetail,
        'nikkei_change': nikkei_change,
        'nikkei_value': (breadth_info or {}).get('nikkei_value'),
        'relative_strength': round(rel, 2) if rel is not None else None,
        'supply': {**(supply_info or {'date': None, 'status': 'unavailable'}), 'status': sstatus, 'reason': sreason},
        'supply_status': sstatus,
        'supply_reason': sreason,
        'supply_points': sp, 'supply_breakdown': sdetail,
        'relative_points': relp,
        'score_breakdown': {
            'daily_drop': daily, 'volume': vol, 'vs20': weak,
            'rsi14': rp, 'vs75': tp, 'breadth': bp,
            'relative_strength': relp, 'supply': sp,
        },
        'breadth_source': (breadth_info or {}).get('source_url'),
        'breadth_error': (breadth_info or {}).get('error'),
    }


# Market breadth is fetched once. Failure is non-fatal because market context is
# never used as a hard buy veto.
breadth_cache = None
breadth_error = None
supply_batch_cache = {}
supply_batch_errors = []

out = {
    'updated_at': now_jst().isoformat(),
    'source': 'IRBANK API + 豆腐ハードボイルド（騰落銘柄数）',
    'api_strategy': 'price:up to 500 rows/stock with cursor pagination as needed + supply:5 metric calls/stock (rate-limited, exact-code matched) + breadth:1 call/run',
    'stocks': {},
    'diagnostics': [],
}

# Supply is fetched once per metric for the whole watchlist (5 calls total).
try:
    # Target date is based on the latest available price date from the first stock.
    probe_code = normalize_security_code(watch[0].get('code') if isinstance(watch[0], dict) else watch[0])
    probe_rows, _ = get_all_prices(probe_code)
    probe_date = probe_rows[0]['date']
    supply_batch_cache, supply_batch_errors = fetch_supply_batch(probe_date, watch)
except Exception as e:
    supply_batch_cache = {}
    supply_batch_errors = [str(e)]

for item in watch:
    if isinstance(item, str):
        code = normalize_security_code(item)
        name, industry = code, None
    else:
        code = normalize_security_code(item.get('code'))
        name = str(item.get('name') or code or '').strip()
        industry = item.get('industry')

    if not code:
        continue

    try:
        rows, attribution = get_all_prices(code)
        target_date = rows[0]['date']

        if breadth_cache is None and breadth_error is None:
            try:
                breadth_cache = fetch_breadth_and_nikkei(target_date)
            except Exception as e:
                breadth_error = str(e)
                breadth_cache = None

        supply_info = supply_batch_cache.get(code) or {
            'date': target_date, 'status': 'unavailable',
            'error': '; '.join(supply_batch_errors) if supply_batch_errors else 'supply batch unavailable',
            'source_url': 'https://api.irbank.net/v1/screening'
        }

        s = calc(rows, breadth_cache, supply_info)
        regime = classify_regime(rows, s.get('candle_signal'))
        decision = decide(s, regime)
        s.update(regime)
        s.update(decision)
        s.update({
            'code': code, 'name': name, 'industry': industry,
            'attribution': attribution,
        })
        out['stocks'][code] = s
        out['diagnostics'].append({
            'code': code, 'status': 'ok', 'rows_received': len(rows),
            'data_points': s['data_points'], 'date': s['date'],
            'regime': s.get('regime'), 'signal': s.get('signal'),
            'relative_strength': s['relative_strength'],
            'supply_status': s.get('supply', {}).get('status', 'ok'),
        })
    except Exception as e:
        out['stocks'][code] = {'code': code, 'name': name, 'industry': industry, 'error': str(e)}
        out['diagnostics'].append({'code': code, 'status': 'error', 'error': str(e)})

out['breadth_status'] = 'ok' if breadth_cache else 'unavailable'
out['breadth_error'] = breadth_error
out['successful_stocks'] = sum(1 for s in out['stocks'].values() if s.get('price') is not None)
out['failed_stocks'] = len(out['stocks']) - out['successful_stocks']

if out['successful_stocks'] == 0:
    raise SystemExit('No stock data calculated: ' + json.dumps(out['diagnostics'], ensure_ascii=False))

os.makedirs('data', exist_ok=True)
with open('data/stocks.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(json.dumps({
    'updated_at': out['updated_at'],
    'successful_stocks': out['successful_stocks'],
    'failed_stocks': out['failed_stocks'],
    'breadth_status': out['breadth_status'],
    'api_strategy': out['api_strategy'],
}, ensure_ascii=False, indent=2))
