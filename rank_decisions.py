import json
from datetime import datetime, timezone, timedelta

with open('data/stocks.json', encoding='utf-8') as f:
    payload = json.load(f)
with open('watchlist.json', encoding='utf-8') as f:
    watch = json.load(f).get('stocks', [])

priority = {'BUY_CANDIDATE':0,'WATCH':1,'INSUFFICIENT':2,'AVOID':3}
state_bonus = {'UPTREND_PULLBACK':0,'DOWNTREND_REVERSAL_WAIT':1,'UPTREND':2,'RANGE_TRANSITION':3,'DOWNTREND_CONTINUED':4,'UNKNOWN':5}
items = []

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
            'score': None, 'sort_key': (2, 9, 9, 999)
        })
        continue
    sig = s.get('signal', 'INSUFFICIENT')
    rs = s.get('relative_strength')
    items.append({
        'code': code, 'name': s.get('name', default_name), 'price': s.get('price'), 'change': s.get('change'),
        'regime': s.get('regime'), 'regime_reason': s.get('regime_reason'), 'signal': sig,
        'signal_reason': s.get('signal_reason'), 'one_condition_away': s.get('one_condition_away', False),
        'missing_conditions': s.get('missing_conditions', []), 'relative_strength': rs, 'score': s.get('score'),
        'sort_key': (priority.get(sig, 9), 0 if s.get('one_condition_away') else 1,
                     state_bonus.get(s.get('regime'), 9), -(rs if rs is not None else -999))
    })

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

out = {'updated_at': datetime.now(timezone(timedelta(hours=9))).isoformat(), 'summary': summary, 'ranking': items}
with open('data/decision_ranking.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))
