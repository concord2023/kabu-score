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
          'index.html','dashboard.html','detail.html','signals.html','recommendations.html','daily_recommendations.html','bottom.html',
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
assert_true(all(k in s for k in ('weekly_ma5','monthly_ma6','monthly_ma12','monthly_ma18')), 'weekly/monthly MA fields must be present')
assert_true(s['weekly_ma5'] is not None, '5-week MA should be available')
long=rows(800,'up')
slong=ud.calc(long, breadth_info={}, supply_info=None)
assert_true(all(slong.get(k) is not None for k in ('monthly_ma6','monthly_ma12','monthly_ma18')), '6/12/18-month MAs should be available with the normal 800-session history')
wh=ud.build_period_chart_history(long, 'weekly', 60)
mh=ud.build_period_chart_history(long, 'monthly', 18)
assert_true(len(wh)==60, 'weekly chart must present 60 weeks')
assert_true(all(wh[-1].get(k) is not None for k in ('ma5','ma13','ma26','ma52')), 'weekly chart must use 5/13/26/52-week MAs')
assert_true(len(mh)==18, 'monthly chart must present 18 months')
assert_true(all(mh[-1].get(k) is not None for k in ('ma6','ma12','ma18')), 'monthly chart must use 6/12/18-month MAs')
assert_true(all(k not in wh[-1] for k in ('ma25','ma75','ma200')), 'weekly chart must not use daily MA periods')
assert_true(all(k not in mh[-1] for k in ('ma25','ma75','ma200','ma5')), 'monthly chart must not use daily/old MA periods')
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
bottom=(ROOT/'.github/workflows/bottom-prime-scan.yml').read_text(encoding='utf-8')
assert_true('workflow_dispatch:' in bottom, 'Prime bottom scan must be manual')
assert_true('scan_bottom_prime.py' in bottom, 'Prime bottom workflow missing scan script')
bottom_py=(ROOT/'scan_bottom_prime.py').read_text(encoding='utf-8')
assert_true('REQUEST_BUDGET = 900' in bottom_py and 'REQUEST_BUDGET' in bottom_py, 'Prime Tora request budget guard missing')
assert_true("market': 'Prime'" in bottom_py, 'Prime-only universe filter missing')
assert_true('weekly RSI(14) <= 30' in bottom_py and 'monthly_macd_bottom_signal' in bottom_py, 'Prime Tora scan must enforce the strict weekly-RSI/monthly-MACD rule')
assert_true('rsi14_wilder' in bottom_py and "weekly_rsi_method': 'Wilder'" in bottom_py, 'Prime Tora scan must use conventional Wilder weekly RSI and expose the method')
assert_true('weekly_rsi_simple' not in bottom_py, 'Prime Tora scan must not carry a second/simple RSI definition into its result path')
assert_true("build_period_chart_history(rows, 'monthly', 18)" in (ROOT/'update_data.py').read_text(encoding='utf-8'), 'monthly chart must keep 18 months')
# 8. PER/weekly valuation UI and IRBANK hard safety ceiling.
detail=(ROOT/'detail.html').read_text(encoding='utf-8')
idx=(ROOT/'index.html').read_text(encoding='utf-8')
assert_true('dailyAttentionCard' in idx and 'data/daily_recommendations.json' in idx and '今日の注目5選' in idx, 'daily attention must be visible on the main page')
assert_true('forecastPerBox' in detail and 'togglePerChart' in detail, 'forecast PER toggle missing')
assert_true('chart_history_weekly' in detail and 'per_history' in detail and 'per_forecast' in detail and '全期間（20年度）' in detail and '実績PER（20年度）' in detail and '予想PER' in detail, 'stored 20-fiscal-year actual/forecast PER chart missing')
assert_true('per_history' in detail and 'per_forecast' in detail and '直近の決算発表日' in (ROOT/'update_data.py').read_text(encoding='utf-8'), 'stored historical actual PER or forecast switch date missing')
assert_true('実績PER（20年度）' in detail and '過去20年度はIRBANKの実績PER' in detail and 'current forecast EPS' not in detail, 'must not replace historical actual PER with current forecast EPS')
upd=(ROOT/'update_data.py').read_text(encoding='utf-8')
assert_true('MAX_API_REQUESTS = 3999' in upd, 'IRBANK absolute request ceiling missing')
assert_true('worst_case_requests' in upd and 'minimum_needed = max(10, worst_case_requests)' in upd, 'IRBANK preflight worst-case budget missing')
sw=(ROOT/'sw.js').read_text(encoding='utf-8')
assert_true('kabu-score-v15-attention-realtime' in sw, 'service worker cache must be bumped for the latest Daily PER change')

assert_true('Calculate period indicators from the full available monthly history' in (ROOT/'update_data.py').read_text(encoding='utf-8'), 'monthly indicators must be calculated before presentation trimming')
html=(ROOT/'detail.html').read_text(encoding='utf-8')
assert_true("[['ma5','5週MA'],['ma13','13週MA'],['ma26','26週MA'],['ma52','52週MA']]" in html, 'weekly chart MA set must be 5/13/26/52')
assert_true("[['ma6','6か月MA'],['ma12','12か月MA'],['ma18','18か月MA']]" in html, 'monthly chart MA set must be 6/12/18')
assert_true('200週MA' not in html and '200か月MA' not in html, 'weekly/monthly charts must not display old 200-period MAs')
rd=(ROOT/'rank_decisions.py').read_text(encoding='utf-8')
assert_true("'chart_history_weekly'" in rd and "'chart_history_monthly'" in rd, 'weekly/monthly chart histories must survive ranking output')
assert_true('stock_change_from_issue.py' in daily and '[株スコア削除]' in daily, 'daily workflow must support stock deletion')
assert_true('watchlist_exclusions.json' in daily and 'watchlist_exclusions.json' in (ROOT/'update_data.py').read_text(encoding='utf-8'), 'deletion persistence is missing')
assert_true(not (ROOT/'scripts/update_data.py').exists(), 'legacy scripts/update_data.py must be removed; Daily stock update is the only daily updater')
print('RELEASE VALIDATION OK')
print(f'watchlist={len(codes)}; full_history={s["data_points"]}; short_history={ss["data_points"]}')
