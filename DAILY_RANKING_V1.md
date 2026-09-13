# Daily ranking v1

## Policy
- Do not force a BUY signal.
- If no stock satisfies its state-specific entry rule, show `今日は買い候補なし`.
- Rank BUY candidates first, then WATCH, then AVOID.
- Within WATCH, a more complete validated setup ranks above an early setup.
- Trend regime is a context field, not a probability of future return.

## Display target
1. 今日の結論
2. BUY候補（あれば）
3. WATCH: 「あと何が必要か」
4. AVOID
5. 各銘柄の状態（上昇／押し目／反転待ち／下落継続／判定不能）

## Important limitation
The current uptrend/pullback branches are monitoring-only until separate historical entry rules are validated. The validated Kioxia reversal result is not proof that the same rule works for all stocks or all regimes.
