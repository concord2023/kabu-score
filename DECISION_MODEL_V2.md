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
