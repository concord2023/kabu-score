# 最終セットアップ

1. GitHub `concord2023/kabu-score` にこのZIPの中身をアップロード。
2. 既存の同名ファイルは上書き。古い不要ファイルは削除不要。
3. `.github/workflows/daily-update.yml` はその場所に置く。
4. Repository Secrets に `IRBANK_API_KEY` を設定。
5. Actions → Daily stock update → Run workflow を1回実行。
6. 成功後、GitHub Pagesを開く。

Pagesの入口は `index.html`。旧 `dashboard.html` を入口にしない。


## 追加銘柄とデータ保護（2026-09-19）
- アプリから追加した銘柄は `data/custom_watchlist.json` にも保存します。ソースZIPを差し替えても、Daily stock update 実行時に追加銘柄を `watchlist.json` へ自動統合します。
- 現在の追加銘柄としてダイキン工業（6367）を復元しています。
- Daily stock update が失敗した場合、`data/stocks.json` / `data/decision_ranking.json` の古い正常データを空データで上書きしないよう、保存処理を停止します。
- 上場銘柄BUYスキャンが失敗しても、通常の日次分析データは保存します。
- ZIP差し替え直後に日次データが初期状態なら、GitHub Actions の `Daily stock update` を1回実行してください。成功後に「判定不能」ではなく実データが表示されます。
