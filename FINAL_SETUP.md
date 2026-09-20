# 最終セットアップ

1. GitHub `concord2023/kabu-score` にこのZIPの中身をアップロード。
2. 既存の同名ファイルは上書き。古い不要ファイルは削除不要。
3. `.github/workflows/daily-update.yml` はその場所に置く。
4. Repository Secrets に `IRBANK_API_KEY` を設定。
5. Actions → Daily stock update → Run workflow を1回実行。
6. 成功後、GitHub Pagesを開く。

Pagesの入口は `index.html`。旧 `dashboard.html` を入口にしない。
