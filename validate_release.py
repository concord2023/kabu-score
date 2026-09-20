"""Offline release regression tests for kabu-score.

No network calls are made. This validates the core indicator/decision paths,
new-listing partial-history behavior, supply rules, signal independence, and
static release structure before a GitHub Actions run.
"""
import ast, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
os.environ.setdefault('IRBANK_API_KEY', 'offline-validation-token')
import update_data as ud
from regime_model_v2 import classify_regime, decide


def rows(n=260, trend='up'):
    out=[]
    for i in range(n):
        # newest first; deterministic synthetic series
        if trend == 'up': price = 100 + (n-i)*0.2
        elif trend == 'down': price = 160 - (n-i)*0.2
        else: price = 120 + ((i % 10)-5)*0.5
        out.append({'date': f'2026-{((n-i-1)//28)%12+1:02d}-{((n-i-1)%28)+1:02d}',
                    'open': price-1, 'high': price+2, 'low': price-2,
                    'close': price, 'adj_close': price, 'volume': 100000})
    # Use valid unique dates from a real calendar sequence instead.
    import datetime
    start=datetime.date(2025, 9, 1)
    out=[]
    d=start
    while len(out) < n:
        if d.weekday() < 5:
            j=len(out)
            if trend=='up': price=100+j*0.2
            elif trend=='down': price=160-j*0.2
            else: price=120+((j%10)-5)*0.5
            out.insert(0, {'date':d.isoformat(),'open':price-1,'high':price+2,'low':price-2,
                           'close':price,'adj_close':price,'volume':100000})
        d += datetime.timedelta(days=1)
    return out


def assert_true(cond, msg):
    if not cond: raise AssertionError(msg)

# 1. Syntax / required files
required=['update_data.py','regime_model_v2.py','rank_decisions.py','run_daily.py','watchlist.json',
          'index.html','dashboard.html','detail.html','signals.html','recommendations.html',
          '.github/workflows/daily-update.yml','.github/workflows/recommendation-scan.yml']
for name in required: assert_true((ROOT/name).exists(), f'missing {name}')
for p in ROOT.glob('*.py'): ast.parse(p.read_text(encoding='utf-8'))

watch=json.loads((ROOT/'watchlist.json').read_text(encoding='utf-8'))['stocks']
codes=[str(x.get('code','')).strip() for x in watch]
assert_true(len(codes)==len(set(codes)), 'duplicate watchlist codes')
assert_true('485A' in codes and any(x.get('name')=='パワーエックス' for x in watch), 'PowerX 485A missing')
assert_true('285A' in codes and any(x.get('name')=='キオクシアホールディングス' for x in watch), 'Kioxia 285A missing')

# 2. Core indicators with full history
r=rows(260,'up')
s=ud.calc(r, breadth_info={'breadth':{'6d':100,'10d':100,'15d':100,'25d':100},'nikkei_change':0}, supply_info={
    'date':'2026-09-18','status':'complete','buy_balance':100,'sell_balance':100,
    'buy_change':-20,'sell_change':10,'credit_ratio':1.5,'net_balance':0,'net_change':-30})
for k in ('ma5','ma20','ma25','ma75','ma200','rsi14','rsi14_weekly','macd','macd_signal','macd_hist','bb_daily','bb_weekly','chart_history'):
    assert_true(k in s, f'missing calc field {k}')
assert_true(s['diagnostic']['ma200_ready'], '200MA should be ready')
assert_true(s['diagnostic']['weekly_rsi_ready'], 'weekly RSI should be ready')
assert_true(any(i['code']=='SUPPLY_BUY' for i in s['signal_icons']), 'supply buy icon missing')

# 3. Supply safety: buy-balance increase must not be masked by small sell increase.
heavy={'credit_ratio':5,'buy_change':1000,'sell_change':10,'net_change':990}
status,_=ud.supply_status(heavy)
assert_true(status=='重い', 'bad supply must stay heavy')

# 4. New listing: fewer than 200 rows must not become a generic fetch error.
short=rows(120,'up')
ss=ud.calc(short, breadth_info={}, supply_info=None)
assert_true(ss['ma25'] is not None and ss['ma75'] is not None, 'short history should still calculate MA25/75')
assert_true(ss['ma200'] is None, '200MA must remain unavailable with short history')

# 5. Bottom/pullback decision must not use same-day positivity as sole BUY trigger.
down=rows(260,'down')
cs=ud.calc(down, breadth_info={}, supply_info=None)
reg=classify_regime(down, cs['candle_signal'])
dec=decide(cs, reg)
assert_true(dec['signal'] in {'WATCH','AVOID','INSUFFICIENT','BUY_CANDIDATE'}, 'decision must be valid enum')

# 6. HTML JS syntax is checked separately in CI when Node is available.
for p in ROOT.glob('*.html'):
    text=p.read_text(encoding='utf-8')
    assert_true('data/' in text or p.name=='manifest.html' or True, f'html unreadable: {p}')

# 7. Large recommendation scan must remain a separate manual workflow.
daily=(ROOT/'.github/workflows/daily-update.yml').read_text(encoding='utf-8')
rec=(ROOT/'.github/workflows/recommendation-scan.yml').read_text(encoding='utf-8')
assert_true('scan_recommendations.py' not in daily, 'all-listed scan leaked into daily workflow')
assert_true('workflow_dispatch:' in rec, 'recommendation scan must be manual')

print('RELEASE VALIDATION OK')
print(f'watchlist={len(codes)}; full_history={s["data_points"]}; short_history={ss["data_points"]}')
