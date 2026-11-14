"""Pure technical-indicator calculations.

Extracted from update_data.py (Phase B2). All functions here are pure: they
operate on in-memory price rows/values only, with no network, file, or
global-state access.

Note: rsi14 (simple average) and rsi14_wilder (Wilder smoothing) are
intentionally different methods kept for different consumers; do not merge
them.
"""
import statistics


def pct(a, b):
    return ((a / b) - 1) * 100 if a is not None and b not in (None, 0) else None

def signal_icons(candle_signal, breakout, supply_info, volume_ratio, price_change, vs20, ret5, range_position60, rsi_daily=None, rsi_weekly=None, bb_daily=None, bb_weekly=None, monthly_bottom=None):
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

    # Very strict multi-timeframe bottom clue.
    mb = monthly_bottom or {}
    if mb.get('active'):
        icons.append({
            'code': 'MONTHLY_MACD_BOTTOM', 'icon': '🟣', 'label': '大底候補（月足MACD）',
            'strength': 'strong', 'reason': mb.get('reason', '')
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

def rsi14_wilder(vals):
    """Standard Wilder RSI(14), accepting newest-first values.

    The existing rsi14() is intentionally retained for the app's historical
    score calculations. The strict Prime "トラさん" scanner uses this
    conventional Wilder form so its <=30 gate matches common charting tools.
    """
    if len(vals) < 15:
        return None
    chronological = [float(v) for v in reversed(vals)]
    gains = []
    losses = []
    for i in range(1, len(chronological)):
        d = chronological[i] - chronological[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    if len(gains) < 14:
        return None
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    for gain, loss in zip(gains[14:], losses[14:]):
        avg_gain = (avg_gain * 13 + gain) / 14
        avg_loss = (avg_loss * 13 + loss) / 14
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def ema_series(values, period):
    """Return standard EMA values in chronological order."""
    if not values or period <= 0:
        return []
    alpha = 2 / (period + 1)
    out = []
    ema = float(values[0])
    out.append(ema)
    for value in values[1:]:
        ema = (float(value) - ema) * alpha + ema
        out.append(ema)
    return out

def macd_series(vals, fast=12, slow=26, signal=9):
    """Return MACD/Signal/Histogram in newest-first order."""
    if len(vals) < slow:
        return [], [], []
    chronological = list(reversed([float(v) for v in vals]))
    fast_ema = ema_series(chronological, fast)
    slow_ema = ema_series(chronological, slow)
    macd = [f - sl for f, sl in zip(fast_ema, slow_ema)]
    sig = ema_series(macd, signal)
    hist = [m - sg for m, sg in zip(macd, sig)]
    return list(reversed(macd)), list(reversed(sig)), list(reversed(hist))

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

def monthly_closes(rows):
    """Return one adjusted closing price per ISO calendar month, newest first."""
    seen = set()
    closes = []
    for row in rows:
        date = row.get('date')
        if not date:
            continue
        try:
            key = date[:7]
            __import__('datetime').date.fromisoformat(date)
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

def monthly_macd_bottom_signal(monthly_vals, weekly_rsi):
    """Bottom clue: expose monthly MACD state separately from the weekly RSI gate."""
    result = {
        'active': False, 'state': 'データ不足', 'macd_active': False, 'weekly_rsi_ok': False,
        'monthly_points': len(monthly_vals), 'macd': None, 'signal': None,
        'hist': None, 'prev_hist': None, 'prev2_hist': None, 'hist_change': None, 'hist_change_prev': None,
        'reason': '月足MACDに必要な履歴が不足しています。'
    }
    if weekly_rsi is not None:
        result['weekly_rsi_ok'] = weekly_rsi <= 30
    if len(monthly_vals) < 30:
        result['reason'] = f'月足データが不足（{len(monthly_vals)}か月）。30か月以上を必要とします。'
        return result
    m, sig, hist = macd_series(monthly_vals)
    if len(hist) < 3:
        return result
    h0, h1, h2 = hist[0], hist[1], hist[2]
    result.update({
        'macd': m[0], 'signal': sig[0], 'hist': h0,
        'prev_hist': h1, 'prev2_hist': h2,
        'hist_change': h0 - h1, 'hist_change_prev': h1 - h2
    })
    golden_cross = h0 >= 0 and h1 < 0
    pre_golden = h0 < 0 and h0 > h1 > h2
    if golden_cross:
        result['macd_active'] = True
        result['state'] = '月足MACDゴールデンクロス'
    elif pre_golden:
        result['macd_active'] = True
        result['state'] = '月足MACD GC手前・差分縮小'
    else:
        result['state'] = '月足MACD条件未達'

    result['active'] = bool(result['weekly_rsi_ok'] and result['macd_active'])
    if result['active']:
        if golden_cross:
            result['reason'] = f'週足RSI {weekly_rsi:.1f}（30以下）＋月足MACDがゴールデンクロス。'
        else:
            result['reason'] = f'週足RSI {weekly_rsi:.1f}（30以下）＋月足MACDはGC前でヒストグラムが2か月連続縮小。'
    elif not result['weekly_rsi_ok']:
        result['reason'] = f'月足MACDは「{result["state"]}」ですが、週足RSI条件（30以下）は未成立です。'
    else:
        result['reason'] = f"週足RSIは30以下ですが、月足MACDは「{result['state']}」です。"
    return result

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

    # Confirmation is based on the current candle itself, not on reclaiming
    # the previous day's high.  This keeps the bottom/reversal check anchored
    # to the broader 20/60-day decline context instead of making the signal
    # depend on a single immediately preceding day's price.
    confirmed = bool(current and current['bull'] and current['close'] >= current['low'] + current['range'] * 0.60)
    confirmation = ('反転方向の値動きを確認（当日陽線・終値が値幅の上側）'
                    if confirmed else
                    ('反発の形はあるが、当日の反転方向は弱い' if current and current['bull']
                     else '当日は陰線。反転方向の確認待ち'))

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
        # Avoid the vague label "反転サイン". Show the actual detected
        # candle pattern(s) so the user can tell what triggered the clue.
        labels = '・'.join(x['label'] for x in unique)
        status = labels
        strength = '中' if confirmed else '弱'
        reason = f"{labels}。ただし大底圏の条件は未達。{confirmation}。"

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
    """Detect a recent range breakout primarily from price action.

    A breakout is different from a bottom reversal. We use the *intraday high*
    of the prior 20 sessions as the range ceiling, then look for a closing
    breakout and continued holding above that level. Breakout-day volume is a
    useful strength clue, but is NOT a mandatory condition: a valid price
    breakout can occur without unusually high volume.
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
            'breakout_level':round(current_range_high,2),'breakout_trigger':round(current_range_high*1.005,2),'breakout_date':None,
            'current_price':round(current,2),'current_volume':round(vols[0],0) if vols and vols[0] is not None else None,
            'avg_volume20':round(sum([v for v in vols[1:21] if v is not None and v>0])/len([v for v in vols[1:21] if v is not None and v>0]),0) if any(v is not None and v>0 for v in vols[1:21]) else None,
            'volume_reference_ratio':1.5,'volume_reference':None,
            'breakout_volume_ratio':None,'distance_from_breakout':round((current/current_range_high-1)*100,2),
            'reason':f'現在値{current:.0f}円。20日レンジ上限{current_range_high:.0f}円、明確な終値ブレイク目安{current_range_high*1.005:.0f}円。出来高増加はブレイクの信頼度をみる参考材料で、必須条件ではありません。' 
        }

    b=candidates[0]
    held=current >= b['level']*0.99
    volume_ok=b['volume_ratio'] is not None and b['volume_ratio']>=1.5
    ma_ok=current > ma20
    ret5=(current/closes[5]-1)*100 if len(closes)>5 else None
    trend_ok=ret5 is not None and ret5>0
    dist=(current/b['level']-1)*100
    extension_ok=dist <= 15
    confirmed=held and ma_ok and trend_ok and extension_ok
    if confirmed:
        status='レンジ抜け・再上昇'
        strength='強' if volume_ok else '中'
        volume_note=(f"突破日の出来高は直前20日平均の{b['volume_ratio']:.1f}倍" if b['volume_ratio'] is not None else '突破日の出来高は未確認')
        if not volume_ok:
            volume_note += '（出来高増加なしでも価格条件を満たすためブレイク判定）'
        reason=f"{b['date']}に20日レンジ高値{b['level']:.0f}円を終値で上抜け。{volume_note}。現在も突破水準を維持。"
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
        'breakout_level':round(b['level'],2),'breakout_trigger':round(b['level']*1.005,2),'breakout_date':b['date'],
        'current_price':round(current,2),'current_volume':round(vols[0],0) if vols and vols[0] is not None else None,
        'avg_volume20':round(sum([v for v in vols[1:21] if v is not None and v>0])/len([v for v in vols[1:21] if v is not None and v>0]),0) if any(v is not None and v>0 for v in vols[1:21]) else None,
        'volume_reference_ratio':1.5,
        'volume_reference':round((sum([v for v in vols[1:21] if v is not None and v>0])/len([v for v in vols[1:21] if v is not None and v>0]))*1.5,0) if any(v is not None and v>0 for v in vols[1:21]) else None,
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

def bollinger_sigma_position(current, mid, upper, sigma=2):
    """Return current price's position in standard-deviation units.

    The Bollinger bands are calculated as mid +/- sigma*SD, so SD can be
    recovered from (upper-mid)/sigma without changing the existing band logic.
    """
    if current is None or mid is None or upper is None or sigma in (None, 0):
        return None
    sd = (float(upper) - float(mid)) / float(sigma)
    if sd == 0:
        return 0.0
    return (float(current) - float(mid)) / sd

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

def build_period_chart_history(rows, timeframe, limit):
    """Build aggregated OHLCV chart data for weekly/monthly tabs.

    Input rows are newest-first daily rows.  The returned series is chronological
    so the browser can draw left-to-right.  This is presentation data only; all
    trading decisions continue to use the daily calculation pipeline.
    """
    if timeframe not in ('weekly', 'monthly'):
        return []
    groups = {}
    for row in rows:
        date = row.get('date')
        if not date:
            continue
        try:
            d = __import__('datetime').date.fromisoformat(date)
        except Exception:
            continue
        key = d.isocalendar()[:2] if timeframe == 'weekly' else (d.year, d.month)
        groups.setdefault(key, []).append(row)

    periods = []
    for key, items in groups.items():
        items = sorted(items, key=lambda z: str(z.get('date') or ''))
        if not items:
            continue
        def adj_price(z, field):
            raw = z.get(field)
            if raw is None:
                return None
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                return None
            try:
                raw_close = float(z.get('close')) if z.get('close') is not None else None
                adj_close = float(z.get('adj_close')) if z.get('adj_close') is not None else None
            except (TypeError, ValueError):
                raw_close = adj_close = None
            if raw_close and adj_close is not None:
                return raw * (adj_close / raw_close)
            return raw
        opens = [adj_price(z, 'open') for z in items]
        highs = [adj_price(z, 'high') for z in items]
        lows = [adj_price(z, 'low') for z in items]
        closes = [adj_price(z, 'close') for z in items]
        volumes = [float(z.get('volume')) for z in items if z.get('volume') is not None]
        if not any(v is not None for v in closes):
            continue
        periods.append({
            'date': items[-1].get('date'),
            'open': round(opens[0], 2) if opens[0] is not None else None,
            'high': round(max(v for v in highs if v is not None), 2) if any(v is not None for v in highs) else None,
            'low': round(min(v for v in lows if v is not None), 2) if any(v is not None for v in lows) else None,
            'close': round(closes[-1], 2) if closes[-1] is not None else None,
            'volume': sum(volumes) if volumes else None,
        })

    periods.sort(key=lambda z: str(z.get('date') or ''))
    # Calculate period indicators from the full available monthly history, then
    # trim only the presentation window.  Trimming to 18 months before MACD
    # calculation made MACD(12,26,9) impossible because macd_series requires
    # at least 26 monthly closes, even though the underlying daily history was
    # already long enough.
    newest_all = list(reversed(periods))
    vals = [r['close'] for r in newest_all if r.get('close') is not None]
    macd_values, macd_signal_values, macd_hist_values = macd_series(vals)
    newest = newest_all[:limit]
    out = []
    for idx, r in enumerate(newest):
        def ma(window):
            v = vals[idx:idx + window]
            return round(statistics.mean(v), 2) if len(v) >= window else None
        win25 = vals[idx:idx + 25]
        bb_mid, bb_upper, bb_lower = bollinger(win25, 25, 2)
        vol_window = [float(z['volume']) for z in newest[idx+1:idx+21] if z.get('volume') is not None]
        out_row = {
            **r,
            # Period-specific MA sets: daily=5/25/75/200, weekly=5/13/26/52,
            # monthly=6/12/18.  Keep the field names explicit so the UI cannot
            # accidentally draw daily MA periods on weekly/monthly charts.
            'bb25_mid': round(bb_mid, 2) if bb_mid is not None else None,
            'bb25_upper': round(bb_upper, 2) if bb_upper is not None else None,
            'bb25_lower': round(bb_lower, 2) if bb_lower is not None else None,
            'volume_ma20': round(statistics.mean(vol_window), 0) if vol_window else None,
            'rsi14': round(rsi14(vals[idx:]), 1) if len(vals) - idx >= 15 else None,
            'macd': round(macd_values[idx], 3) if idx < len(macd_values) else None,
            'macd_signal': round(macd_signal_values[idx], 3) if idx < len(macd_signal_values) else None,
            'macd_hist': round(macd_hist_values[idx], 3) if idx < len(macd_hist_values) else None,
        }
        if timeframe == 'weekly':
            out_row.update({
                'ma5': ma(5), 'ma13': ma(13), 'ma26': ma(26), 'ma52': ma(52),
            })
        elif timeframe == 'monthly':
            out_row.update({
                'ma6': ma(6), 'ma12': ma(12), 'ma18': ma(18),
            })
        else:
            out_row.update({
                'ma5': ma(5), 'ma25': ma(25), 'ma75': ma(75), 'ma200': ma(200),
            })
        out.append(out_row)
    return list(reversed(out))
