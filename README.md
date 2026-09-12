# 株スコア（GitHub Actions + IRBANK）

- APIキーは GitHub Actions Secret `IRBANK_API_KEY` のみで使用。
- ブラウザの JavaScript に API キーを入れない。
- 平日16:30頃のデータ更新を想定し、翌朝16:30? ではなく GitHub Actions のUTC 07:30（JST 16:30）に実行する設定。
- watchlist.json に銘柄コードを追加すると対象を増やせる。
- 現段階では株価・出来高を実データ化。騰落レシオはデータ源を統一した後に追加する。
