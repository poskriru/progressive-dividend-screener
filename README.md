# 累進配当スクリーナー

東証上場銘柄の株価・財務・配当・株主還元方針を取得し、
累進配当銘柄を検索するための個人用スクリーナーです。

## 現在の機能

- JPX公式の東証上場銘柄一覧を取得
- プライム・スタンダード・グロースの内国株式を抽出
- JPX東京証券取引所日報から終値を取得
- EDINET APIから有価証券報告書・財務情報・配当実績を取得
- PER、PBR、ROE、配当利回り、配当性向などを計算
- 直近5期の非減配実績から累進配当候補を判定
- PostgreSQLへ保存し、Googleスプレッドシートへ一括出力
- 条件に合う銘柄を「累進配当候補」シートへランキング出力
- Discord Webhookへ実行結果と累進配当候補の上位10銘柄を通知
- GitHub Actionsによる手動・定期実行

## 累進配当候補の抽出条件

EDINET財務情報の更新後、本番「株式指標」と同じタイミングで
「累進配当候補」シートを更新します。初期条件は以下のとおりです。

| 条件 | 初期値 | 環境変数 |
|---|---:|---|
| 5期累進配当判定 | `TRUE` | 変更不可 |
| 配当利回り | 3.0%以上 | `CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT` |
| 配当性向 | 0%以上70.0%以下 | `CANDIDATE_MAX_PAYOUT_RATIO_PERCENT` |
| PER | 0倍超25.0倍以下 | `CANDIDATE_MAX_PER_RATIO` |
| PBR | 0倍超3.0倍以下 | `CANDIDATE_MAX_PBR_RATIO` |
| ROE | 8.0%以上 | `CANDIDATE_MIN_ROE_PERCENT` |
| フリーCF | プラス必須 | `CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW` |
| 最大出力件数 | 300件 | `CANDIDATE_MAX_ROWS` |

候補は配当利回り、5期配当CAGR、ROEの降順で並びます。
環境変数が未設定の場合は上記の初期値を使用します。定期実行の条件を
変更する場合は、該当するGitHub Actionsの実行環境へ設定してください。
年間配当履歴は株式分割・株式併合による過年度調整前の値なので、
候補シートの判定注記と一次資料を確認してください。

## 今後追加する機能

- 株式分割・株式併合を考慮した過年度配当の補正
- TDnetの新着開示を監視
- 会社公表資料から累進配当方針を判定
- Discordから条件指定して銘柄検索

## GitHub Secrets

以下のRepository Secretsが必要です。

| Secret名 | 内容 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GoogleサービスアカウントJSON全文 |
| `GOOGLE_SPREADSHEET_ID` | GoogleスプレッドシートID |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL |
| `EDINET_API_KEY` | EDINET APIキー |
| `JQUANTS_API_KEY` | J-Quants APIキー |

現時点の銘柄マスター更新処理で使用するのは、
以下の3つです。

- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SPREADSHEET_ID`
- `DISCORD_WEBHOOK_URL`

EDINETとJ-QuantsのAPIキーは、今後の処理で使用します。

## データ出典

- 日本取引所グループ
- 金融庁EDINET
- 各上場会社の公式IR情報

## 注意事項

本ツールは個人の情報収集を目的としています。
投資判断は利用者自身の責任で行ってください。

取得したデータの完全性・正確性・最新性を保証するものでは
ありません。
