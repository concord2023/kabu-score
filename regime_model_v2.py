"""Weekly direction + daily timing model.
Conservative by design: only the downtrend-reversal branch can currently emit BUY_CANDIDATE.
"""
from statistics import mean
from datetime import date


def _close(row):
    v = row.get('adj_close') if row.get('adj_close') is not None else row.get('close')
    return float(v) if v is not None else None


def _pct(a, b):
    return (a / b - 1) * 100 if a is not None and b not in (None, 0) else None


def weekly_metrics(rows):
    """Return weekly closes/MA metrics using the last trading day of each ISO week."""
    usable = []
    for r in rows:
        c = _close(r)
        d = r.get('date')
        if c is not None and d:
            usable.append((d, c))
    usable.sort(reverse=True)
    weeks = {}
    for d, c in usable:
        try:
            y, w, _ = date.fromisoformat(d).isocalendar()
        except Exception:
            continue
        key = (y, w)
        # rows are descending, so first row is latest trading day of that week
        if key not in weeks:
            weeks[key] = (d, c)
    ordered = [weeks[k] for k in sorted(weeks, reverse=True)]
    closes = [x[1] for x in ordered]
    dates = [x[0] for x in ordered]
    def ma(n):
        return mean(closes[0:n]) if len(closes) >= n else None
    m13, m26, m52 = ma(13), ma(26), ma(52)
    m13_prev4 = mean(closes[4:17]) if len(closes) >= 17 else None
    m26_prev4 = mean(closes[4:30]) if len(closes) >= 30 else None
    current = closes[0] if closes else None
    return {
        'weekly_date': dates[0] if dates else None,
        'weekly_close': current,
        'weekly_ma13': m13,
        'weekly_ma26': m26,
        'weekly_ma52': m52,
        'weekly_vs13': _pct(current, m13),
        'weekly_vs26': _pct(current, m26),
        'weekly_vs52': _pct(current, m52),
        'weekly_ma13_slope4w': _pct(m13, m13_prev4),
        'weekly_ma26_slope4w': _pct(m26, m26_prev4),
        'weekly_count': len(closes),
    }


def classify_weekly(m):
    """Classify broad direction, normalizing for missing 52-week history."""
    checks = []
    def add(v, label_up, label_down):
        if v is not None:
            # Numeric metrics are positive for the 'up' side; booleans keep their truth value.
            ok = v if isinstance(v, bool) else (float(v) > 0)
            checks.append((ok, label_up, label_down))
    add(m.get('weekly_vs13'), 'above 13w MA', 'below 13w MA')
    add(m.get('weekly_vs26'), 'above 26w MA', 'below 26w MA')
    if m.get('weekly_ma13') is not None and m.get('weekly_ma26') is not None:
        add(m['weekly_ma13'] > m['weekly_ma26'], '13w MA > 26w MA', '13w MA < 26w MA')
    add(m.get('weekly_ma13_slope4w'), '13w MA rising', '13w MA falling')
    add(m.get('weekly_ma26_slope4w'), '26w MA rising', '26w MA falling')
    if m.get('weekly_ma52') is not None:
        add(m.get('weekly_vs52'), 'above 52w MA', 'below 52w MA')
        if m.get('weekly_ma26') is not None:
            add(m['weekly_ma26'] > m['weekly_ma52'], '26w MA > 52w MA', '26w MA < 52w MA')
    if not checks:
        return 'UNKNOWN', [], 0, 0
    up = sum(1 for ok,_,_ in checks if ok)
    down = len(checks) - up
    # Require 5/7 when 52w exists, otherwise 4/5.
    threshold = 5 if len(checks) >= 7 else 4 if len(checks) >= 5 else 3
    if up >= threshold:
        regime = 'UPTREND'
    elif down >= threshold:
        regime = 'DOWNTREND'
    else:
        regime = 'RANGE_TRANSITION'
    reasons = [u if ok else d for ok,u,d in checks]
    return regime, reasons, up, down


def _daily_metrics(rows):
    vals = [_close(r) for r in rows]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {}
    close = vals[0]
    ma20 = mean(vals[1:21]) if len(vals) >= 21 else None
    ma60 = mean(vals[1:61]) if len(vals) >= 61 else None
    return {
        'close': close,
        'ma20': ma20,
        'ma60': ma60,
        'vs20': _pct(close, ma20),
        'vs60': _pct(close, ma60),
        'ret1': _pct(vals[0], vals[1]) if len(vals) > 1 else None,
        'ret5': _pct(vals[0], vals[5]) if len(vals) > 5 else None,
        'ret10': _pct(vals[0], vals[10]) if len(vals) > 10 else None,
        'ret20': _pct(vals[0], vals[20]) if len(vals) > 20 else None,
    }


def classify(rows):
    w = weekly_metrics(rows)
    weekly, wreasons, up, down = classify_weekly(w)
    d = _daily_metrics(rows)
    vs20, vs60, ret1, ret5, ret10, ret20 = (d.get(k) for k in ('vs20','vs60','ret1','ret5','ret10','ret20'))

    if weekly == 'UPTREND':
        # A deep daily drawdown is treated as a pullback even when the
        # longer weekly structure is still bullish. This prevents a stock
        # such as Kioxia from being mislabeled as a clean continuation.
        deep_pullback = ((vs20 is not None and vs20 <= -5) or
                         (vs60 is not None and vs60 <= -10) or
                         (ret20 is not None and ret20 <= -10))
        if deep_pullback or (vs20 is not None and vs20 < 0) or (ret5 is not None and ret5 < 0):
            state = 'UPTREND_PULLBACK'
            reason = '週足は上昇基調だが、日足は押し目/深い調整。上昇継続と反転を分けて監視。'
        else:
            state = 'UPTREND'
            reason = '週足・日足とも上向き。'
    elif weekly == 'DOWNTREND':
        stabilization = (ret1 is not None and ret1 > 0 and
                         ret5 is not None and ret5 >= -5 and
                         ret20 is not None and ret20 < 0)
        above20 = vs20 is not None and vs20 >= 0
        if stabilization or (above20 and ret20 is not None and ret20 < 0):
            state = 'DOWNTREND_REVERSAL_WAIT'
            reason = '週足は下降だが、日足に下げ止まり/反発の兆候。反転確認待ち。'
        else:
            state = 'DOWNTREND_CONTINUED'
            reason = '週足下降で日足にも反転条件が不足。'
    elif weekly == 'RANGE_TRANSITION':
        state = 'RANGE_TRANSITION'
        reason = '週足の方向が揃っておらず、上昇/下降を断定しない。'
    else:
        state = 'UNKNOWN'
        reason = '必要な週足データが不足。'

    required = {
        'vs60_deep_discount': vs60 is not None and vs60 <= -5,
        'ret1_positive': ret1 is not None and ret1 > 0,
        'ret5_stabilized': ret5 is not None and ret5 >= -5,
        'ret10_stabilized': ret10 is not None and ret10 >= -10,
    }
    if state == 'DOWNTREND_REVERSAL_WAIT':
        missing = [k for k,v in required.items() if not v]
        signal = 'BUY_CANDIDATE' if not missing else 'WATCH'
        if missing:
            signal_reason = '反転4条件の不足: ' + ', '.join(missing)
        else:
            signal_reason = '反転4条件が全て成立。暫定BUY候補。'
    elif state in ('UPTREND','UPTREND_PULLBACK','RANGE_TRANSITION'):
        signal = 'WATCH'
        signal_reason = 'この状態のBUY条件は未検証のためWATCH。'
        missing = []
    elif state == 'DOWNTREND_CONTINUED':
        signal = 'AVOID'
        signal_reason = '下降継続。反転確認まで買わない。'
        missing = []
    else:
        signal = 'INSUFFICIENT'
        signal_reason = 'データ不足。'
        missing = []

    return {
        **w,
        'weekly_regime': weekly,
        'weekly_up_count': up,
        'weekly_down_count': down,
        'weekly_reason': ' / '.join(wreasons),
        **d,
        'signal_state': state,
        'signal': signal,
        'signal_reason': signal_reason,
        'missing_conditions': missing,
        'one_condition_away': len(missing) == 1,
        'entry_conditions': required,
    }
