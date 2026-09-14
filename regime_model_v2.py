from __future__ import annotations
from statistics import mean
from collections import defaultdict
from datetime import datetime


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _close(row):
    return _f(row.get('adj_close') if row.get('adj_close') is not None else row.get('close'))


def _weekly_closes(rows):
    buckets = defaultdict(list)
    for r in rows:
        d = r.get('date')
        c = _close(r)
        if not d or c is None:
            continue
        try:
            dt = datetime.strptime(d, '%Y-%m-%d')
        except ValueError:
            continue
        y, w, _ = dt.isocalendar()
        buckets[(y, w)].append((dt, c))
    out = []
    for key, vals in buckets.items():
        vals.sort(key=lambda x: x[0])
        out.append({'date': vals[-1][0].date().isoformat(), 'close': vals[-1][1]})
    out.sort(key=lambda x: x['date'], reverse=True)
    return out


def _ma(vals, n):
    return mean(vals[:n]) if len(vals) >= n else None


def _pct(a, b):
    return (a / b - 1) * 100 if a is not None and b not in (None, 0) else None


def weekly_metrics(rows):
    ws = _weekly_closes(rows)
    vals = [x['close'] for x in ws]
    result = {'weekly_date': ws[0]['date'] if ws else None, 'weekly_points': len(vals)}
    for n in (13, 26, 52):
        ma = _ma(vals, n)
        result[f'weekly_ma{n}'] = round(ma, 2) if ma is not None else None
        result[f'weekly_vs{n}'] = round(_pct(vals[0], ma), 2) if ma is not None else None
    for n in (13, 26):
        now = _ma(vals, n)
        old = _ma(vals[4:], n)
        result[f'weekly_ma{n}_slope4w'] = round(_pct(now, old), 2) if now is not None and old is not None else None
    return result


def classify_regime(rows):
    wm = weekly_metrics(rows)
    vals = [_close(r) for r in rows]
    vals = [x for x in vals if x is not None]
    close = vals[0] if vals else None
    daily_ma20 = mean(vals[1:21]) if len(vals) >= 21 else None
    vs20 = _pct(close, daily_ma20)
    def ret(n): return _pct(vals[0], vals[n]) if len(vals) > n else None
    ret1, ret5, ret10, ret20 = ret(1), ret(5), ret(10), ret(20)

    features = []
    up = down = 0
    if wm['weekly_vs13'] is not None:
        features.append('13MA'); up += wm['weekly_vs13'] > 0; down += wm['weekly_vs13'] < 0
    if wm['weekly_vs26'] is not None:
        features.append('26MA'); up += wm['weekly_vs26'] > 0; down += wm['weekly_vs26'] < 0
    if wm['weekly_ma13'] is not None and wm['weekly_ma26'] is not None:
        features.append('MA13>MA26'); up += wm['weekly_ma13'] > wm['weekly_ma26']; down += wm['weekly_ma13'] < wm['weekly_ma26']
    if wm['weekly_ma13_slope4w'] is not None:
        features.append('MA13 slope'); up += wm['weekly_ma13_slope4w'] > 0; down += wm['weekly_ma13_slope4w'] < 0
    if wm['weekly_ma26_slope4w'] is not None:
        features.append('MA26 slope'); up += wm['weekly_ma26_slope4w'] > 0; down += wm['weekly_ma26_slope4w'] < 0
    if wm['weekly_vs52'] is not None:
        features.append('52MA'); up += wm['weekly_vs52'] > 0; down += wm['weekly_vs52'] < 0
    if wm['weekly_ma26'] is not None and wm['weekly_ma52'] is not None:
        features.append('MA26>MA52'); up += wm['weekly_ma26'] > wm['weekly_ma52']; down += wm['weekly_ma26'] < wm['weekly_ma52']
    n = len(features)
    if n < 4:
        weekly_dir = 'UNKNOWN'
    elif up / n >= 0.70 and up > down:
        weekly_dir = 'UP'
    elif down / n >= 0.70 and down > up:
        weekly_dir = 'DOWN'
    else:
        weekly_dir = 'RANGE'

    if weekly_dir == 'UP':
        if (vs20 is not None and vs20 < 0) or (ret5 is not None and ret5 < 0):
            state = 'UPTREND_PULLBACK'
            reason = '週足は上向き。日足は20日MA下/5日下落で押し目状態。'
        else:
            state = 'UPTREND'
            reason = '週足の方向が上向きで、日足も大きく崩れていない。'
    elif weekly_dir == 'DOWN':
        stabilized = (ret1 is not None and ret1 > 0 and ret5 is not None and ret5 >= -5 and
                      ((ret20 is not None and ret20 < 0) or (vs20 is not None and vs20 > 0)))
        if stabilized:
            state = 'DOWNTREND_REVERSAL_WAIT'
            reason = '週足は下向きだが、日足に下げ止まり/反発の兆候。反転確認待ち。'
        else:
            state = 'DOWNTREND_CONTINUED'
            reason = '週足・日足とも弱く、まだ反転条件が整っていない。'
    elif weekly_dir == 'RANGE':
        state = 'RANGE_TRANSITION'
        reason = '週足の方向が上昇/下降に決め切れない。レンジ・転換途中。'
    else:
        state = 'UNKNOWN'
        reason = '週足データが不足しており方向判定できない。'

    return {**wm, 'daily_vs20': round(vs20,2) if vs20 is not None else None,
            'daily_ret1': round(ret1,2) if ret1 is not None else None,
            'daily_ret5': round(ret5,2) if ret5 is not None else None,
            'daily_ret10': round(ret10,2) if ret10 is not None else None,
            'daily_ret20': round(ret20,2) if ret20 is not None else None,
            'weekly_direction': weekly_dir, 'regime': state, 'regime_reason': reason}


def decide(stock, regime):
    vs60 = _f(stock.get('vs60')); ret1 = _f(stock.get('change')); ret5 = _f(stock.get('ret5')); ret10 = _f(stock.get('ret10'))
    missing = []
    checks = []
    if regime['regime'] == 'DOWNTREND_REVERSAL_WAIT':
        conds = [
            ('60日MAより5%以上下', vs60 is not None and vs60 <= -5, vs60, 'vs60<=-5%'),
            ('当日プラス', ret1 is not None and ret1 > 0, ret1, '当日騰落率>0%'),
            ('5日騰落率が-5%以上', ret5 is not None and ret5 >= -5, ret5, '5日騰落率>=-5%'),
            ('10日騰落率が-10%以上', ret10 is not None and ret10 >= -10, ret10, '10日騰落率>=-10%')
        ]
        for label, ok, value, rule in conds:
            checks.append({'label': label, 'ok': bool(ok), 'value': value, 'rule': rule})
            if not ok:
                missing.append(label)
        signal = 'BUY_CANDIDATE' if not missing else 'WATCH'
        reason = '反転4条件をすべて満たした暫定買い候補。' if not missing else '反転待ち。未達条件: ' + ' / '.join(missing)
    elif regime['regime'] in ('UPTREND', 'UPTREND_PULLBACK', 'RANGE_TRANSITION'):
        signal = 'WATCH'
        reason = '方向/押し目を監視。上昇・押し目買い条件は検証後にBUYへ昇格。'
        checks = [
            {'label':'週足方向', 'ok': regime.get('weekly_direction') == 'UP', 'value': regime.get('weekly_direction'), 'rule':'週足で方向判定'},
            {'label':'日足20MA', 'ok': _f(regime.get('daily_vs20')) is not None, 'value': regime.get('daily_vs20'), 'rule':'20日MAからの乖離を確認'}
        ]
    elif regime['regime'] == 'DOWNTREND_CONTINUED':
        signal = 'AVOID'; reason = '下降継続のため現時点では回避。'
    else:
        signal = 'INSUFFICIENT'; reason = 'データ不足。'
    return {'signal': signal, 'signal_reason': reason, 'missing_conditions': missing,
            'condition_checks': checks, 'one_condition_away': len(missing) == 1}
