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


## Important: generated data files are preserved
The source package intentionally does not overwrite these generated/user-state files when updating an existing repository:
- data/stocks.json
- data/decision_ranking.json
- data/recommendations.json
- data/custom_watchlist.json
- data/company_master.json

They are generated or maintained by GitHub Actions. This prevents installing an updated source package from resetting existing analysis results or user-added stocks.

The Daily stock update workflow runs only the watchlist analysis. The all-listed-stock BUY scan is a separate manual workflow, so a long or failed recommendation scan cannot hide or delay the normal daily results.


## 全銘柄BUY候補スキャンの負荷対策

全上場銘柄を一度に800営業日以上取得する方式はAPIリクエスト数が多いため、手動の `recommendation-scan.yml` は2段階方式とする。
1. 各銘柄から直近約120営業日だけを1回取得し、週足RSI(14)<=30（Wilder方式）を一次選別。
2. 一次選別を通過した銘柄だけ800営業日以上を取得し、月足MACDを含む通常の `decide()` で詳細判定。
通常の `daily-update.yml` は変更しない。


## 銘柄の追加・削除
- 一覧の「銘柄を追加」からGitHubの追加依頼を作成できます。
- 一覧の「銘柄を削除」から、現在の分析対象から外す依頼を作成できます。
- 削除した銘柄は `data/watchlist_exclusions.json` に保持するため、ソースZIPを差し替えても意図せず復活しません。
- 削除した銘柄を再追加すると除外状態を解除して分析対象へ戻します。
- 端末の「表示する銘柄を選択」は表示だけを切り替える機能で、分析対象そのものは増減させません。
- 月足チャートは直近18か月を表示します。月足MACDの大底候補判定に必要な長期履歴は別途確保するため、判定ロジックは変更しません。
