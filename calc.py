"""Per-stock indicator aggregation: calc() and its supply helpers.

Extracted from update_data.py (Phase B3). calc() is a pure aggregation over
in-memory rows plus optional breadth/supply context; it performs no network
or file access. supply_points/supply_status moved with it because calc() is
their primary consumer (update_data re-exports them for existing importers).
"""
import statistics

from indicators import (
    bollinger, bollinger_sigma_position, bollinger_signal, breadth_points,
    breakout_signal, build_period_chart_history, candle_reversal_signals,
    macd_series, monthly_closes, monthly_macd_bottom_signal, pct,
    relative_points, rsi14, signal_icons, weekly_closes,
)


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
    macd_values, macd_signal_values, macd_hist_values = macd_series(vals)
    macd_now = macd_values[0] if macd_values else None
    macd_signal_now = macd_signal_values[0] if macd_signal_values else None
    macd_hist_now = macd_hist_values[0] if macd_hist_values else None
    weekly_vals = weekly_closes(rows)
    weekly_ma5 = statistics.mean(weekly_vals[:5]) if len(weekly_vals) >= 5 else None
    weekly_vs5 = pct(weekly_vals[0], weekly_ma5) if weekly_vals and weekly_ma5 is not None else None
    rsi_weekly = rsi14(weekly_vals)
    monthly_vals = monthly_closes(rows)
    monthly_ma6 = statistics.mean(monthly_vals[:6]) if len(monthly_vals) >= 6 else None
    monthly_ma12 = statistics.mean(monthly_vals[:12]) if len(monthly_vals) >= 12 else None
    monthly_ma18 = statistics.mean(monthly_vals[:18]) if len(monthly_vals) >= 18 else None
    monthly_bottom = monthly_macd_bottom_signal(monthly_vals, rsi_weekly)
    bb_weekly_mid, bb_weekly_upper, bb_weekly_lower = bollinger(weekly_vals, 13, 2)
    bb_daily_signal = bollinger_signal(vals[0], bb_daily_mid, bb_daily_upper, bb_daily_lower, 'DAILY', 25)
    bb_weekly_signal = bollinger_signal(weekly_vals[0] if weekly_vals else None, bb_weekly_mid, bb_weekly_upper, bb_weekly_lower, 'WEEKLY', 13)
    bb_daily_sigma = bollinger_sigma_position(vals[0], bb_daily_mid, bb_daily_upper, 2)
    bb_weekly_sigma = bollinger_sigma_position(weekly_vals[0] if weekly_vals else None, bb_weekly_mid, bb_weekly_upper, 2)
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
    icons = signal_icons(candle_signal, breakout, supply_info, vr, change, d20, r5, range_position60, rsi, rsi_weekly, bb_daily_signal, bb_weekly_signal, monthly_bottom)

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
            'volume': float(r.get('volume')) if r.get('volume') is not None else None,
            'volume_ma20': round(statistics.mean([float(z.get('volume')) for z in rows[idx+1:idx+21] if z.get('volume') is not None]), 0) if [z.get('volume') for z in rows[idx+1:idx+21] if z.get('volume') is not None] else None,
            'rsi14': round(rsi14(vals[idx:]), 1) if len(vals) - idx >= 15 else None,
            'macd': round(macd_values[idx], 3) if idx < len(macd_values) else None,
            'macd_signal': round(macd_signal_values[idx], 3) if idx < len(macd_signal_values) else None,
            'macd_hist': round(macd_hist_values[idx], 3) if idx < len(macd_hist_values) else None,
        })
    chart_history.reverse()
    chart_history_weekly = build_period_chart_history(rows, 'weekly', 60)
    chart_history_monthly = build_period_chart_history(rows, 'monthly', 36)

    return {
        'date': rows[0].get('date'), 'price': close,
        'change': round(change, 2) if change is not None else None,
        'volume': vols[0] if vols else None,
        'volume_ratio': round(vr, 2) if vr is not None else None,
        'volume_ratio_basis': '当日出来高 ÷ 直前20営業日の平均出来高',
        'volume_surge_threshold': 1.8,
        # Prior 5-session high is used for the explicit "uptrend continuation"
        # buy trigger.  The current close is intentionally excluded.
        'high5_prev': round(max(vals[1:6]), 2) if len(vals) >= 6 else None,
        'breakout5_prev': bool(len(vals) >= 6 and close > max(vals[1:6])),
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
        'bb_daily': {'period':25,'sigma':2,'middle':round(bb_daily_mid,2) if bb_daily_mid is not None else None,'upper':round(bb_daily_upper,2) if bb_daily_upper is not None else None,'lower':round(bb_daily_lower,2) if bb_daily_lower is not None else None,'position_sigma':round(bb_daily_sigma,2) if bb_daily_sigma is not None else None,'status':bb_daily_signal['code'] if bb_daily_signal else 'なし'},
        'bb_weekly': {'period':13,'sigma':2,'middle':round(bb_weekly_mid,2) if bb_weekly_mid is not None else None,'upper':round(bb_weekly_upper,2) if bb_weekly_upper is not None else None,'lower':round(bb_weekly_lower,2) if bb_weekly_lower is not None else None,'position_sigma':round(bb_weekly_sigma,2) if bb_weekly_sigma is not None else None,'status':bb_weekly_signal['code'] if bb_weekly_signal else 'なし'},
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
        'weekly_ma5': round(weekly_ma5, 2) if weekly_ma5 is not None else None,
        'weekly_vs5': round(weekly_vs5, 2) if weekly_vs5 is not None else None,
        'monthly_ma6': round(monthly_ma6, 2) if monthly_ma6 is not None else None,
        'monthly_ma12': round(monthly_ma12, 2) if monthly_ma12 is not None else None,
        'monthly_ma18': round(monthly_ma18, 2) if monthly_ma18 is not None else None,
        'macd': round(macd_now, 3) if macd_now is not None else None,
        'macd_signal': round(macd_signal_now, 3) if macd_signal_now is not None else None,
        'macd_hist': round(macd_hist_now, 3) if macd_hist_now is not None else None,
        'monthly_rsi_bottom_signal': monthly_bottom,
        'monthly_macd': round(monthly_bottom['macd'], 3) if monthly_bottom.get('macd') is not None else None,
        'monthly_macd_signal': round(monthly_bottom['signal'], 3) if monthly_bottom.get('signal') is not None else None,
        'monthly_macd_hist': round(monthly_bottom['hist'], 3) if monthly_bottom.get('hist') is not None else None,
        'monthly_macd_state': monthly_bottom.get('state'),
        'monthly_points': monthly_bottom.get('monthly_points', len(monthly_vals)),
        'score': total, 'score_max': 100,
        'data_points': len(vals), 'rows_received': len(rows),
        'candle_signal': candle_signal,
        'breakout_signal': breakout,
        'signal_icons': icons,
        'chart_history': chart_history,
        'chart_history_weekly': chart_history_weekly,
        'chart_history_monthly': chart_history_monthly,
        'interpretation': {
            'vs5': deviation_text(d5, '5日MA'), 'vs20': deviation_text(d20, '20日MA'), 'vs25': deviation_text(d25, '25日MA'), 'vs75': deviation_text(d75, '75日MA'), 'vs200': deviation_text(d200, '200日MA'),
            'rsi': rsi_text, 'rsi_daily': rsi_text, 'rsi_weekly': (f'RSI {rsi_weekly:.1f}（売られ過ぎ）' if rsi_weekly is not None and rsi_weekly < 30 else f'RSI {rsi_weekly:.1f}（過熱警戒）' if rsi_weekly is not None and rsi_weekly > 70 else f'RSI {rsi_weekly:.1f}（中立）' if rsi_weekly is not None else '判定不可'), 'volume': volume_text, 'range60': range_text, 'momentum': momentum_text,
        },
        'diagnostic': {
            'usable_points': len(vals), 'rsi_ready': len(vals) >= 15, 'weekly_rsi_ready': len(weekly_vals) >= 15, 'weekly_points': len(weekly_vals), 'weekly_ma5_ready': len(weekly_vals) >= 5,
            'ma20_ready': len(vals) >= 20, 'ma25_ready': len(vals) >= 25, 'ma75_ready': len(vals) >= 75, 'ma200_ready': len(vals) >= 200,
            'monthly_macd_ready': len(monthly_vals) >= 30, 'monthly_points': len(monthly_vals),
            'monthly_ma6_ready': len(monthly_vals) >= 6, 'monthly_ma12_ready': len(monthly_vals) >= 12,
            'monthly_ma18_ready': len(monthly_vals) >= 18,
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
