# 株スコア V2

18銘柄の日本株を、**週足＝方向 / 日足＝タイミング**で判定するGitHub Pages向けPWAです。

## 今回の修正版
- iPhone/GitHub PagesからIRBANK APIへ直接アクセスしない構成に変更
- GitHub Actionsが1日1回データ取得・判定し、`data/decision_ranking.json` を更新
- IRBANKの現在の60リクエスト/分制限を考慮し、1銘柄あたり株価1回＋需給2回に抑制
- 429/5xxは指数バックオフで再試行
- 市場騰落は1日1回だけ取得し、失敗しても株判定を止めない
- `index.html` を追加し、古い0〜100スコア画面ではなくV2画面をGitHub Pagesの入口にする
- 18銘柄ランキング、BUY候補、WATCH、AVOID、あと1条件を表示
- 上昇トレンド/押し目のBUY化はまだ検証前なのでWATCHのまま
- 下降トレンドの反転4条件だけ暫定BUY候補

## 毎日の流れ
IRBANK → GitHub Actions → `data/stocks.json` / `data/decision_ranking.json` → GitHub Pages

ユーザー端末からIRBANKへアクセスしないため、画面を開くたびにAPIを叩いて429になる問題を避けます。

## 必要な設定
GitHubリポジトリのSecretsに `IRBANK_API_KEY` を登録してください。GitHub ActionsのSecretはログに直接露出しないようGitHub側で管理します。

## 公開URL
`https://concord2023.github.io/kabu-score/`
