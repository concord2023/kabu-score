# 株判定 V2

## 判定の基本
- 週足 = 方向
- 日足 = タイミング
- 市場環境 = 文脈・相対強度（買い禁止フィルターにはしない）

## 状態
- UPTREND
- UPTREND_PULLBACK
- DOWNTREND_REVERSAL_WAIT
- DOWNTREND_CONTINUED
- RANGE_TRANSITION
- UNKNOWN

## シグナル
- BUY_CANDIDATE: 下降トレンド反転の暫定4条件が全成立
- WATCH: 条件検証中/監視
- AVOID: 下降継続
- INSUFFICIENT: データ不足

## 毎日の実行
ローカル環境で `IRBANK_API_KEY` を設定した上で:

```bash
python run_daily.py
```

APIキーはチャットへ貼らないでください。

IRBANK APIの株価時系列は `/securities/{code}/prices` を使用し、時系列比較には調整後終値を利用します。APIはBearer token認証です。
