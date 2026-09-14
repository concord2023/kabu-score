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

from regime_model_v2 import classify_regime, decide

API = 'https://api.irbank.net/v1'
TOKEN = os.environ.get('IRBANK_API_KEY')
if not TOKEN:
    raise SystemExit('IRBANK_API_KEY is not set')

with open('watchlist.json', encoding='utf-8') as f:
    watch = json.load(f).get('stocks', [])

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


def get_all_prices(code, minimum=320):
    # 500 rows is enough for the current 320-row target in one request.
    data = get(f'/securities/{code}/prices', {'limit': 500})
    rows = data.get('prices') or []
    attribution = data.get('attribution') or {}
    if not rows:
        raise ValueError(f'No price rows returned for {code}')

    seen = set()
    clean = []
    for x in rows:
        d = x.get('date')
        if not d or d in seen:
            continue
        if x.get('close') is None and x.get('adj_close') is None:
            continue
        seen.add(d)
        clean.append(x)
    clean.sort(key=lambda x: x['date'], reverse=True)
    if len(clean) < minimum:
        raise ValueError(f'Not enough price history for {code}: {len(clean)} rows')
    return clean, attribution


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


def screening_metric(name, target_date, field):
    """Read one current screening metric for one stock.

    /screening returns the requested sort_by metric in metrics[]. Keeping this
    to two calls per stock avoids the old 7-calls-per-stock burst that caused
    the 429 error in the published app.
    """
    data = get('/screening', {
        'name': name,
        'sort_by': field,
        'sort_order': 'desc',
        'as_of': target_date,
        'limit': 10,
    })
    matches = data.get('securities') or []
    exact = next((x for x in matches if x.get('name') == name), None)
    match = exact or (matches[0] if matches else None)
    if not match:
        return None, None
    for metric in match.get('metrics') or []:
        if metric.get('field') == field:
            return metric.get('value'), metric.get('as_of')
    return None, None


def fetch_supply(code, target_date, security_name=None):
    """Fetch practical supply/demand context without the old burst of 7 calls.

    We keep five low-frequency screening metrics: buy/sell balances, their
    week-over-week changes, and margin ratio. These are context indicators;
    they are not a standalone BUY trigger.
    """
    name = security_name or code
    fields = [
        'marginBuyBalance', 'marginSellBalance',
        'marginBuyBalanceChangeWow', 'marginSellBalanceChangeWow',
        'marginRatio'
    ]
    result = {}
    errors = []
    for field in fields:
        try:
            data = get('/screening', {
                'name': name, 'sort_by': field, 'sort_order': 'desc',
                'as_of': target_date, 'limit': 100
            })
            matches = data.get('securities') or []
            match = next((x for x in matches if x.get('name') == name), None)
            if match is None and matches:
                match = matches[0]
            if match:
                for m in match.get('metrics') or []:
                    result[m.get('field')] = m.get('value')
                    result[m.get('field') + '_as_of'] = m.get('as_of')
        except Exception as e:
            errors.append(f'{field}: {e}')

    if not result:
        raise RuntimeError('IRBANK API supply metrics unavailable: ' + ('; '.join(errors) if errors else 'no matching metrics'))

    return {
        'date': result.get('marginBuyBalance_as_of') or result.get('marginRatio_as_of') or target_date,
        'buy_balance': result.get('marginBuyBalance'),
        'sell_balance': result.get('marginSellBalance'),
        'credit_ratio': result.get('marginRatio'),
        'buy_change': result.get('marginBuyBalanceChangeWow'),
        'sell_change': result.get('marginSellBalanceChangeWow'),
        'source_url': 'https://api.irbank.net/v1/screening',
        'source_note': 'IRBANK API スクリーニング（信用買い残・信用売り残・前週比・信用倍率）',
        'api_partial': bool(errors), 'api_errors': errors
    }


def supply_points(s):
    """Experimental context score. Visible to the user; not a BUY gate."""
    if not s:
        return 0, {}
    p = 0
    detail = {}
    bc, sc, r = s.get('buy_change'), s.get('sell_change'), s.get('credit_ratio')
    detail['buy_balance_change'] = '減少（改善方向）' if bc is not None and bc < 0 else '増加（重い方向）' if bc is not None and bc > 0 else '横ばい/不明'
    detail['sell_balance_change'] = '増加（改善方向）' if sc is not None and sc > 0 else '減少（支え弱化）' if sc is not None and sc < 0 else '横ばい/不明'
    if bc is not None and bc < 0: p += 4
    if sc is not None and sc > 0: p += 3
    detail['credit_ratio_level'] = r
    if r is not None:
        p += 5 if r <= 3 else 3 if r <= 6 else 1 if r <= 10 else 0
    return min(p, 12), detail


def supply_status(s):
    if not s or s.get('credit_ratio') is None:
        return 'データ不足', '信用倍率データが取得できないため需給判定は保留。'
    r = float(s['credit_ratio'])
    bc, sc = s.get('buy_change'), s.get('sell_change')
    if bc is not None and bc > 0 and r >= 6:
        return '重い', f'信用倍率{r:.2f}倍で、買い残も前週比増加。上値の重さに注意。'
    if bc is not None and bc < 0 and (sc is None or sc >= 0):
        return '改善方向', f'信用倍率{r:.2f}倍。買い残が前週比減少して需給は改善方向。'
    if r >= 10:
        return '重い', f'信用倍率{r:.2f}倍と高め。買い残の整理が進むかを確認。'
    if r <= 3:
        return '軽い', f'信用倍率{r:.2f}倍で、買い残の偏りは比較的小さい。'
    return '中立', f'信用倍率{r:.2f}倍。需給だけでは方向を決めにくい。'

def pct(a, b):
    return ((a / b) - 1) * 100 if a is not None and b not in (None, 0) else None


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

    ma5 = statistics.mean(vals[1:6]) if len(vals) >= 6 else None
    ma20 = statistics.mean(vals[1:21]) if len(vals) >= 21 else None
    ma60 = statistics.mean(vals[1:61]) if len(vals) >= 61 else None
    high20 = max(vals[1:21]) if len(vals) >= 21 else None
    low20 = min(vals[1:21]) if len(vals) >= 21 else None
    high60 = max(vals[1:61]) if len(vals) >= 61 else None
    low60 = min(vals[1:61]) if len(vals) >= 61 else None
    r5 = pct(vals[0], vals[5]) if len(vals) > 5 else None
    r10 = pct(vals[0], vals[10]) if len(vals) > 10 else None
    r20 = pct(vals[0], vals[20]) if len(vals) > 20 else None
    rsi = rsi14(vals)
    d5 = pct(vals[0], ma5)
    d20 = pct(vals[0], ma20)
    d60 = pct(vals[0], ma60)
    drawdown60 = pct(vals[0], high60)
    range_position60 = ((vals[0]-low60)/(high60-low60)*100) if high60 is not None and low60 is not None and high60 != low60 else None
    volatility20 = (statistics.stdev(vals[:20]) / statistics.mean(vals[:20]) * 100) if len(vals) >= 20 and statistics.mean(vals[:20]) else None

    daily = 15 if change is not None and change <= -7 else 12 if change is not None and change <= -5 else 9 if change is not None and change <= -3 else 6 if change is not None and change <= -2 else 3 if change is not None and change <= -1 else 0
    vol = 10 if vr is not None and vr >= 1.8 else 8 if vr is not None and vr >= 1.5 else 6 if vr is not None and vr >= 1.3 else 3 if vr is not None and vr >= 1.15 else 0
    weak = 10 if d20 is not None and d20 <= -12 else 8 if d20 is not None and d20 <= -8 else 6 if d20 is not None and d20 <= -5 else 3 if d20 is not None and d20 <= -3 else 0
    rp = 10 if rsi is not None and rsi <= 25 else 8 if rsi is not None and rsi <= 30 else 5 if rsi is not None and rsi <= 35 else 2 if rsi is not None and rsi <= 40 else 0
    tp = 10 if d60 is not None and d60 <= -10 else 7 if d60 is not None and d60 <= -5 else 4 if d60 is not None and d60 <= 0 else 0

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
    elif rsi <= 30: rsi_text = '売られ過ぎ寄り'
    elif rsi <= 40: rsi_text = '弱め'
    elif rsi < 60: rsi_text = '中立'
    elif rsi < 70: rsi_text = '強め'
    else: rsi_text = '過熱警戒'
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

    # Keep a compact recent history for the UI chart.  The chart is intentionally
    # presentation data only; decisions continue to use the full calculation above.
    chart_history = []
    for idx, r in enumerate(rows[:61]):
        raw_close = r.get('adj_close') if r.get('adj_close') is not None else r.get('close')
        if raw_close is None: continue
        close_i = float(raw_close)
        prev20 = [float((z.get('adj_close') if z.get('adj_close') is not None else z.get('close'))) for z in rows[idx+1:idx+21]
                  if (z.get('adj_close') if z.get('adj_close') is not None else z.get('close')) is not None]
        prev60 = [float((z.get('adj_close') if z.get('adj_close') is not None else z.get('close'))) for z in rows[idx+1:idx+61]
                  if (z.get('adj_close') if z.get('adj_close') is not None else z.get('close')) is not None]
        chart_history.append({
            'date': r.get('date'), 'close': round(close_i,2),
            'ma20': round(statistics.mean(prev20),2) if len(prev20)>=20 else None,
            'ma60': round(statistics.mean(prev60),2) if len(prev60)>=60 else None,
        })
    chart_history.reverse()

    return {
        'date': rows[0].get('date'), 'price': close,
        'change': round(change, 2) if change is not None else None,
        'volume': vols[0] if vols else None,
        'volume_ratio': round(vr, 2) if vr is not None else None,
        'ma5': round(ma5, 2) if ma5 is not None else None,
        'ma20': round(ma20, 2) if ma20 is not None else None,
        'ma60': round(ma60, 2) if ma60 is not None else None,
        'vs5': round(d5, 2) if d5 is not None else None,
        'vs20': round(d20, 2) if d20 is not None else None,
        'vs60': round(d60, 2) if d60 is not None else None,
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
        'score': total, 'score_max': 100,
        'data_points': len(vals), 'rows_received': len(rows),
        'candle_signal': candle_signal,
        'breakout_signal': breakout,
        'chart_history': chart_history,
        'interpretation': {
            'vs5': deviation_text(d5, '5日MA'), 'vs20': deviation_text(d20, '20日MA'), 'vs60': deviation_text(d60, '60日MA'),
            'rsi': rsi_text, 'volume': volume_text, 'range60': range_text, 'momentum': momentum_text,
        },
        'diagnostic': {
            'usable_points': len(vals), 'rsi_ready': len(vals) >= 15,
            'ma20_ready': len(vals) >= 21, 'ma60_ready': len(vals) >= 61,
        },
        'breadth': (breadth_info or {}).get('breadth') or {'6d': None, '10d': None, '15d': None, '25d': None},
        'breadth_points': bp, 'breadth_breakdown': bdetail,
        'nikkei_change': nikkei_change,
        'nikkei_value': (breadth_info or {}).get('nikkei_value'),
        'relative_strength': round(rel, 2) if rel is not None else None,
        'supply': supply_info or {'date': None, 'status': 'unavailable'},
        'supply_points': sp, 'supply_breakdown': sdetail,
        'relative_points': relp,
        'score_breakdown': {
            'daily_drop': daily, 'volume': vol, 'vs20': weak,
            'rsi14': rp, 'vs60': tp, 'breadth': bp,
            'relative_strength': relp, 'supply': sp,
        },
        'breadth_source': (breadth_info or {}).get('source_url'),
        'breadth_error': (breadth_info or {}).get('error'),
    }


# Market breadth is fetched once. Failure is non-fatal because market context is
# never used as a hard buy veto.
breadth_cache = None
breadth_error = None

out = {
    'updated_at': now_jst().isoformat(),
    'source': 'IRBANK API + 豆腐ハードボイルド（騰落銘柄数）',
    'api_strategy': 'price:1 call/stock, supply:5 calls/stock, breadth:1 call/run (rate-limited)',
    'stocks': {},
    'diagnostics': [],
}

for item in watch:
    if isinstance(item, str):
        code, name, industry = item, item, None
    else:
        code = str(item.get('code'))
        name = item.get('name') or code
        industry = item.get('industry')

    try:
        rows, attribution = get_all_prices(code)
        target_date = rows[0]['date']

        if breadth_cache is None and breadth_error is None:
            try:
                breadth_cache = fetch_breadth_and_nikkei(target_date)
            except Exception as e:
                breadth_error = str(e)
                breadth_cache = None

        try:
            supply_info = fetch_supply(code, target_date, name)
        except Exception as e:
            supply_info = {'date': target_date, 'status': 'unavailable', 'error': str(e), 'source_url': 'https://api.irbank.net/v1/screening'}

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
