# 株スコア (kabu-score)

ウォッチリスト銘柄について、IRBANK API の日次株価から RSI・移動平均・MACD・出来高・需給などを計算し、週足＝方向、日足＝タイミングを基本に分類・スコア表示する静的 Web アプリ（PWA）です。GitHub Pages 上で動作し、画面は `index.html`（一覧＋今日の注目5選）、`detail.html`（1銘柄の詳細分析）、`signals.html`、`recommendations.html`、`bottom.html` などから構成されます。

## データの流れ

1. `.github/workflows/daily-update.yml` が平日 17:10（日本時間）に単一スケジュールで `update_data.py` を実行する（予備実行・自動リトライはない）。
2. `update_data.py` は IRBANK API（上限管理あり: プロセス上限 200 リクエスト/日、`check_api_usage` で事前確認）から株価・需給・PER 等を取得し、指標計算（`indicators.py` / `calc.py`）と市場レジーム判定（`regime_model_v2.py`）を経て `data/*.json` を生成する。取得失敗した銘柄は前回の分析結果を `stale` として残し、失敗時に生成データをコミットしないことで「全銘柄が判定不能になる」事故を防ぐ。
3. `rank_decisions.py` が判定結果をランキング形式に整形し、GitHub Pages が `data/*.json` を配信して画面が表示する。
4. 手動ワークフロー: `recommendation-scan.yml`（全上場銘柄の BUY 候補スキャン、`scan_recommendations.py`）と `bottom-prime-scan.yml`（大底候補スキャン、`scan_bottom_prime.py`）は必要なときだけ手動実行する。通常の日次更新では全銘柄スキャンは実行しない。

## コード構成

| ファイル | 役割 |
|---|---|
| `update_data.py` | 日次更新の入口。公開 API を再エクスポートし、銘柄追加・削除（環境変数経由）とウォッチリスト読み込みを担う |
| `market_data.py` | IRBANK API 取得層（`get` / `check_api_usage` / 各 `fetch_*`）。レート制限カウンタはここに一元管理 |
| `indicators.py` | 純粋な指標計算（RSI 2方式・EMA・MACD・週足/月足集計・ローソク足/ブレイクアウト/BB・チャート履歴） |
| `calc.py` | 1銘柄分の指標を集約する `calc()` |
| `forecast_parsers.py` | Yahoo!ファイナンス予想 PER/EPS・PBR/配当のパーサと決算期日変換 |
| `regime_model_v2.py` | 市場レジーム分類 `classify_regime` と売買判定 `decide` |
| `validate_release.py` | オフラインのリリース検証（ネットワーク不要） |

RSI は用途の異なる2方式を意図的に併存させています: `rsi14`（単純平均版、スコア計算の継続性のため）と `rsi14_wilder`（一般的なチャートと一致させる厳密ゲート用）。統一しないでください。

## 銘柄の追加・削除

- アプリ一覧から GitHub Issue 経由で追加・削除を依頼でき、日次ワークフローが処理する（リポジトリ所有者本人の Issue のみ受理、証券コードを検証）。
- 追加銘柄は `data/custom_watchlist.json`、削除は `data/watchlist_exclusions.json` に保持されるため、ソース差し替えで意図せず消えたり復活したりしない。実効ウォッチリストは `watchlist.json` に書き戻される。

## 実行方法

```bash
# 日次更新（IRBANK_API_KEY が必要）
IRBANK_API_KEY=... python update_data.py

# オフライン検証と回帰テスト（API キーはダミーで可、ネットワーク不要）
IRBANK_API_KEY=dummy python validate_release.py
IRBANK_API_KEY=dummy python test_tora_rsi.py
IRBANK_API_KEY=dummy python test_regime_classification_regression.py
IRBANK_API_KEY=dummy python test_watchlist_persistence.py
IRBANK_API_KEY=dummy python test_monthly_chart_macd.py
IRBANK_API_KEY=dummy python test_parse_yahoo_forecast.py
```

push / pull request 時は `.github/workflows/tests.yml` が上記の検証とテストを自動実行します。

## 依存関係

本体は Python 標準ライブラリのみで動作します。例外は `update_company_master.py` の `openpyxl` で、`requirements.txt` に固定しています（ワークフロー側は現状それぞれ `pip install` しており、固定化は順次対応）。

## ドキュメント

- 現行の入口はこの README です。
- 過去の設計・モデル文書は履歴として残しています: `README_V2.md`、`README_V3.md`、`DAILY_RANKING_V1.md`、`DECISION_MODEL_V2.md`、`ENTRY_BRANCH_V1.md`、`MARKET_CONTEXT_V1.md`、`REGIME_MODEL_V1.md`、`SIGNAL_MODEL_V1.md`、`VALIDATION_V1.md`、`WEEKLY_DAILY_MODEL_V2.md`、`FINAL_SETUP.md`。内容が現行仕様と異なる場合があります（特に銘柄数やスケジュールはこの README とワークフローが正）。
- セットアップの経緯は `FINAL_SETUP.md` を参照してください。

IRBANK API の利用条件により、株価データの出典表示と利用条件を守ってください。
