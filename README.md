# 累進配当スクリーナー

東証上場銘柄の株価・財務・配当・株主還元方針を取得し、
累進配当銘柄を検索するための個人用スクリーナーです。

## 現在の機能

- JPX公式の東証上場銘柄一覧を取得
- プライム・スタンダード・グロースの内国株式を抽出
- Googleスプレッドシートへ一括出力
- Discord Webhookへ実行結果を通知
- GitHub Actionsによる手動・定期実行

## 今後追加する機能

- JPX東京証券取引所日報から終値を取得
- EDINET APIから有価証券報告書を取得
- 財務情報と配当実績を抽出
- PER、PBR、配当利回り、配当性向を計算
- TDnetの新着開示を監視
- 累進配当方針を判定
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
