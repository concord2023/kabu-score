"""Scan all listed ordinary companies for the exact kabu-score BUY condition.

This scanner deliberately reuses the same `calc() -> classify_regime() -> decide()`
path as the watchlist, so the recommendation page cannot silently drift away from
the app's BUY rules.
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
HISTORY_MINIMUM = 800  # enough for roughly 30 monthly closes plus daily indicators
REQUEST_GAP = 1.05


def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))


def load_master():
    payload = json.loads(MASTER.read_text(encoding='utf-8'))
    companies = payload.get('companies', payload if isinstance(payload, list) else [])
    return [x for x in companies if x.get('code') and x.get('name')]


def compact(stock, regime, decision, company):
    keys = (
        'price', 'change', 'vs20', 'vs25', 'vs75', 'vs200', 'ret5', 'ret10', 'ret20',
        'rsi14', 'rsi14_weekly', 'monthly_macd', 'monthly_macd_signal', 'monthly_macd_hist', 'monthly_macd_state', 'monthly_points', 'monthly_rsi_bottom_signal', 'volume_ratio', 'ma20', 'ma25', 'ma75', 'ma200',
        'daily_vs20', 'daily_ma20_slope5', 'weekly_direction', 'weekly_points',
        'candle_signal', 'breakout_signal', 'signal_icons', 'score', 'score_max',
        'condition_checks', 'interpretation'
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
    scanned = 0
    errors = 0
    insufficient = 0
    last_progress = time.monotonic()
    breadth = None

    for company in companies:
        code = str(company['code']).strip().upper()
        try:
            rows, _ = ud.get_all_prices(code, minimum=HISTORY_MINIMUM)
            # Market breadth is context only. Fetch it once using the first valid
            # trading date and continue even if the public breadth source fails.
            if breadth is None and rows:
                try:
                    breadth = ud.fetch_breadth_and_nikkei(rows[0].get('date'))
                except Exception as exc:
                    print(f'Breadth unavailable: {exc}')
                    breadth = {}
            stock = ud.calc(rows, breadth_info=breadth or {}, supply_info=None)
            regime = classify_regime(rows, stock.get('candle_signal'))
            decision = decide(stock, regime)
            scanned += 1
            if decision.get('signal') == 'BUY_CANDIDATE':
                recommendations.append(compact(stock, regime, decision, company))
        except Exception as exc:
            errors += 1
            if 'Not enough price history' in str(exc):
                insufficient += 1
        if time.monotonic() - last_progress > 60:
            print(f'Scan progress: {scanned + errors}/{len(companies)}; BUY={len(recommendations)}')
            last_progress = time.monotonic()

    # Keep a stable, useful order: stricter/clearer pullback candidates first,
    # then bottom/reversal/breakout candidates by score.
    def sort_key(x):
        d = x.get('details') or {}
        regime = d.get('regime')
        regime_order = {
            'UPTREND_PULLBACK': 0,
            'DOWNTREND_REVERSAL_CONFIRMED': 1,
            'DOWNTREND_REVERSAL_WAIT': 2,
        }
        return (regime_order.get(regime, 9), -(d.get('score') or 0), x['code'])

    recommendations.sort(key=sort_key)
    output = {
        'updated_at': now_jst().isoformat(),
        'source': 'IRBANK API / JPX listed-company master',
        'rule': 'kabu-score の decide() が BUY_CANDIDATE と判定した銘柄のみ。総合BUY条件をそのまま使用。',
        'universe_count': len(companies),
        'scanned_count': scanned,
        'insufficient_count': insufficient,
        'error_count': errors,
        'recommendation_count': len(recommendations),
        'recommendations': recommendations,
        'note': 'このページはアプリのBUY条件に合致した銘柄を抽出したもの。売買の最終判断は別途確認してください。',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: output[k] for k in ('universe_count','scanned_count','insufficient_count','error_count','recommendation_count')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
