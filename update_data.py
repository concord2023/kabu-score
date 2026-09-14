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
    """Detect simple bullish reversal candlestick patterns near a potential bottom.

    This is an alert layer, not a validated BUY rule. It deliberately favors
    recall: the dashboard should tell the user that a bottom/reversal pattern
    appeared so it can be checked together with weekly direction, volume and
    follow-through.
    """
    def n(v):
        try: return float(v) if v is not None else None
        except (TypeError, ValueError): return None

    def candle(i):
        r=rows[i]
        o,h,l,c=map(lambda k:n(r.get(k)),('open','high','low','close'))
        if None in (o,h,l,c): return None
        body=abs(c-o); rng=max(h-l,1e-9)
        upper=h-max(o,c); lower=min(o,c)-l
        return {'date':r.get('date'),'open':o,'high':h,'low':l,'close':c,
                'body':body,'range':rng,'upper':upper,'lower':lower,
                'bull':c>o,'bear':c<o}

    cs=[candle(i) for i in range(min(len(rows),8))]
    if not cs or cs[0] is None: return {'status':'データ不足','patterns':[], 'recent_patterns':[], 'bottom_zone':False, 'confirmation':'不明'}

    found=[]
    # Current day and previous 1-2 days: patterns are still actionable context.
    for i,c in enumerate(cs[:3]):
        if c is None: continue
        prev=cs[i+1] if i+1<len(cs) else None
        prev2=cs[i+2] if i+2<len(cs) else None
        if c['range'] and c['lower'] >= max(c['body'],0.01)*2 and c['upper'] <= max(c['body'],0.01)*1.2 and c['close'] >= c['low'] + c['range']*0.55:
            found.append(('hammer','下ヒゲの長いハンマー'))
        if c['body'] <= c['range']*0.10 and c['lower'] > c['upper']:
            found.append(('doji_lower_shadow','下ヒゲ付き小陰/十字'))
        if prev and prev['bear'] and c['bull'] and c['open'] <= prev['close'] and c['close'] >= prev['open']:
            found.append(('bullish_engulfing','陽線の包み足'))
        if prev and prev['bear'] and c['bull'] and c['close'] > (prev['open']+prev['close'])/2 and c['close'] < prev['open']:
            found.append(('piercing','切り返し（Piercing）'))
        if prev and abs(c['low']/prev['low']-1) <= 0.005 and c['bull'] and c['close'] > c['open']:
            found.append(('tweezer_bottom','毛抜き底'))
        if prev and prev2 and prev2['bear'] and prev2['body'] > prev2['range']*0.35 and abs(prev['close']-prev['open']) <= prev['range']*0.25 and c['bull'] and c['close'] > (prev2['open']+prev2['close'])/2:
            found.append(('morning_star','明けの明星型'))
        if prev and prev['bear'] and c['bull'] and c['close'] > prev['high']:
            found.append(('bullish_breakout','前日高値を上抜く陽線'))

    # Deduplicate by pattern, preserving first occurrence.
    unique=[]; seen=set()
    for code,label in found:
        if code not in seen:
            seen.add(code); unique.append({'code':code,'label':label})

    # Potential bottom zone from the most recent price context.
    closes=[]
    for r in rows[:61]:
        v=n(r.get('adj_close') if r.get('adj_close') is not None else r.get('close'))
        if v is not None: closes.append(v)
    bottom_zone=False; zone_reason='底値圏ではない/判定材料不足'
    if len(closes)>=21:
        low20=min(closes[1:21]); pos20=(closes[0]-low20)/low20*100 if low20 else None
        low60=min(closes[1:61]) if len(closes)>=61 else None
        pos60=(closes[0]-low60)/low60*100 if low60 else None
        high60=max(closes[1:61]) if len(closes)>=61 else None
        dd=(closes[0]/high60-1)*100 if high60 else None
        bottom_zone = (pos20 is not None and pos20 <= 8) or (pos60 is not None and pos60 <= 12) or (dd is not None and dd <= -20)
        if bottom_zone: zone_reason='直近安値に近く、下落後の底値圏を監視'

    current=cs[0]
    confirmation='未確認'
    if current and len(cs)>1 and cs[1]:
        if current['bull'] and current['close'] > cs[1]['high']:
            confirmation='翌日/当日フォロー確認'
        elif current['bull']:
            confirmation='反発は出たが上値確認待ち'
        else:
            confirmation='まだ陰線。反転確認待ち'

    # Only expose a signal when a bullish reversal pattern exists.
    recent = unique
    if not recent:
        status='なし'
        strength='—'
        reason='ローソク足の明確な反転パターンは直近3営業日に未検出。'
    else:
        strength='強' if bottom_zone and len(recent)>=1 else '中'
        status='大底反転サイン' if bottom_zone else '反転サイン'
        reason=f"{', '.join(x['label'] for x in recent)}。{zone_reason}。{confirmation}。"

    return {'status':status,'strength':strength,'patterns':recent,
            'recent_patterns':recent,'bottom_zone':bottom_zone,
            'zone_reason':zone_reason,'confirmation':confirmation,'reason':reason,
            'lookback_days':3}

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
        regime = classify_regime(rows)
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
