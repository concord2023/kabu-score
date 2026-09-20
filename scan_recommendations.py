"""Two-stage scan for strict kabu-score BUY candidates.

Stage 1 fetches only recent daily prices and computes weekly RSI(14).  Only
stocks at or below the strict weekly-RSI<=30 threshold proceed to stage 2,
where the full history needed for monthly MACD and the normal kabu-score
BUY decision is fetched.

This keeps the manual all-listed scan useful without downloading 1000 trading
days for every listed company.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import update_data as ud
from regime_model_v2 import classify_regime, decide

MASTER = Path('data/company_master.json')
OUT = Path('data/recommendations.json')
RECENT_DAYS = 120          # enough for >=15 weekly closes for RSI(14)
HISTORY_MINIMUM = 1000      # enough for monthly MACD + daily/weekly indicators
REQUEST_GAP = 1.05


def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))


def load_master():
    payload = json.loads(MASTER.read_text(encoding='utf-8'))
    companies = payload.get('companies', payload if isinstance(payload, list) else [])
    return [x for x in companies if x.get('code') and x.get('name')]


def fetch_recent_prices(code, limit=RECENT_DAYS):
    """Fetch only a compact recent window for the cheap first-stage screen."""
    data = ud.get(f'/securities/{code}/prices', {'limit': limit})
    rows = []
    seen = set()
    for x in data.get('prices') or []:
        d = x.get('date')
        if not d or d in seen:
            continue
        if x.get('close') is None and x.get('adj_close') is None:
            continue
        seen.add(d)
        rows.append(x)
    rows.sort(key=lambda x: x['date'], reverse=True)
    if not rows:
        raise ValueError(f'No recent price rows returned for {code}')
    return rows


def compact(stock, regime, decision, company):
    keys = (
        'price', 'change', 'vs20', 'vs25', 'vs75', 'vs200', 'ret5', 'ret10', 'ret20',
        'rsi14', 'rsi14_weekly', 'monthly_macd', 'monthly_macd_signal', 'monthly_macd_hist',
        'monthly_macd_state', 'monthly_points', 'monthly_rsi_bottom_signal', 'volume_ratio',
        'ma20', 'ma25', 'ma75', 'ma200', 'daily_vs20', 'daily_ma20_slope5', 'weekly_direction',
        'weekly_points', 'candle_signal', 'breakout_signal', 'signal_icons', 'score', 'score_max',
        'condition_checks', 'interpretation', 'attribution'
    )
    details = {k: stock.get(k) for k in keys if k in stock}
    details.update({
        'regime': regime.get('regime'),
        'regime_reason': regime.get('regime_reason'),
        'weekly_ma13': regime.get('weekly_ma13'),
        'weekly_ma26': regime.get('weekly_ma26'),
        'weekly_ma13_slope4w': regime.get('weekly_ma13_slope4w'),
        'weekly_ma26_slope4w': regime.get('weekly_ma26_slope4w'),
        'signal_reason': decision.get('signal_reason'),
        'missing_conditions': decision.get('missing_conditions', []),
    })
    return {
        'code': str(company['code']),
        'name': company['name'],
        'market': company.get('market'),
        'industry': company.get('industry'),
        'signal': decision.get('signal'),
        'reason': decision.get('signal_reason'),
        'details': details,
    }


def main():
    if not os.environ.get('IRBANK_API_KEY'):
        raise SystemExit('IRBANK_API_KEY is not set')

    companies = load_master()
    recommendations = []
    stage1_checked = 0
    stage1_candidates = 0
    stage2_scanned = 0
    errors = 0
    insufficient = 0
    last_progress = time.monotonic()
    breadth = None

    for company in companies:
        code = str(company['code']).strip().upper()
        try:
            # Stage 1: cheap strict filter.  No full-history request unless
            # weekly RSI(14) is already <= 30.
            recent = fetch_recent_prices(code)
            weekly_vals = ud.weekly_closes(recent)
            weekly_rsi = ud.rsi14(weekly_vals)
            stage1_checked += 1
            if weekly_rsi is None or weekly_rsi > 30:
                continue
            stage1_candidates += 1

            # Stage 2: only the strict weekly-RSI candidates get the long
            # history needed to evaluate monthly MACD and the full BUY model.
            rows, _ = ud.get_all_prices(code, minimum=HISTORY_MINIMUM)
            if breadth is None and rows:
                try:
                    breadth = ud.fetch_breadth_and_nikkei(rows[0].get('date'))
                except Exception as exc:
                    print(f'Breadth unavailable: {exc}')
                    breadth = {}
            stock = ud.calc(rows, breadth_info=breadth or {}, supply_info=None)
            regime = classify_regime(rows, stock.get('candle_signal'))
            decision = decide(stock, regime)
            stage2_scanned += 1
            if decision.get('signal') == 'BUY_CANDIDATE':
                recommendations.append(compact(stock, regime, decision, company))
        except Exception as exc:
            errors += 1
            if 'Not enough price history' in str(exc):
                insufficient += 1
        if time.monotonic() - last_progress > 30:
            print(
                f'Scan progress: stage1={stage1_checked}/{len(companies)} '
                f'candidates={stage1_candidates}; stage2={stage2_scanned}; '
                f'BUY={len(recommendations)}; errors={errors}'
            )
            last_progress = time.monotonic()

    def sort_key(x):
        d = x.get('details') or {}
        regime = d.get('regime')
        regime_order = {
            'UPTREND_PULLBACK': 0,
            'DOWNTREND_REVERSAL_CONFIRMED': 1,
            'DOWNTREND_REVERSAL_WAIT': 2,
        }
        # Strict monthly-MACD bottom candidates first within their group.
        bottom = 0 if (d.get('monthly_rsi_bottom_signal') or {}).get('active') else 1
        return (bottom, regime_order.get(regime, 9), -(d.get('score') or 0), x['code'])

    recommendations.sort(key=sort_key)
    output = {
        'updated_at': now_jst().isoformat(),
        'source': 'IRBANK API / JPX listed-company master',
        'rule': '2段階スキャン。一次選別は週足RSI(14)<=30、通過銘柄だけ月足MACDとkabu-scoreのBUY条件を詳細判定。',
        'universe_count': len(companies),
        'stage1_checked_count': stage1_checked,
        'stage1_candidate_count': stage1_candidates,
        'stage2_scanned_count': stage2_scanned,
        'insufficient_count': insufficient,
        'error_count': errors,
        'recommendation_count': len(recommendations),
        'recommendations': recommendations,
        'note': '全上場銘柄を重い長期履歴で一括解析せず、週足RSI30以下を一次選別してから詳細分析する。',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        k: output[k] for k in (
            'universe_count', 'stage1_checked_count', 'stage1_candidate_count',
            'stage2_scanned_count', 'insufficient_count', 'error_count',
            'recommendation_count'
        )
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
