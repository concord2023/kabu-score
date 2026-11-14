import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import re
import calendar
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

from regime_model_v2 import classify_regime, decide
import market_data
from market_data import IRBankRateLimitError, TableParser, check_api_usage, collect_per_data, fetch_breadth_and_nikkei, fetch_historical_per, fetch_supply_batch, fetch_yahoo_forecast_per, fetch_yahoo_forward_pe_timeseries, fetch_yahoo_pbr_dividend, get, get_all_prices, normalize_security_code, now_jst, resolve_security_query, screening_metric
from forecast_parsers import _parse_yahoo_forecast_text, _parse_yahoo_pbr_dividend_text, fiscal_year_end_date
from indicators import pct, signal_icons, rsi14, rsi14_wilder, ema_series, macd_series, weekly_closes, monthly_closes, monthly_macd_bottom_signal, breadth_points, relative_points, candle_reversal_signals, breakout_signal, bollinger, bollinger_sigma_position, bollinger_signal, build_period_chart_history
from calc import calc, supply_points, supply_status

def require_api_key():
    """Fail fast when IRBANK_API_KEY is not set.

    Until Phase B4 this check ran at import time; it now runs only from
    entry points so importing update_data has no side effects.
    """
    if not market_data.TOKEN:
        raise SystemExit('IRBANK_API_KEY is not set')


CUSTOM_WATCHLIST = 'data/custom_watchlist.json'
EXCLUDED_WATCHLIST = 'data/watchlist_exclusions.json'

# Effective watchlist state. Populated by load_watchlist(); importing this
# module no longer reads or writes watchlist.json.
watch_data = {'stocks': []}
watch = []
excluded_codes = set()


def load_watchlist():
    """Load watchlist.json, merge custom additions, apply exclusions, and
    materialize the effective list back into watchlist.json.

    This is exactly the sequence that used to run at import time (Phase B4).
    Returns watch_data and updates the module globals watch / excluded_codes.
    """
    global watch_data, watch, excluded_codes
    with open('watchlist.json', encoding='utf-8') as f:
        watch_data = json.load(f)
    watch = watch_data.get('stocks', [])

    # User-added stocks are kept separately so replacing the source ZIP does not
    # silently erase additions that were made through the app/GitHub workflow.
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

    # Base-watchlist deletions are kept separately from the source ZIP so a source
    # replacement does not silently re-add a stock the user intentionally removed.
    try:
        with open(EXCLUDED_WATCHLIST, encoding='utf-8') as f:
            excluded_codes = {re.sub(r'[^0-9A-Z]', '', str(x).strip().upper()) for x in json.load(f).get('codes', [])}
    except (FileNotFoundError, json.JSONDecodeError):
        excluded_codes = set()
    if excluded_codes:
        watch = [x for x in watch if re.sub(r'[^0-9A-Z]', '', str(x.get('code') if isinstance(x, dict) else x).strip().upper()) not in excluded_codes]
    watch_data['stocks'] = watch

    # Materialize the effective watchlist before analysis. Custom additions are
    # stored separately under data/ so they survive source ZIP replacement, but
    # rank_decisions.py and the public app read watchlist.json. Without persisting
    # the merged list here, an already-present custom addition can be analyzed in
    # update_data.py yet never reach watchlist.json, making the Issue appear
    # successful while the newly added stock remains invisible to the app.
    _base_watch = json.loads(Path('watchlist.json').read_text(encoding='utf-8')).get('stocks', [])
    if watch != _base_watch:
        with open('watchlist.json', 'w', encoding='utf-8') as f:
            json.dump(watch_data, f, ensure_ascii=False, indent=2)
    return watch_data



# Optional manual stock addition from GitHub Actions workflow_dispatch.
# The input may be either a security code (e.g. 6323) or a company name
# (e.g. ローツェ). IRBANK resolves the official code/name automatically.
def apply_stock_change_from_env():
    """Apply DELETE_STOCK_QUERY / ADD_STOCK_QUERY from the environment.

    This is exactly the sequence that used to run at import time (Phase B4):
    deletion first, then addition, persisting watchlist.json and the
    custom/exclusion sidecar files. Called from main() before analysis so
    the daily workflow's Issue/dispatch-driven stock changes still take
    effect in the same run.
    """
    global watch, watch_data
    delete_query = os.environ.get('DELETE_STOCK_QUERY', os.environ.get('DELETE_STOCK_CODE', '')).strip()
    if delete_query:
        delete_code = re.sub(r'[^0-9A-Z]', '', delete_query.upper())
        if not re.fullmatch(r'[0-9A-Z]{4,5}', delete_code):
            raise SystemExit(f'削除対象の証券コードが不正です: {delete_query}')
        before = len(watch)
        watch = [x for x in watch if re.sub(r'[^0-9A-Z]', '', str(x.get('code') if isinstance(x, dict) else x).strip().upper()) != delete_code]
        removed = before != len(watch)
        watch_data['stocks'] = watch
        Path(EXCLUDED_WATCHLIST).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(EXCLUDED_WATCHLIST, encoding='utf-8') as f:
                excluded_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            excluded_data = {'codes': []}
        excluded = {re.sub(r'[^0-9A-Z]', '', str(x).strip().upper()) for x in excluded_data.get('codes', [])}
        excluded.add(delete_code)
        excluded_data['codes'] = sorted(excluded)
        with open(EXCLUDED_WATCHLIST, 'w', encoding='utf-8') as f:
            json.dump(excluded_data, f, ensure_ascii=False, indent=2)
        # If it was an app-added stock, remove it from the persistent custom list too.
        try:
            with open(CUSTOM_WATCHLIST, encoding='utf-8') as f:
                custom_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            custom_data = {'stocks': []}
        custom_data['stocks'] = [x for x in custom_data.get('stocks', []) if re.sub(r'[^0-9A-Z]', '', str(x.get('code') if isinstance(x, dict) else x).strip().upper()) != delete_code]
        Path(CUSTOM_WATCHLIST).parent.mkdir(parents=True, exist_ok=True)
        with open(CUSTOM_WATCHLIST, 'w', encoding='utf-8') as f:
            json.dump(custom_data, f, ensure_ascii=False, indent=2)
        with open('watchlist.json', 'w', encoding='utf-8') as f:
            json.dump(watch_data, f, ensure_ascii=False, indent=2)
        print(f'Deleted stock from watchlist: {delete_code} (found={removed})')

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
        if add_code in excluded_codes:
            excluded_codes.discard(add_code)
            Path(EXCLUDED_WATCHLIST).parent.mkdir(parents=True, exist_ok=True)
            with open(EXCLUDED_WATCHLIST, 'w', encoding='utf-8') as f:
                json.dump({'codes': sorted(excluded_codes)}, f, ensure_ascii=False, indent=2)
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



def load_cached_supply(code, max_age_days=10, reference_date=None):
    """Reuse weekly supply data when it is fresh relative to the latest price date.

    Supply is weekly, while the price target is a trading day.  The old caller
    compared a weekly date directly with the daily target date using ``>=``; a
    Friday supply row therefore became stale on the next trading day even though
    it was still only a few days old.  Use an age window instead, and never accept
    a future-dated cache.
    """
    try:
        with open("data/stocks.json", encoding="utf-8") as f:
            payload = json.load(f)
        stock = (payload.get("stocks") or {}).get(str(code)) or {}
        supply = stock.get("supply") or {}
        supply_date = supply.get("date")
        if not supply_date:
            return None
        # Accept ISO dates and ISO datetimes.
        cached = str(supply_date)[:10]
        cached_dt = datetime.fromisoformat(cached).date()
        ref_dt = reference_date
        if ref_dt:
            ref_dt = datetime.fromisoformat(str(ref_dt)[:10]).date()
        else:
            ref_dt = now_jst().date()
        age_days = (ref_dt - cached_dt).days
        if age_days < 0 or age_days > max_age_days:
            return None
        if not any(supply.get(k) is not None for k in (
            "marginBuyBalance", "marginSellBalance", "marginRatio",
            "buy_balance", "sell_balance", "credit_ratio"
        )):
            return None
        return supply
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return None




def has_fresh_chart_data(stock, latest_market_date):
    """Return True only when the saved stock metrics and chart reach the probed market date."""
    if not isinstance(stock, dict) or stock.get('price') is None or stock.get('data_stale'):
        return False
    expected = str(latest_market_date or '')
    stock_date = str(stock.get('date') or '')
    history = stock.get('chart_history') or []
    chart_date = str(history[-1].get('date') or '') if history else ''
    return bool(expected and stock_date >= expected and chart_date >= expected)


def main():
    # Keep the previous successful per-stock analysis available as a safety net.
    # A transient fetch failure must never be converted into the legitimate
    # classification state UNKNOWN/判定不能.  If a stock cannot be refreshed,
    # retain its last complete classification and mark it stale instead.
    require_api_key()
    load_watchlist()
    apply_stock_change_from_env()

    previous_stocks = {}
    try:
        with open('data/stocks.json', encoding='utf-8') as f:
            previous_stocks = (json.load(f).get('stocks') or {})
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        previous_stocks = {}

    # Market breadth is fetched once. Failure is non-fatal because market context is
    # never used as a hard buy veto.
    breadth_cache = None
    breadth_error = None
    supply_batch_cache = {}
    supply_batch_errors = []
    run_started = time.monotonic()

    out = {
        'updated_at': now_jst().isoformat(),
        'latest_market_date': None,
        'source': 'IRBANK API + 豆腐ハードボイルド（騰落銘柄数）',
        'api_strategy': 'price:800-row target/stock; weekly margin:1 endpoint/stock only when cache is stale; valuations:20 fiscal years/stock; Yahoo company forecast PER/EPS/stock; breadth:1/run; monthly MACD:1000-row target only for weekly-RSI<=30; quota preflight via /usage',
        'stocks': {},
        'diagnostics': [],
    }

    check_api_usage(len(watch))

    # Probe one price series first. This gives us the actual latest trading date
    # so cached weekly supply can be compared against the market date.
    probe_code = normalize_security_code(watch[0].get('code') if isinstance(watch[0], dict) else watch[0]) if watch else None
    probe_rows = []
    probe_attribution = {}
    if probe_code:
        probe_rows, probe_attribution = get_all_prices(probe_code, minimum=260)
        probe_date = probe_rows[0]['date']
    else:
        probe_date = now_jst().date().isoformat()
    out['latest_market_date'] = probe_date

    cached_supply = {}
    stale_supply_items = []
    for item in watch:
        code0 = normalize_security_code(item.get('code') if isinstance(item, dict) else item)
        if not code0:
            continue
        cached = load_cached_supply(code0, max_age_days=10, reference_date=probe_date)
        if cached is not None:
            cached_supply[code0] = cached
        else:
            stale_supply_items.append(item)

    supply_batch_cache = dict(cached_supply)
    if stale_supply_items:
        fresh_supply, supply_batch_errors = fetch_supply_batch(probe_date, stale_supply_items)
        supply_batch_cache.update(fresh_supply)
    print(f'Daily setup: watch={len(watch)} cache_supply={len(cached_supply)} refresh_supply={len(stale_supply_items)} requests={market_data._request_count} elapsed={time.monotonic()-run_started:.1f}s')

    for idx, item in enumerate(watch, start=1):
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
            # Fast path: normal daily analysis only needs enough history for the
            # 200MA and the daily/weekly indicators.  The expensive long-history
            # fetch for monthly MACD is only done for strict bottom-signal candidates.
            if code == probe_code and probe_rows:
                rows, attribution = probe_rows, probe_attribution
            else:
                rows, attribution = get_all_prices(code, minimum=800)
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
                'source_url': f'https://api.irbank.net/v1/securities/{code}/weekly-margin-balance'
            }

            s = calc(rows, breadth_cache, supply_info)

            # Monthly MACD needs a much longer history.  Only stocks that pass the
            # strict weekly-RSI<=30 gate are expanded, so the usual daily update
            # stays fast while the bottom signal remains fully evaluated.
            if s.get('rsi14_weekly') is not None and s.get('rsi14_weekly') <= 30:
                long_rows, long_attribution = get_all_prices(code, minimum=1000)
                rows = long_rows
                attribution = long_attribution or attribution
                s = calc(rows, breadth_cache, supply_info)
                target_date = rows[0]['date']

            regime = classify_regime(rows, s.get('candle_signal'), s.get('breakout_signal'))
            # The decision model uses recent lows/support levels to distinguish
            # a healthy deep pullback from a broken trend. Keep raw rows internal
            # to the decision step only; never serialize them into stocks.json.
            s['_rows'] = rows
            try:
                decision = decide(s, regime)
            finally:
                s.pop('_rows', None)
            s.update(regime)
            s.update(decision)
            try:
                per_data = collect_per_data(code)
            except Exception as per_error:
                per_data = {
                    'actual_history': [], 'per_5y_avg': None, 'actual_attribution': None,
                    'forecast': {'forecast_pe': None, 'forecast_eps': None, 'switch_date': None,
                                 'source': 'Yahoo!ファイナンス（会社予想PER/EPS）',
                                 'switch_date_basis': '取得できず'},
                    'forecast_error': str(per_error), 'updated_at': now_jst().isoformat(),
                }
            valuation_data = fetch_yahoo_pbr_dividend(code)
            # Forecast PER can always be reconstructed from current price /
            # company-forecast EPS, even if Yahoo omits the displayed PER.
            if per_data['forecast'].get('forecast_pe') is None and per_data['forecast'].get('forecast_eps') is not None and s.get('price'):
                per_data['forecast']['forecast_pe'] = round(float(s['price']) / float(per_data['forecast']['forecast_eps']), 3)
            s.update({
                'code': code, 'name': name, 'industry': industry,
                'attribution': attribution,
                'per_history': per_data['actual_history'],
                'per_5y_avg': per_data['per_5y_avg'],
                'per_forecast': per_data['forecast'],
                'per_forecast_error': per_data['forecast_error'],
                'per_updated_at': per_data['updated_at'],
                'per_actual_attribution': per_data['actual_attribution'],
                'pbr': valuation_data.get('pbr'),
                'dividend_yield': valuation_data.get('dividend_yield'),
                'valuation_source': valuation_data.get('source'),
                'valuation_error': valuation_data.get('error'),
            })
            out['stocks'][code] = s
            out['diagnostics'].append({
                'code': code, 'status': 'ok', 'rows_received': len(rows),
                'data_points': s['data_points'], 'date': s['date'],
                'regime': s.get('regime'), 'signal': s.get('signal'),
                'relative_strength': s['relative_strength'],
                'supply_status': s.get('supply', {}).get('status', 'ok'),
                'per_actual_count': len(s.get('per_history') or []),
                'per_forecast_eps': (s.get('per_forecast') or {}).get('forecast_eps'),
                'per_forecast_pe': (s.get('per_forecast') or {}).get('forecast_pe'),
                'per_error': s.get('per_forecast_error'),
            })
        except Exception as e:
            # Do not turn an ordinary refresh failure into 判定不能.  判定不能 is
            # reserved for a successfully calculated stock whose directional
            # classification remains UNKNOWN after all priority rules are applied.
            # When yesterday/previous-run data exists, keep that complete analysis
            # visible and explicitly mark it as stale so the row does not disappear
            # or change meaning merely because one external request failed.
            previous = previous_stocks.get(code) or {}
            if previous.get('price') is not None and previous.get('regime') is not None:
                stale = dict(previous)
                stale['data_stale'] = True
                stale['data_error'] = str(e)
                out['stocks'][code] = stale
                out['diagnostics'].append({
                    'code': code, 'status': 'stale', 'error': str(e),
                    'previous_date': previous.get('date'),
                    'regime': previous.get('regime'), 'signal': previous.get('signal'),
                })
            else:
                # No prior complete analysis exists: this is a genuine data error,
                # not a directional classification. Keep the explicit error state.
                out['stocks'][code] = {
                    'code': code, 'name': name, 'industry': industry,
                    'error': str(e), 'data_stale': False
                }
                out['diagnostics'].append({'code': code, 'status': 'error', 'error': str(e)})
        print(f'Daily progress: {idx}/{len(watch)} {code} requests={market_data._request_count} elapsed={time.monotonic()-run_started:.1f}s')

    out['breadth_status'] = 'ok' if breadth_cache else 'unavailable'
    out['breadth_error'] = breadth_error
    out['successful_stocks'] = sum(
        1 for s in out['stocks'].values()
        if has_fresh_chart_data(s, probe_date)
    )
    out['stale_stocks'] = sum(1 for s in out['stocks'].values() if s.get('data_stale'))
    out['market_date_mismatch_stocks'] = sum(
        1 for s in out['stocks'].values()
        if s.get('price') is not None and not s.get('data_stale')
        and not has_fresh_chart_data(s, probe_date)
    )
    out['failed_stocks'] = len(out['stocks']) - out['successful_stocks']

    if out['successful_stocks'] == 0:
        raise SystemExit('No stock data calculated: ' + json.dumps(out['diagnostics'], ensure_ascii=False))

    os.makedirs('data', exist_ok=True)
    out['timing'] = {
        'total_seconds': round(time.monotonic() - run_started, 1),
        'api_requests': market_data._request_count,
        'rate_limit_wait_seconds': round(market_data._rate_wait_seconds, 1),
        'retry_count': market_data._retry_count,
        'http_seconds': round(market_data._http_seconds, 1),
    }
    out['irbank_usage_at_start'] = market_data._api_usage
    with open('data/stocks.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        'updated_at': out['updated_at'],
        'latest_market_date': out['latest_market_date'],
        'successful_stocks': out['successful_stocks'],
        'stale_stocks': out['stale_stocks'],
        'market_date_mismatch_stocks': out['market_date_mismatch_stocks'],
        'failed_stocks': out['failed_stocks'],
        'breadth_status': out['breadth_status'],
        'api_strategy': out['api_strategy'],
        'timing': out['timing'],
    }, ensure_ascii=False, indent=2))



if __name__ == '__main__':
    main()
