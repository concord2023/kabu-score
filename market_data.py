"""Market-data fetch layer: IRBANK API and public valuation sources.

Extracted from update_data.py (Phase B5). All rate-limit counters live here
and only here; update_data re-exports the public functions so existing
importers (scan_*, validate_release, tests) keep working unchanged.
Retry and quota logic is exactly as before the move — this module only
relocates the code.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from forecast_parsers import _parse_yahoo_forecast_text, _parse_yahoo_pbr_dividend_text, fiscal_year_end_date

API = 'https://api.irbank.net/v1'
TOKEN = os.environ.get('IRBANK_API_KEY')


# Keep API traffic below IRBANK's current 60 requests/minute limit.
# 1.05s means at most ~57 requests/minute, leaving a small safety margin.
MIN_REQUEST_INTERVAL = 1.05
_last_request_at = 0.0
_request_count = 0
_rate_wait_seconds = 0.0
_retry_count = 0
_http_seconds = 0.0
# Hard process ceiling for the current Free quota (200 requests/day).
# /usage is not quota-consuming, but is counted locally by get(), so the
# dynamic ceiling below includes the usage-check attempts.
MAX_API_REQUESTS = 200
_api_usage = None

def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))

def rate_limit_wait():
    global _last_request_at, _rate_wait_seconds
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
        _rate_wait_seconds += wait
    _last_request_at = time.monotonic()

class IRBankRateLimitError(RuntimeError):
    pass

def _http_error_payload(error):
    try:
        raw = error.read().decode('utf-8', errors='replace')
        return json.loads(raw) if raw else {}
    except Exception:
        return {}

def get(path, params=None, retries=4):
    global _request_count, _retry_count, _http_seconds
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(retries):
        if _request_count >= MAX_API_REQUESTS:
            raise IRBankRateLimitError(
                f'IRBANK request safety ceiling reached ({MAX_API_REQUESTS}); no further request will be sent.'
            )
        rate_limit_wait()
        _request_count += 1
        req = urllib.request.Request(
            url,
            headers={
                'Authorization': f'Bearer {TOKEN}',
                'User-Agent': 'kabu-score/11.0'
            },
        )
        try:
            request_started = time.monotonic()
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.load(r)
            _http_seconds += time.monotonic() - request_started
            return payload
        except urllib.error.HTTPError as e:
            _http_seconds += time.monotonic() - request_started
            _retry_count += 1
            last_error = e
            body = _http_error_payload(e)
            error = body.get('error') if isinstance(body, dict) else {}
            error_code = error.get('code') if isinstance(error, dict) else None
            if e.code == 429:
                # IRBANK uses 429 for both per-minute and daily limits.  The
                # response contains retry information; never blindly repeat a
                # daily-limit failure for minutes.  This makes a quota outage
                # fail fast instead of making GitHub Actions appear hung.
                retry_after = e.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = max(1.0, float(retry_after))
                    except ValueError:
                        try:
                            retry_at = parsedate_to_datetime(retry_after)
                            if retry_at.tzinfo is None:
                                retry_at = retry_at.replace(tzinfo=timezone.utc)
                            delay = max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                        except Exception:
                            delay = min(30.0, 2 ** attempt)
                    # A long Retry-After is normally the daily reset window,
                    # not a short per-minute throttle. Never leave Actions
                    # sleeping for hours or blindly replay a daily-quota 429.
                    if delay > 120:
                        raise IRBankRateLimitError(
                            f'IRBANK 429 rate_limited; Retry-After={delay:.0f}s. '
                            f'Quota/rate limit reached; stop without retry. '
                            f'error_code={error_code or "unknown"}; '
                            f'{error.get("message", str(e))}'
                        ) from e
                else:
                    delay = min(30.0, 2 ** attempt)
                if attempt == retries - 1:
                    raise IRBankRateLimitError(
                        f'IRBANK 429 rate_limited after {retries} attempts: {error_code or "unknown"}; {error.get("message", str(e))}'
                    ) from e
                time.sleep(delay)
                continue
            if e.code not in (500, 502, 503, 504):
                message = error.get('message') if isinstance(error, dict) else None
                raise RuntimeError(f'IRBANK HTTP {e.code} {error_code or ""}: {message or e}') from e
            delay = min(30, 2 ** attempt)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as e:
            _http_seconds += time.monotonic() - request_started
            _retry_count += 1
            last_error = e
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f'IRBANK request failed after retries: {url}: {last_error}')

def check_api_usage(watch_count):
    """Fail early when today's IRBANK quota cannot support a normal run."""
    global _api_usage
    usage = get('/usage', retries=2)
    _api_usage = usage if isinstance(usage, dict) else {}
    remaining = _api_usage.get('remaining')
    limit = _api_usage.get('limit')
    resets_at = _api_usage.get('resets_at')
    if remaining is None:
        raise IRBankRateLimitError(
            'IRBANK /usage から残り回数を確認できないため、安全のため更新を中止します。'
        )
    # Calculate the normal daily path from the actual implementation rather
    # than multiplying every call by all four retry attempts.  With 20 stocks:
    #   price history: 1 probe + 2 pages for each remaining stock = 39
    #   stale weekly margin cache: at most 20 calls
    #   historical PER: 20 calls
    #   monthly-MACD expansion: at most 2 pages per stock = 40 calls
    # Total upper bound = 119 quota-consuming requests (plus free /usage).
    # Keep a 20% retry reserve, and set a dynamic hard ceiling to the quota
    # remaining today.  This avoids both the old false rejection at 200/day
    # and any attempt to exceed the actual remaining quota.
    price_requests = 1 + max(0, watch_count - 1) * 2 if watch_count else 0
    supply_requests = watch_count
    valuation_requests = watch_count
    monthly_requests = watch_count * 2
    worst_case_requests = price_requests + supply_requests + valuation_requests + monthly_requests
    retry_reserve = max(10, (worst_case_requests + 4) // 5)
    minimum_needed = worst_case_requests + retry_reserve
    print(
        f'IRBANK usage: remaining={remaining}/{limit} resets_at={resets_at}; '
        f'estimated={worst_case_requests}, retry_reserve={retry_reserve}, '
        f'preflight_required={minimum_needed}'
    )
    if int(remaining) <= 0:
        raise IRBankRateLimitError(
            f'IRBANKの日次API上限に到達しています（remaining=0）。次回リセット: {resets_at}'
        )
    if int(remaining) < minimum_needed:
        raise IRBankRateLimitError(
            f'IRBANKの残りAPI枠が不足しています（remaining={remaining}）。'
            f'通常更新の最大見積り={worst_case_requests}＋再試行予備={retry_reserve}。'
            f'次回リセット: {resets_at}'
        )
    # get() counts /usage locally even though IRBANK does not charge it.
    # Therefore allow only the preflight budget (including its retry reserve),
    # never more than the remaining quota or the 200-call process ceiling.
    global MAX_API_REQUESTS
    MAX_API_REQUESTS = min(200, _request_count + min(int(remaining), minimum_needed))
    print(f'IRBANK hard request ceiling for this run: {MAX_API_REQUESTS} attempts')

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

def get_all_prices(code, minimum=800):
    # Fetch enough history for the 75/200MA plus the strict monthly-MACD bottom
    # signal. 800 sessions gives roughly 30+ monthly closes for established names.
    # Do NOT reject
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
    # ``minimum`` is a target, not a hard validity gate.  New listings can
    # legitimately have fewer than 200 sessions; calc() then exposes exactly
    # which indicators are unavailable instead of turning the whole stock into
    # a generic error.  At least 15 observations are needed for RSI(14).
    if len(rows) < 15:
        raise ValueError(f'Not enough price history for {code}: {len(rows)} rows (15 sessions required for basic RSI)')
    if len(rows) < minimum:
        print(f'Price history partial: {code} rows={len(rows)} target={minimum}; continuing with available history')
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
    with urllib.request.urlopen(req, timeout=8) as r:
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
    """Fetch weekly margin data with one API call per stock.

    IRBANK added GET /securities/{code}/weekly-margin-balance on 2026-09-16.
    It returns buy balance, sell balance, weekly changes and credit ratio in
    one response, replacing the old 5 screening requests per stock.
    """
    result = {}
    errors = []
    for item in watch_items:
        code = normalize_security_code(item.get('code') if isinstance(item, dict) else item)
        if not code:
            continue
        row = {
            'date': target_date,
            'status': 'unavailable',
            'source_url': f'https://api.irbank.net/v1/securities/{code}/weekly-margin-balance',
            'source_note': 'IRBANK API 週次信用残高（信用買い残・信用売り残・前週比・信用倍率）',
            'missing_fields': ['marginBuyBalance','marginSellBalance','marginBuyBalanceChangeWow','marginSellBalanceChangeWow','marginRatio'],
            'field_dates': {},
            'api_errors': [],
        }
        try:
            data = get(f'/securities/{code}/weekly-margin-balance', {'period': '26w'})
            weeks = data.get('weeks') or []
            usable = [w for w in weeks if w.get('report_date')]
            if not usable:
                raise ValueError('weekly margin data is empty')
            w = usable[-1]
            row['date'] = w.get('report_date') or target_date
            row['marginBuyBalance'] = w.get('buy_balance_total')
            row['marginSellBalance'] = w.get('sell_balance_total')
            row['marginBuyBalanceChangeWow'] = w.get('buy_balance_change')
            row['marginSellBalanceChangeWow'] = w.get('sell_balance_change')
            row['marginRatio'] = w.get('margin_ratio')
            mapping = {
                'marginBuyBalance': row.get('marginBuyBalance'),
                'marginSellBalance': row.get('marginSellBalance'),
                'marginBuyBalanceChangeWow': row.get('marginBuyBalanceChangeWow'),
                'marginSellBalanceChangeWow': row.get('marginSellBalanceChangeWow'),
                'marginRatio': row.get('marginRatio'),
            }
            row['missing_fields'] = [k for k,v in mapping.items() if v is None]
            row['field_dates'] = {k: row['date'] for k,v in mapping.items() if v is not None}
            row['buy_balance'] = row.get('marginBuyBalance')
            row['sell_balance'] = row.get('marginSellBalance')
            row['credit_ratio'] = row.get('marginRatio')
            row['buy_change'] = row.get('marginBuyBalanceChangeWow')
            row['sell_change'] = row.get('marginSellBalanceChangeWow')
            if row['buy_balance'] is not None and row['sell_balance'] is not None:
                row['net_balance'] = row['buy_balance'] - row['sell_balance']
            if row['buy_change'] is not None and row['sell_change'] is not None:
                row['net_change'] = row['buy_change'] - row['sell_change']
            if not row['missing_fields']:
                row['status'] = 'complete'
            elif len(row['missing_fields']) < 5:
                row['status'] = 'partial'
        except Exception as e:
            row['api_errors'].append(f'{code}: {e}')
            errors.append(row['api_errors'][-1])
        row['api_partial'] = row['status'] != 'complete'
        result[code] = row
    return result, errors

def fetch_historical_per(code):
    """Fetch the 20-fiscal-year actual PER series from IRBANK.

    IRBANK's /valuations.per is based on each fiscal period's adjusted
    closing price divided by that period's actual EPS.  This is the historical
    annual actual-PER series used by the PER chart; it is not current forecast
    EPS applied retroactively.
    """
    data = get('/valuations', {'security_code': code, 'years': 20})
    history = data.get('history') or [] if isinstance(data, dict) else []
    rows = []
    for row in history:
        if not isinstance(row, dict):
            continue
        per = row.get('per')
        fy = row.get('fiscal_year')
        if fy and isinstance(per, (int, float)) and per > 0:
            rows.append({
                'fiscal_year': str(fy),
                'date': fiscal_year_end_date(fy),
                'per': round(float(per), 3),
                'per_source': row.get('per_source'),
                'stock_price': row.get('stock_price_adjusted'),
                'eps': row.get('eps_adjusted'),
            })
    rows.sort(key=lambda x: x.get('date') or '')
    return {
        'history': rows,
        'per_5y_avg': data.get('per_5y_avg') if isinstance(data, dict) else None,
        'attribution': data.get('attribution') if isinstance(data, dict) else None,
    }

def fetch_yahoo_forward_pe_timeseries(code):
    """Fetch current Yahoo forward P/E from fundamentals-timeseries."""
    import time as _time
    code = str(code).strip().upper()
    symbol = code + '.T'
    end_ts = int(_time.time())
    start_ts = end_ts - 45 * 86400
    errors = []
    for host in ('https://query1.finance.yahoo.com', 'https://query2.finance.yahoo.com'):
        url = host + '/ws/fundamentals-timeseries/v1/finance/timeseries/' + urllib.parse.quote(symbol)
        params = {'symbol': symbol, 'type': 'trailingForwardPeRatio', 'period1': start_ts, 'period2': end_ts}
        try:
            req = urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers={'User-Agent':'Mozilla/5.0 (compatible; kabu-score/15.0)','Accept':'application/json','Referer':'https://finance.yahoo.com/'})
            with urllib.request.urlopen(req, timeout=15) as r:
                obj = json.loads(r.read().decode('utf-8','replace'))
            result = ((obj.get('timeseries') or {}).get('result') or [])
            for item in result:
                vals = item.get('trailingForwardPeRatio') or []
                for row in reversed(vals):
                    rv = row.get('reportedValue') if isinstance(row,dict) else None
                    raw = rv.get('raw') if isinstance(rv,dict) else row.get('raw') if isinstance(row,dict) else None
                    try: pe = float(raw)
                    except (TypeError,ValueError): continue
                    if pe > 0: return pe
            errors.append(host + ': unavailable')
        except Exception as e:
            errors.append(host + ': ' + str(e))
    raise RuntimeError('Yahoo forward P/E unavailable (' + '; '.join(errors) + ')')

def fetch_yahoo_forecast_per(code):
    """Read current company-forecast PER/EPS from Yahoo."""
    code = str(code).strip().upper()
    errors=[]
    try:
        pe = fetch_yahoo_forward_pe_timeseries(code)
        return {'forecast_pe':round(pe,3),'forecast_eps':None,'switch_date':None,'source':'Yahoo Finance fundamentals-timeseries（会社予想PER）','switch_date_basis':'Yahoo fundamentals-timeseries'}
    except Exception as e:
        errors.append('timeseries: '+str(e))
    for host in ('https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'):
        try:
            qurl = host + '/v7/finance/quote?' + urllib.parse.urlencode({'symbols':code+'.T'})
            req=urllib.request.Request(qurl,headers={'User-Agent':'Mozilla/5.0 (compatible; kabu-score/15.0)','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=15) as r: obj=json.loads(r.read().decode('utf-8','replace'))
            rows=((obj.get('quoteResponse') or {}).get('result') or [])
            if rows:
                q=rows[0]; pe=q.get('forwardPE'); eps=q.get('epsForward') if q.get('epsForward') is not None else q.get('forwardEps')
                if pe is not None or eps is not None:
                    pe=float(pe) if pe is not None else None; eps=float(eps) if eps is not None else None
                    if pe is None and eps and q.get('regularMarketPrice'): pe=float(q['regularMarketPrice'])/eps
                    return {'forecast_pe':round(pe,3) if pe and pe>0 else None,'forecast_eps':round(eps,3) if eps and eps>0 else None,'switch_date':None,'source':'Yahoo Finance quote API（会社予想PER/EPS）','switch_date_basis':'Yahoo quote API'}
            errors.append(host+': unavailable')
        except Exception as e: errors.append(host+': '+str(e))
    url='https://finance.yahoo.co.jp/quote/'+urllib.parse.quote(code)+'.T'; texts=[]
    for target in (url,'https://r.jina.ai/https://finance.yahoo.co.jp/quote/'+urllib.parse.quote(code)+'.T','https://r.jina.ai/http://finance.yahoo.co.jp/quote/'+urllib.parse.quote(code)+'.T'):
        try:
            req=urllib.request.Request(target,headers={'User-Agent':'Mozilla/5.0 (compatible; kabu-score/15.0)','Accept':'text/plain,text/html'})
            with urllib.request.urlopen(req,timeout=20) as r: texts.append(r.read().decode('utf-8','ignore'))
            if texts: break
        except Exception as e: errors.append('page: '+str(e))
    for raw in texts:
        pe,eps,switch_date=_parse_yahoo_forecast_text(raw)
        if pe is not None or eps is not None:
            return {'forecast_pe':round(pe,3) if pe is not None else None,'forecast_eps':round(eps,3) if eps is not None else None,'switch_date':switch_date,'source':'Yahoo!ファイナンス（会社予想PER/EPS）','switch_date_basis':'直近の決算発表日'}
    raise RuntimeError('Yahooから会社予想PER/EPSを取得できませんでした ('+'; '.join(errors)+')')

def fetch_yahoo_pbr_dividend(code):
    """Fetch current PBR and company-forecast dividend yield from Yahoo.

    Prefer the quote API, but fall back to Yahoo Japan's public quote page.
    The fallback is important because Yahoo Japan currently exposes PBR and
    company-forecast dividend yield on the quote page even when the global
    quote API omits those fields.
    """
    code = str(code).strip().upper()
    errors = []

    # Fast path: Yahoo global quote API.
    for host in ('https://query1.finance.yahoo.com', 'https://query2.finance.yahoo.com'):
        try:
            qurl = host + '/v7/finance/quote?' + urllib.parse.urlencode({'symbols': code + '.T'})
            req = urllib.request.Request(
                qurl,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; kabu-score/15.0)', 'Accept': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                obj = json.loads(r.read().decode('utf-8', 'replace'))
            rows = ((obj.get('quoteResponse') or {}).get('result') or [])
            if not rows:
                errors.append(host + ': unavailable')
                continue
            q = rows[0]
            pbr = q.get('priceToBook')
            if pbr is not None:
                try:
                    pbr = float(pbr)
                    if pbr <= 0:
                        pbr = None
                except (TypeError, ValueError):
                    pbr = None

            div_raw = q.get('dividendYield')
            if div_raw is None:
                div_raw = q.get('trailingAnnualDividendYield')
            dividend_yield = None
            if div_raw is not None:
                try:
                    div_raw = float(div_raw)
                    dividend_yield = div_raw * 100.0 if abs(div_raw) <= 1.5 else div_raw
                    if dividend_yield < 0:
                        dividend_yield = None
                except (TypeError, ValueError):
                    dividend_yield = None

            if pbr is not None or dividend_yield is not None:
                return {
                    'pbr': round(pbr, 3) if pbr is not None else None,
                    'dividend_yield': round(dividend_yield, 3) if dividend_yield is not None else None,
                    'source': 'Yahoo Finance quote API',
                }
            errors.append(host + ': PBR/dividend fields omitted')
        except Exception as e:
            errors.append(host + ': ' + str(e))

    # Reliable fallback: Yahoo Japan public quote page / Jina text relay.
    targets = (
        'https://finance.yahoo.co.jp/quote/' + urllib.parse.quote(code) + '.T',
        'https://r.jina.ai/https://finance.yahoo.co.jp/quote/' + urllib.parse.quote(code) + '.T',
        'https://r.jina.ai/http://finance.yahoo.co.jp/quote/' + urllib.parse.quote(code) + '.T',
    )
    for target in targets:
        try:
            req = urllib.request.Request(
                target,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; kabu-score/15.0)', 'Accept': 'text/plain,text/html'}
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode('utf-8', 'ignore')
            pbr, dividend_yield = _parse_yahoo_pbr_dividend_text(raw)
            if pbr is not None or dividend_yield is not None:
                return {
                    'pbr': round(pbr, 3) if pbr is not None else None,
                    'dividend_yield': round(dividend_yield, 3) if dividend_yield is not None else None,
                    'source': 'Yahoo!ファイナンス（参考指標）',
                }
            errors.append('page: PBR/dividend not found')
        except Exception as e:
            errors.append('page: ' + str(e))

    # Second fallback: MINKABU public valuation/dividend pages. Yahoo can
    # occasionally omit or block the reference-indicator fields from automated
    # requests even though they are visible in the browser. Keep this outside
    # the IRBANK quota because it is an external web source.
    try:
        valuation_url = 'https://minkabu.jp/stock/' + urllib.parse.quote(code) + '/daily_valuation'
        req = urllib.request.Request(valuation_url, headers={'User-Agent': 'Mozilla/5.0 (compatible; kabu-score/16.0)', 'Accept': 'text/html'})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode('utf-8', 'ignore')
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw)).replace('\u00a0', ' ')
        pbr_m = re.search(r'PBR\s*\(倍\)[^0-9]{0,120}([0-9]{1,6}(?:\.[0-9]+)?)', text)
        if pbr_m is None:
            pbr_m = re.search(r'PBR[^0-9]{0,120}([0-9]{1,6}(?:\.[0-9]+)?)', text)
        pbr = float(pbr_m.group(1)) if pbr_m else None
        dividend_yield = None
        # Prefer the dedicated dividend page because it exposes the current
        # company-forecast yield rather than only the historical valuation yield.
        div_url = 'https://minkabu.jp/stock/' + urllib.parse.quote(code) + '/dividend'
        req = urllib.request.Request(div_url, headers={'User-Agent': 'Mozilla/5.0 (compatible; kabu-score/16.0)', 'Accept': 'text/html'})
        with urllib.request.urlopen(req, timeout=20) as r:
            div_raw = r.read().decode('utf-8', 'ignore')
        div_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', div_raw)).replace('\u00a0', ' ')
        div_m = re.search(r'配当利回り[^%]{0,120}?([0-9]{1,4}(?:\.[0-9]+)?)\s*%', div_text)
        if div_m:
            dividend_yield = float(div_m.group(1))
        if pbr is not None or dividend_yield is not None:
            return {
                'pbr': round(pbr, 3) if pbr is not None else None,
                'dividend_yield': round(dividend_yield, 3) if dividend_yield is not None else None,
                'source': 'みんかぶ（バリュエーション／配当情報）',
            }
        errors.append('minkabu: PBR/dividend not found')
    except Exception as e:
        errors.append('minkabu: ' + str(e))

    return {
        'pbr': None,
        'dividend_yield': None,
        'source': 'Yahoo!ファイナンス／みんかぶ（参考指標）',
        'error': '; '.join(errors),
    }

def collect_per_data(code):
    """Collect all PER inputs during Daily stock update; UI only reads saved data."""
    actual = fetch_historical_per(code)
    try:
        forecast = fetch_yahoo_forecast_per(code)
        forecast_error = None
    except Exception as e:
        forecast = {
            'forecast_pe': None, 'forecast_eps': None, 'switch_date': None,
            'source': 'Yahoo!ファイナンス（会社予想PER/EPS）',
            'switch_date_basis': '取得できず',
        }
        forecast_error = str(e)
    return {
        'actual_history': actual.get('history', []),
        'per_5y_avg': actual.get('per_5y_avg'),
        'actual_attribution': actual.get('attribution'),
        'forecast': forecast,
        'forecast_error': forecast_error,
        'updated_at': now_jst().isoformat(),
    }
