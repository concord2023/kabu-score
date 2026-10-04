import json
from datetime import datetime, timezone, timedelta

with open('data/stocks.json', encoding='utf-8') as f:
    payload = json.load(f)
with open('watchlist.json', encoding='utf-8') as f:
    watch = json.load(f).get('stocks', [])

priority = {'BUY_CANDIDATE':0,'WATCH':1,'INSUFFICIENT':2,'AVOID':3}
state_bonus = {'DOWNTREND_REVERSAL_CONFIRMED':0,'UPTREND_PULLBACK':1,'RANGE_BREAKOUT':2,'DOWNTREND_REVERSAL_WAIT':3,'UPTREND':4,'RANGE_TRANSITION':5,'DOWNTREND_CONTINUED':6,'UNKNOWN':7}
items = []

def pick_metrics(s):
    keys = [
        'date','price','change','volume','ma5','ma20','ma25','ma75','ma200','ma60','vs5','vs20','vs25','vs75','vs200','vs60','ret5','ret10','ret20','rsi14','rsi14_weekly',
        'volume_ratio','macd','macd_signal','macd_hist','monthly_macd','monthly_macd_signal','monthly_macd_hist','monthly_macd_state','monthly_points','monthly_rsi_bottom_signal','drawdown60','range_position60','volatility20','bb_daily','bb_weekly','weekly_ma5','weekly_ma13',
        'weekly_ma26','weekly_ma52','weekly_vs13','weekly_vs26','weekly_vs52',
        'weekly_ma13_slope4w','monthly_ma6','monthly_ma12','monthly_ma18','weekly_ma26_slope4w','daily_vs20','daily_ma20_slope5','supply','supply_status',
        'supply_reason','nikkei_change','relative_strength','breadth','daily_vs25','daily_vs75','daily_ma25_slope5','daily_ma75_slope20','score','score_breakdown',
        'condition_checks','signal_type','candle_signal','breakout_signal','signal_icons','chart_history','per_history','per_5y_avg','per_forecast','pbr','dividend_yield','valuation_source','valuation_error','per_forecast_error','per_updated_at','per_actual_count','per_forecast_eps','per_forecast_pe','per_actual_attribution','chart_history_weekly','chart_history_monthly','interpretation','attribution','high20','low20','high60','low60','weekly_direction','weekly_points','daily_ret1','daily_ret5','daily_ret10','daily_ret20'
    ]
    return {k:s.get(k) for k in keys if k in s}

for item in watch:
    if isinstance(item, str):
        code, default_name = item, item
    else:
        code, default_name = str(item.get('code')), item.get('name') or str(item.get('code'))
    s = payload.get('stocks', {}).get(code) or {}
    if s.get('error') or s.get('price') is None:
        items.append({
            'code': code, 'name': s.get('name', default_name), 'price': None, 'change': None,
            'regime': 'UNKNOWN', 'regime_reason': s.get('error') or '日次データを取得できませんでした。',
            'signal': 'INSUFFICIENT', 'signal_reason': 'データ取得失敗。次回更新で再試行します。',
            'one_condition_away': False, 'missing_conditions': [], 'relative_strength': None,
            'score': None, 'details': {}, 'sort_key': (2, 9, 9, 999)
        })
        continue
    sig = s.get('signal', 'INSUFFICIENT')
    rs = s.get('relative_strength')

    # Keep the displayed regime synchronized with the actual buy-signal route.
    # A previous failure mode left RANGE_BREAKOUT in signal_type while regime
    # remained UNKNOWN, producing a misleading '判定不能' label in the UI.
    # RANGE_BREAKOUT is the more specific current-state classification, so it
    # must win over a stale/unknown generic regime when the breakout route is
    # active.
    regime = s.get('regime')
    signal_type = s.get('signal_type')
    bo = s.get('breakout_signal') or {}
    breakout_active = bool(bo.get('is_breakout')) and bo.get('status') in ('レンジ抜け・再上昇', 'レンジ抜け候補')
    if signal_type == 'RANGE_BREAKOUT' or breakout_active:
        regime = 'RANGE_BREAKOUT'
        if signal_type != 'RANGE_BREAKOUT':
            signal_type = 'RANGE_BREAKOUT'

    items.append({
        'code': code, 'name': s.get('name', default_name), 'price': s.get('price'), 'change': s.get('change'),
        'regime': regime, 'regime_reason': ('レンジブレイク中・再上昇型。' + (s.get('regime_reason') or '') if regime == 'RANGE_BREAKOUT' and s.get('regime') != 'RANGE_BREAKOUT' else s.get('regime_reason')), 'signal': sig,
        'signal_reason': s.get('signal_reason'), 'one_condition_away': s.get('one_condition_away', False),
        'missing_conditions': s.get('missing_conditions', []), 'relative_strength': rs, 'score': s.get('score'), 'signal_icons': s.get('signal_icons', []), 'signal_type': signal_type or s.get('signal_type'), 'details': pick_metrics(s),
        'sort_key': (priority.get(sig, 9), 0 if s.get('one_condition_away') else 1,
                     state_bonus.get(s.get('regime'), 9), -(rs if rs is not None else -999))
    })

# Hard integrity check: every watchlist stock must remain in the public
# ranking, even when its analysis failed.  A classification/data exception
# must become INSUFFICIENT, never a disappearing row.
watch_codes = {str((x.get('code') if isinstance(x, dict) else x)) for x in watch}
item_codes = {str(x.get('code')) for x in items}
missing_codes = sorted(watch_codes - item_codes)
if missing_codes:
    raise SystemExit('Ranking integrity failure: watchlist stocks missing from ranking: ' + ','.join(missing_codes))

items.sort(key=lambda x: x['sort_key'])
for i, x in enumerate(items, 1):
    x['rank'] = i
    x.pop('sort_key', None)

summary = {
    'buy_candidates': sum(x['signal']=='BUY_CANDIDATE' for x in items),
    'watch': sum(x['signal']=='WATCH' for x in items),
    'avoid': sum(x['signal']=='AVOID' for x in items),
    'insufficient': sum(x['signal']=='INSUFFICIENT' for x in items),
    'total': len(items),
    'today_message': '今日は買い候補なし' if not any(x['signal']=='BUY_CANDIDATE' for x in items) else '今日の買い候補あり'
}

out = {'updated_at': datetime.now(timezone(timedelta(hours=9))).isoformat(), 'summary': summary,
       'integrity': {'watchlist_count': len(watch_codes), 'ranking_count': len(item_codes),
                     'missing_codes': missing_codes, 'status': 'ok'},
       'ranking': items}
with open('data/decision_ranking.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))
