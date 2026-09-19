# Decision Model V2

## Core
- Weekly = direction
- Daily = timing
- Market = context / relative strength, never a hard veto

## States
- UPTREND
- UPTREND_PULLBACK
- DOWNTREND_REVERSAL_WAIT
- DOWNTREND_CONTINUED
- RANGE_TRANSITION
- UNKNOWN

## Provisional BUY branch
Only `DOWNTREND_REVERSAL_WAIT` can emit `BUY_CANDIDATE` until the other branches are validated.

Required:
1. close is at least 5% below 60-day MA
2. 1-day return > 0%
3. 5-day return >= -5%
4. 10-day return >= -10%

If exactly one is missing, `one_condition_away=true`.

## Ranking
1. BUY_CANDIDATE
2. WATCH with one condition away
3. WATCH: UPTREND_PULLBACK
4. WATCH: UPTREND
5. WATCH: RANGE_TRANSITION
6. AVOID: DOWNTREND_CONTINUED
7. INSUFFICIENT

Relative strength and supply/demand are ranking/context factors, not hard gates.


## 2026-09-14 BUY判定の厳格化
- DOWNTREND_REVERSAL_WAIT は BUY_CANDIDATE にしない。常に WATCH。
- BUY_CANDIDATE は DOWNTREND_REVERSAL_CONFIRMED のみ。
- 反転確認には週足下降、日足20MA回復、大底反転ローソク足、反転4条件を要求。
- これにより「下降・反転待ちなのにBUY候補」という表示矛盾を防止。


## レンジ抜け・再上昇
大底反転とは別系統で、直近20営業日の高値を終値で突破し、突破日の出来高増加、20日MA上、突破水準維持、短期上昇、過熱度を確認した場合に「レンジ抜け・再上昇」としてBUY候補を判定する。


### 上昇トレンドの押し目BUY（厳しめ）
- 週足方向がUP
- 現在値の20日MA乖離が -3%〜+0.5%
- 5日騰落率が -1%以下（実際に押している）
- **当日の騰落率はBUY判定に使用しない**（場中・当日の値動きに依存しない）
- 20日MAが5日比較で上向き
- 日足RSIが65未満（過熱を避ける）
上記をすべて満たした場合のみ上昇押し目をBUY候補とする。20日MAから大きく上に離れた高値圏では、上昇トレンドでも追いかけ買いせずWATCHとする。

### 2026-09-20 上昇押し目の基準を25日MA中心へ調整
- 20日MAだけを押し目BUYの基準にしない。
- 第1の押し目目安を25日MAとする。
- 通常の上昇押し目BUYは、週足UP、25日MA乖離 -4%〜+0.5%、5日騰落率 -3%以下、25日MA上向き、RSI65未満を要求する。
- 75日MAは第2の「深い押し目」目安とする。
- 75日MA付近（±3%）まで下げた場合は、20日騰落率 -5%以下、75日MA上向き、RSI55未満を追加確認してBUY候補とする。
- 75日MA到達そのものを自動BUY条件にはしない。深い押しはトレンド崩れの可能性もあるため、75日MAの傾きと下落幅を同時に確認する。
- 当日の騰落率は引き続きBUY条件に使用しない。

### 2026-09-20 チャート拡張
詳細チャートに以下を追加。
- 出来高＋20日平均
- RSI(14)＋30/70ライン
- MACD(12,26,9)＋シグナル＋ヒストグラム
- 既存のローソク足、5/25/75/200MA、日足BB±2σは維持
