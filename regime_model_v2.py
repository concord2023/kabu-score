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
    for n in (5, 13, 26, 52):
        ma = _ma(vals, n)
        result[f'weekly_ma{n}'] = round(ma, 2) if ma is not None else None
        result[f'weekly_vs{n}'] = round(_pct(vals[0], ma), 2) if ma is not None else None
    for n in (13, 26):
        now = _ma(vals, n)
        old = _ma(vals[4:], n)
        result[f'weekly_ma{n}_slope4w'] = round(_pct(now, old), 2) if now is not None and old is not None else None
    return result


def classify_regime(rows, candle_signal=None, breakout_signal=None):
    wm = weekly_metrics(rows)
    vals = [_close(r) for r in rows]
    vals = [x for x in vals if x is not None]
    close = vals[0] if vals else None
    # Moving averages include the current close. 20MA remains useful for
    # regime detection, while 25MA/75MA are used for the actual pullback
    # depth because those are common Japanese-market support references.
    daily_ma20 = mean(vals[:20]) if len(vals) >= 20 else None
    daily_ma20_old = mean(vals[5:25]) if len(vals) >= 25 else None
    daily_ma25 = mean(vals[:25]) if len(vals) >= 25 else None
    daily_ma25_old = mean(vals[5:30]) if len(vals) >= 30 else None
    daily_ma75 = mean(vals[:75]) if len(vals) >= 75 else None
    daily_ma75_old = mean(vals[20:95]) if len(vals) >= 95 else None
    vs20 = _pct(close, daily_ma20)
    vs25 = _pct(close, daily_ma25)
    vs75 = _pct(close, daily_ma75)
    ma20_slope5 = _pct(daily_ma20, daily_ma20_old)
    ma25_slope5 = _pct(daily_ma25, daily_ma25_old)
    ma75_slope20 = _pct(daily_ma75, daily_ma75_old)
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

    # A confirmed range breakout is a more specific current-state label than
    # generic UPTREND/UPTREND_PULLBACK.  Keep the classification and the BUY
    # route aligned: if the stock is being bought because it has just broken
    # out of its range, the UI should say that explicitly.
    bo = breakout_signal or {}
    # A detected breakout is a more specific current-state classification
    # than generic weekly UP/RANGE/UNKNOWN.  `confirmed` belongs to the BUY
    # decision; classification should already say レンジブレイク when the
    # price has actually broken the range and is still holding it, even if
    # volume/MA/continuation conditions are not all satisfied yet.
    # This prevents a real breakout from being hidden as 判定不能 or generic
    # 上昇トレンド.
    breakout_watch = bo.get('is_breakout') and bo.get('status') in ('レンジ抜け・再上昇', 'レンジ抜け候補')
    if breakout_watch:
        state = 'RANGE_BREAKOUT'
        if bo.get('confirmed'):
            reason = 'レンジ上限を確認付きでブレイク中。現在の買いシグナルは通常の上昇トレンドではなく、レンジブレイク・再上昇型。'
        else:
            reason = 'レンジ上限を上抜けて維持中。ただし出来高・20日MA・上昇継続などのBUY確認条件が未達のため、レンジブレイク監視中。'
    elif weekly_dir == 'UP':
        if (vs20 is not None and vs20 < 0) or (ret5 is not None and ret5 < 0):
            state = 'UPTREND_PULLBACK'
            reason = '週足は上向き。日足は20日MA下/5日下落で押し目状態。'
        else:
            state = 'UPTREND'
            reason = '週足の方向が上向きで、日足も大きく崩れていない。'
    elif weekly_dir == 'DOWN':
        stabilized = (ret1 is not None and ret1 > 0 and ret5 is not None and ret5 >= -5 and
                      ((ret20 is not None and ret20 < 0) or (vs20 is not None and vs20 > 0)))
        candle_confirmed = bool(candle_signal and candle_signal.get('status') == '大底反転サイン')
        if stabilized and candle_confirmed and vs20 is not None and vs20 >= 0:
            state = 'DOWNTREND_REVERSAL_CONFIRMED'
            reason = '週足は下向きだが、日足が20日MAを回復し、大底反転ローソク足も確認。反転確認済み。'
        elif stabilized:
            state = 'DOWNTREND_REVERSAL_WAIT'
            reason = '週足は下向きだが、日足に下げ止まり/反発の兆候。ローソク足・20日MAの反転確認待ち。'
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
            'daily_vs25': round(vs25,2) if vs25 is not None else None,
            'daily_vs75': round(vs75,2) if vs75 is not None else None,
            'daily_ma20_slope5': round(ma20_slope5,2) if ma20_slope5 is not None else None,
            'daily_ma25_slope5': round(ma25_slope5,2) if ma25_slope5 is not None else None,
            'daily_ma75_slope20': round(ma75_slope20,2) if ma75_slope20 is not None else None,
            'daily_ret1': round(ret1,2) if ret1 is not None else None,
            'daily_ret5': round(ret5,2) if ret5 is not None else None,
            'daily_ret10': round(ret10,2) if ret10 is not None else None,
            'daily_ret20': round(ret20,2) if ret20 is not None else None,
            'weekly_direction': weekly_dir, 'regime': state, 'regime_reason': reason}


def decide(stock, regime):
    vs75 = _f(stock.get('vs75')); ret1 = _f(stock.get('change')); ret5 = _f(stock.get('ret5')); ret10 = _f(stock.get('ret10'))
    missing = []
    checks = []

    # 1) Range-breakout branch. This is intentionally separate from bottom
    # reversal: a stock can be a valid buy after breaking out of a base even
    # when it is nowhere near a bottom.
    bo = stock.get('breakout_signal') or {}
    if (bo.get('confirmed') or regime.get('regime') == 'RANGE_BREAKOUT') and regime.get('regime') not in ('UPTREND_PULLBACK',):
        conds = [
            ('20日レンジ高値を突破', True, bo.get('breakout_level'), '直近5営業日で20日高値を終値突破'),
            ('突破日の出来高1.5倍以上', bool(bo.get('volume_ok')), bo.get('breakout_volume_ratio'), '突破日出来高比>=1.5'),
            ('現在も突破水準を維持', bool(bo.get('held')), bo.get('distance_from_breakout'), '突破水準を維持'),
            ('20日MAより上', bool(bo.get('ma20_ok')), stock.get('vs20'), '現在値>20日MA'),
            ('5日騰落率がプラス', bool(bo.get('trend_ok')), bo.get('ret5'), '5日騰落率>0%'),
        ]
        checks = [{'label':a,'ok':bool(b),'value':c,'rule':d} for a,b,c,d in conds]
        missing = [c['label'] for c in checks if not c['ok']]
        if not missing:
            signal='BUY_CANDIDATE'
            reason='レンジブレイク中・再上昇型。レンジ高値突破、突破日の出来高1.5倍以上、突破水準維持、20日MA上、5日騰落率プラスの5条件がすべて成立。'
        else:
            signal='WATCH'
            reason='レンジブレイク中だが、買い条件未達。レンジ高値突破後の出来高・突破水準維持・20日MA上・短期上昇をすべて確認してからBUY候補。'
        candle=stock.get('candle_signal') or {}
        if candle.get('status') in ('大底反転サイン','反転サイン'):
            reason += f" 🕯️{candle.get('status')}"
        return {'signal':signal,'signal_reason':reason,'missing_conditions':missing,
                'condition_checks':checks,'one_condition_away':len(missing)==1,'signal_type':'RANGE_BREAKOUT'}

    # 2) Very strict multi-timeframe bottom branch.
    # Weekly RSI<=30 is combined with either a monthly MACD golden cross or a
    # two-month improvement in the negative histogram toward zero.
    monthly_bottom = stock.get('monthly_rsi_bottom_signal') or {}
    if monthly_bottom.get('active'):
        checks = [
            {'label':'週足RSI30以下','ok':bool(monthly_bottom.get('weekly_rsi_ok')),'value':stock.get('rsi14_weekly'),'rule':'週足RSI(14)<=30'},
            {'label':'月足MACD転換','ok':True,'value':monthly_bottom.get('state'),'rule':'月足MACDがGC、またはGC手前でヒストグラムが2か月連続縮小'},
        ]
        signal='BUY_CANDIDATE'
        reason='厳格な大底候補サイン。週足RSI30以下に加え、月足MACDがゴールデンクロス、またはGC手前でMACDとシグナルの差（ヒストグラム）が2か月連続で縮小。通常の押し目BUYとは別系統の長期底打ち候補として扱う。'
        return {'signal':signal,'signal_reason':reason,'missing_conditions':[],
                'condition_checks':checks,'one_condition_away':False,'signal_type':'MONTHLY_BOTTOM'}

    # 2) Bottom-reversal branch. Being positive on the day alone is NOT enough.
    if regime['regime'] in ('DOWNTREND_REVERSAL_WAIT','DOWNTREND_REVERSAL_CONFIRMED'):
        conds = [
            ('75日MAより5%以上下', vs75 is not None and vs75 <= -5, vs75, 'vs75<=-5%'),
            ('5日騰落率が-5%以上', ret5 is not None and ret5 >= -5, ret5, '5日騰落率>=-5%'),
            ('10日騰落率が-10%以上', ret10 is not None and ret10 >= -10, ret10, '10日騰落率>=-10%')
        ]
        for label, ok, value, rule in conds:
            checks.append({'label': label, 'ok': bool(ok), 'value': value, 'rule': rule})
            if not ok: missing.append(label)
        candle = stock.get('candle_signal') or {}
        if regime['regime'] == 'DOWNTREND_REVERSAL_CONFIRMED' and not missing and candle.get('status') == '大底反転サイン':
            signal='BUY_CANDIDATE'; reason='下降トレンド中だが、20日MA回復・大底反転サイン・反転条件を確認した暫定買い候補。'
        else:
            signal='WATCH'
            reason='下降トレンドの反転待ち。BUYにはしません。未達/確認待ち: ' + (' / '.join(missing) if missing else '大底反転確認または20日MA回復')
    elif regime['regime'] == 'UPTREND_PULLBACK':
        # Pullback is NOT a breakout setup.  We deliberately moved the
        # primary entry reference from 20MA to the more commonly watched
        # Japanese 25MA.  A much deeper 75MA pullback is a second-stage setup
        # and needs a little more evidence because it can also mean trend damage.
        vs25 = _f(stock.get('vs25'))
        vs75 = _f(stock.get('vs75'))
        ret20 = _f(stock.get('ret20'))
        ma25_slope5 = _f(regime.get('daily_ma25_slope5'))
        ma75_slope20 = _f(regime.get('daily_ma75_slope20'))
        rsi = _f(stock.get('rsi14'))

        near_25ma = vs25 is not None and -4.0 <= vs25 <= 0.5
        actual_pullback = ret5 is not None and ret5 <= -3.0
        ma25_rising = ma25_slope5 is not None and ma25_slope5 > 0
        deep_75ma = (vs75 is not None and -3.0 <= vs75 <= 3.0 and
                     ret20 is not None and ret20 <= -5.0 and
                     ma75_slope20 is not None and ma75_slope20 > 0)
        weekly_up = regime.get('weekly_direction') == 'UP'
        rsi_primary_ok = rsi is not None and rsi < 65
        rsi_deep_ok = rsi is not None and rsi < 55
        primary_ok = weekly_up and near_25ma and actual_pullback and ma25_rising and rsi_primary_ok
        deep_ok = weekly_up and deep_75ma and rsi_deep_ok
        checks = [
            {'label':'上昇トレンドの土台','ok':regime.get('weekly_direction')=='UP','value':regime.get('weekly_direction'),'rule':'週足方向=UP','group':'通常の25日MA押し目'},
            {'label':'25日MA付近まで調整','ok':near_25ma,'value':vs25,'rule':'25日MA乖離が-4%〜+0.5%','group':'通常の25日MA押し目'},
            {'label':'5日で3%以上調整','ok':actual_pullback,'value':ret5,'rule':'5日騰落率<=-3%','group':'通常の25日MA押し目'},
            {'label':'25日MAが上向き','ok':ma25_rising,'value':ma25_slope5,'rule':'25日MAの5日傾き>0%','group':'通常の25日MA押し目'},
            {'label':'RSIが過熱していない','ok':rsi_primary_ok,'value':rsi,'rule':'通常押し目はRSI<65','group':'通常の25日MA押し目'},
            {'label':'75日MA付近まで深押し','ok':deep_75ma,'value':vs75,'rule':'75日MA±3%、20日騰落<=-5%、75日MA上向き','group':'深い75日MA押し目'},
            {'label':'深押し時のRSI','ok':rsi_deep_ok,'value':rsi,'rule':'深い押し目はRSI<55','group':'深い75日MA押し目'},
        ]
        primary_missing = [c['label'] for c in checks[:5] if not c['ok']]
        deep_missing = [c['label'] for c in checks[5:] if not c['ok']]
        if primary_ok:
            missing=[]
            signal='BUY_CANDIDATE'
            reason='上昇押し目の通常ルート。週足が上昇方向で、25日MA付近、5日で3%以上調整、25日MA上向き、RSI65未満の5条件がすべて成立。当日の値動きはBUY条件に使わない。'
        elif deep_ok:
            missing=[]
            signal='BUY_CANDIDATE'
            reason='上昇押し目の深押しルート。週足が上昇方向で、75日MA付近まで調整、20日で5%以上下落、75日MA上向き、RSI55未満の条件がすべて成立。75日MA割れ・直近安値割れは損切り警戒。'
        elif near_25ma or deep_75ma:
            missing = primary_missing if near_25ma else deep_missing
            signal='WATCH'
            reason='押し目ゾーンには入っているがBUY条件未達。通常ルートは5条件、深押しルートは3条件をすべて確認してからBUY候補にします。当日の値動きだけではBUYにしません。'
        else:
            missing = primary_missing
            signal='WATCH'
            reason='上昇トレンドだが現在は押し目BUY水準ではない。20日MAだけでBUYにせず、まず25日MAを第1の押し目目安、75日MAを第2の深い押し目目安として待つ。'
    elif regime['regime'] == 'UPTREND':
        # Established uptrend: do not turn every strong stock into BUY.
        # Require a fresh 5-session high with expanding volume while the
        # medium-term trend remains healthy.
        vs25 = _f(stock.get('vs25'))
        ma25_slope5 = _f(regime.get('daily_ma25_slope5'))
        rsi = _f(stock.get('rsi14'))
        weekly_up = regime.get('weekly_direction') == 'UP'
        above_25 = vs25 is not None and vs25 > 0
        ma25_rising = ma25_slope5 is not None and ma25_slope5 > 0
        momentum = bool(stock.get('breakout5_prev'))
        volume_ok = _f(stock.get('volume_ratio')) is not None and _f(stock.get('volume_ratio')) >= 1.2
        rsi_ok = rsi is not None and 50 <= rsi < 70
        checks=[
            {'label':'週足上昇トレンド','ok':weekly_up,'value':regime.get('weekly_direction'),'rule':'週足方向=UP'},
            {'label':'25日MAより上','ok':above_25,'value':vs25,'rule':'25日MA乖離>0%'},
            {'label':'25日MAが上向き','ok':ma25_rising,'value':ma25_slope5,'rule':'25日MAの5日傾き>0%'},
            {'label':'直近5営業日高値を更新','ok':momentum,'value':stock.get('high5_prev'),'rule':'現在終値>直前5営業日の高値'},
            {'label':'出来高が平均超','ok':volume_ok,'value':stock.get('volume_ratio'),'rule':'当日出来高÷直前20日平均>=1.2倍'},
            {'label':'RSIが健全な上昇域','ok':rsi_ok,'value':rsi,'rule':'RSI50以上70未満'},
        ]
        missing=[c['label'] for c in checks if not c['ok']]
        if not missing:
            signal='BUY_CANDIDATE'
            reason='上昇トレンドの再上昇型。週足上昇、25日MA上・上向き、直近5営業日高値更新、出来高1.2倍以上、RSI50〜70の6条件が成立。'
        else:
            signal='WATCH'
            reason='上昇トレンド継続中。ただし追いかけ買いはせず、再上昇の6条件（週足上昇・25日MA上・25日MA上向き・5日高値更新・出来高1.2倍・RSI50〜70）が揃うまでWATCH。押し目になれば「上昇押し目」へ分類。'
    elif regime['regime'] == 'RANGE_TRANSITION':
        signal='WATCH'
        reason=(f"レンジ転換監視。現在値{bo.get('current_price'):.0f}円 / レンジ上限{bo.get('breakout_level'):.0f}円 / 明確なブレイク目安{bo.get('breakout_trigger'):.0f}円。"
                 f"出来高は現在{bo.get('current_volume'):.0f}株 / 直前20日平均{bo.get('avg_volume20'):.0f}株 / 判定目安{bo.get('required_volume'):.0f}株（1.5倍）。"
                 if all(bo.get(k) is not None for k in ('current_price','breakout_level','breakout_trigger','current_volume','avg_volume20','required_volume'))
                 else 'レンジ転換監視。上限ブレイクと出来高増加などを確認するまではBUYにしない。')
        checks=[
            {'label':'週足方向','ok':regime.get('weekly_direction')=='RANGE','value':regime.get('weekly_direction'),'rule':'週足がRANGE'},
            {'label':'上限ブレイク','ok':False,'value':bo.get('breakout_trigger'),'rule':f"終値{bo.get('breakout_trigger')}円以上"},
            {'label':'出来高増','ok':False,'value':bo.get('required_volume'),'rule':f"突破日の出来高{bo.get('required_volume')}株以上（直前20日平均×1.5）"},
        ]
    elif regime['regime'] == 'DOWNTREND_CONTINUED':
        candle = stock.get('candle_signal') or {}
        weekly_rsi = _f(stock.get('rsi14_weekly'))
        vs20 = _f(regime.get('daily_vs20'))
        stabilized = ret1 is not None and ret1 > 0 and ret5 is not None and ret5 >= -5 and ((ret10 is not None and ret10 < 0) or (vs20 is not None and vs20 > 0))
        ma20_recovered = vs20 is not None and vs20 >= 0
        candle_ok = candle.get('status') == '大底反転サイン'
        rsi_rebound = weekly_rsi is not None and weekly_rsi <= 35
        checks=[
            {'label':'日足が下げ止まり','ok':stabilized,'value':ret1,'rule':'当日プラス・5日騰落>=-5%・10日下落または20日MA上'},
            {'label':'20日MAを回復','ok':ma20_recovered,'value':vs20,'rule':'20日MA乖離>=0%'},
            {'label':'大底反転サイン','ok':candle_ok,'value':candle.get('status'),'rule':'大底反転サインを確認'},
            {'label':'週足RSIが売られ過ぎ域','ok':rsi_rebound,'value':weekly_rsi,'rule':'週足RSI<=35'},
        ]
        signal='AVOID'
        reason='下降継続のため現時点では買い回避。BUY候補にするには、下げ止まり→20日MA回復→大底反転サイン→週足RSI35以下の確認が必要。'
        missing=[c['label'] for c in checks if not c['ok']]
    else:
        signal='INSUFFICIENT'; reason='データ不足。'

    candle=stock.get('candle_signal') or {}
    if candle.get('status') in ('大底反転サイン','反転サイン'):
        reason += f" 🕯️{candle.get('status')}：{candle.get('reason','')}"
    if bo.get('status') in ('レンジ抜け候補','ブレイク失敗警戒'):
        reason += f" 📈{bo.get('status')}：{bo.get('reason','')}"
    signal_type = {
        'DOWNTREND_REVERSAL_WAIT':'BOTTOM_REVERSAL',
        'DOWNTREND_REVERSAL_CONFIRMED':'BOTTOM_REVERSAL',
        'UPTREND_PULLBACK':'UPTREND_PULLBACK',
        'RANGE_BREAKOUT':'RANGE_BREAKOUT',
        'UPTREND':'UPTREND',
        'RANGE_TRANSITION':'RANGE_TRANSITION',
        'DOWNTREND_CONTINUED':'DOWNTREND_CONTINUED',
    }.get(regime.get('regime'), 'UNKNOWN')
    return {'signal':signal,'signal_reason':reason,'missing_conditions':missing,
            'condition_checks':checks,'one_condition_away':len(missing)==1,
            'signal_type':signal_type}
