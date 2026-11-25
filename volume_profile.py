"""Volume-by-price profile built from Yahoo Finance hourly bars.

This replaces the old client-side approximation in detail.html, which dumped
each daily/weekly bar's whole volume into the single price bin containing
its typical price (high+low+close)/3.  Here every hourly bar's volume is
distributed across all price bins its high-low range overlaps, in proportion
to the overlap width, so the profile reflects where trading actually
concentrated instead of a single representative price per day.

Failure policy (project rule): a failed fetch raises and nothing is
fabricated.  The caller (update_data) keeps the previously saved profile for
that stock and reports the failure in the run diagnostics.

Only the aggregated bins are stored (in data/stocks.json); the raw hourly
bars are never serialized.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

PROFILE_BINS = 60
PROFILE_RANGE = '2y'
PROFILE_INTERVAL = '1h'
_SOURCE = 'Yahoo Finance chart API（1時間足・約2年）'


def _now_jst():
    return datetime.now(JST)


def fetch_hourly_bars(code):
    """Fetch hourly OHLCV bars for one TSE stock from the Yahoo chart API.

    Returns a list of {'timestamp', 'date', 'high', 'low', 'close', 'volume'}
    dicts in chronological order.  Raises RuntimeError when neither Yahoo
    host returns a usable payload.
    """
    code = str(code).strip().upper()
    symbol = code + '.T'
    params = {
        'range': PROFILE_RANGE,
        'interval': PROFILE_INTERVAL,
        'includePrePost': 'false',
        'events': 'div,splits',
    }
    errors = []
    for host in ('https://query1.finance.yahoo.com', 'https://query2.finance.yahoo.com'):
        url = host + '/v8/finance/chart/' + urllib.parse.quote(symbol) + '?' + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; kabu-score/15.0)',
                    'Accept': 'application/json',
                    'Referer': 'https://finance.yahoo.com/',
                },
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                obj = json.loads(r.read().decode('utf-8', 'replace'))
        except Exception as e:
            errors.append(host + ': ' + str(e))
            continue
        chart = obj.get('chart') or {}
        result = chart.get('result') or []
        if not result:
            err = chart.get('error') or {}
            errors.append(host + ': ' + str(err.get('description') or 'no result'))
            continue
        first = result[0] or {}
        timestamps = first.get('timestamp') or []
        quote = ((first.get('indicators') or {}).get('quote') or [{}])[0] or {}
        highs = quote.get('high') or []
        lows = quote.get('low') or []
        closes = quote.get('close') or []
        volumes = quote.get('volume') or []
        bars = []
        for i, ts in enumerate(timestamps):
            def _num(seq):
                if i >= len(seq):
                    return None
                v = seq[i]
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None
            bars.append({
                'timestamp': ts,
                'date': datetime.fromtimestamp(ts, JST).date().isoformat(),
                'high': _num(highs),
                'low': _num(lows),
                'close': _num(closes),
                'volume': _num(volumes),
            })
        if bars:
            return bars
        errors.append(host + ': empty bars')
    raise RuntimeError('Yahoo 1時間足を取得できませんでした (' + '; '.join(errors) + ')')


def build_volume_profile(bars, bins=PROFILE_BINS):
    """Aggregate hourly bars into a volume-by-price profile.

    Each bar's volume is spread over the bins its [low, high] range overlaps,
    proportionally to the overlap width.  A bar with high == low contributes
    all of its volume to the single bin containing that price.
    """
    valid = [
        b for b in bars
        if b.get('high') is not None and b.get('low') is not None
        and b.get('volume') is not None and b['volume'] > 0 and b['high'] >= b['low']
    ]
    if not valid:
        raise RuntimeError('出来高のある1時間足バーがありません')
    price_min = min(b['low'] for b in valid)
    price_max = max(b['high'] for b in valid)
    if price_max <= price_min:
        price_max = price_min + 1.0
    bin_width = (price_max - price_min) / bins
    volumes = [0.0] * bins
    total_volume = 0.0
    for b in valid:
        lo, hi, vol = b['low'], b['high'], b['volume']
        total_volume += vol
        if hi <= lo:
            idx = int((lo - price_min) / bin_width)
            idx = max(0, min(bins - 1, idx))
            volumes[idx] += vol
            continue
        first_idx = max(0, min(bins - 1, int((lo - price_min) / bin_width)))
        last_idx = max(0, min(bins - 1, int((hi - price_min) / bin_width)))
        span = hi - lo
        for idx in range(first_idx, last_idx + 1):
            bin_lo = price_min + idx * bin_width
            bin_hi = bin_lo + bin_width
            overlap = min(hi, bin_hi) - max(lo, bin_lo)
            if overlap > 0:
                volumes[idx] += vol * (overlap / span)
    arr = [
        {
            'price': round(price_min + (i + 0.5) * bin_width, 2),
            'volume': round(volumes[i], 2),
        }
        for i in range(bins)
    ]
    return {
        'source': _SOURCE,
        'interval': PROFILE_INTERVAL,
        'range': PROFILE_RANGE,
        'bins': bins,
        'bin_width': round(bin_width, 4),
        'price_min': round(price_min, 2),
        'price_max': round(price_max, 2),
        'period_start': valid[0]['date'],
        'period_end': valid[-1]['date'],
        'bars': len(valid),
        'total_volume': round(total_volume, 2),
        'built_at': _now_jst().isoformat(),
        'arr': arr,
    }


def fetch_volume_profile(code):
    """Fetch hourly bars for ``code`` and build the stored profile dict."""
    return build_volume_profile(fetch_hourly_bars(code))
