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
- J-Quants V2の`AdjFactor`で過去配当を現在株式ベースへ補正
- raw判定とadjusted判定を併存し、補正範囲充足時だけadjustedを採用
- PostgreSQLへ保存し、Googleスプレッドシートへ一括出力
- 条件に合う銘柄を「累進配当候補」シートへランキング出力
- 発行済株式数の大幅変動を「株式分割等確認対象」へ出力
- TDnet公開一覧から配当・株主還元関連の新着開示を監視
- 表題キーワードから「累進配当方針候補」を作成
- Discord Webhookへ候補銘柄とTDnet新着開示を通知
- GitHub Actionsによる手動・定期実行

## 累進配当候補の抽出条件

EDINET財務情報の更新後、本番「株式指標」と同じタイミングで
「累進配当候補」シートを更新します。初期条件は以下のとおりです。

| 条件 | 初期値 | 「累進配当条件」の設定項目 |
|---|---:|---|
| 5期累進配当判定 | `TRUE` | 変更不可 |
| 配当利回り | 3.0%以上 | `CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT` |
| 配当性向 | 0%以上70.0%以下 | `CANDIDATE_MAX_PAYOUT_RATIO_PERCENT` |
| PER | 0倍超25.0倍以下 | `CANDIDATE_MAX_PER_RATIO` |
| PBR | 0倍超3.0倍以下 | `CANDIDATE_MAX_PBR_RATIO` |
| ROE | 8.0%以上 | `CANDIDATE_MIN_ROE_PERCENT` |
| フリーCF | プラス必須 | `CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW` |
| 最大出力件数 | 300件 | `CANDIDATE_MAX_ROWS` |

初回実行時に「累進配当条件」シートを自動作成します。
2列目の「設定値」を編集すると、次回更新から条件へ反映されます。
設定項目名・列見出しは変更せず、値だけを編集してください。

候補は配当利回り、5期配当CAGR、ROEの降順で並びます。
更新前の候補シートと証券コードを比較し、新規追加・除外された銘柄を
Discord通知へ表示します。除外銘柄には、累進配当判定、配当利回り、
配当性向、PER、PBR、ROE、フリーCFなど現在の指標から判定した理由も
表示します。候補に変更がない場合もその旨を通知します。
追加・除外イベントは「累進配当候補_変動履歴」シートへ追記し、
検出日時、銘柄、理由、適用した抽出条件を後から確認できます。
「累進配当条件」シートが存在しない場合は、環境変数または上記の
初期値を使用してシートを作成します。シート作成後は、シート上の値を
正本として使用します。不明な項目、重複、空欄、不正な数値がある場合は
候補更新を停止し、誤った条件での出力を防ぎます。
候補抽出では、J-Quants補正範囲が完全な銘柄だけadjusted判定を採用します。
未取得・取得範囲不足・未対応アクションがある銘柄はadjusted判定を採用せず、
従来のraw判定と注意書きを維持します。候補シートでは採用判定種別、raw判定、
adjusted判定、補正係数、補正状態、両方の配当履歴を確認できます。

## 株式分割・株式併合による過去配当補正

J-Quants API V2の`/v2/equities/bars/daily`から、日付単位で`AdjFactor`と
`ExRT`を取得します。認証には`JQUANTS_API_KEY`を`x-api-key`ヘッダーで
使用します。取得結果は以下へ保存します。

- `screener.corporate_actions`: 調整係数が1以外、または`ExRT`がある日
- `screener.jquants_adjustment_sync_status`: 銘柄別の要求・取得保証範囲
- `screener.company_dividend_metrics_adjusted`: 調整済み直近5期指標

補正では、対象決算期より後かつ基準日以前の係数をすべて掛け合わせます。
1:2分割の`0.5`は旧配当へ掛け、複数回の分割・併合は係数の積を使います。
権利落ち日当日の係数はその日より前の履歴へ効かせるため、SQLの境界は
`effective_date > fiscal_period_end`です。

自動補正対象は`ExRT=1`（分割・無償割当）と`ExRT=2`（併合）だけです。
`ExRT=3`（ライツイシュー）や種別不明の非1係数は保存しますが、機械的な
配当補正をせず`unsupported_corporate_action`とします。取得範囲が直近5期の
最古決算日以前から当日まで達していない場合は`adjustment_data_incomplete`とし、
adjusted判定はNULLになります。raw値・raw判定は変更も上書きもしません。

初回バックフィルと日次更新は、DBマイグレーション適用後に実行します。

```bash
python src/run_database_migrations.py
python src/update_jquants_corporate_actions.py
python src/export_database_indicators.py
```

初回取得開始日は全銘柄の直近5期で必要な最古日を自動算出します。取得可能な
期間がプランで不足する場合はcompleteにならず、adjusted判定を採用しません。
検証や再取得では`JQUANTS_ADJUSTMENT_FROM`と`JQUANTS_ADJUSTMENT_TO`で期間を
明示できます。`JQUANTS_REQUESTS_PER_MINUTE`は契約プランの上限以下に設定し、
初期値はFreeプラン相当の5です。初回バックフィル後は取得済み終端の翌日から
増分取得します。

現在のGitHub Actionsワークフローには`JQUANTS_API_KEY`を渡すステップがないため、
PR反映後にRepository Secretをジョブ環境へ渡し、上記更新スクリプトを株式指標の
出力前に実行する設定が必要です。workflow更新権限のないGitHub Appからは変更せず、
認証情報をコードやログへ埋め込まないでください。

## 株式分割等の確認対象

直近6年のEDINET年次財務を比較し、発行済株式数が前期比1.5倍以上、
または0.67倍以下になった期間を「株式分割等確認対象」シートへ出力します。
新旧の発行済株式数、年間配当、変動倍率、EDINET閲覧URLを確認できます。

発行済株式数の変動だけでは株式分割・併合を確定できません。増資、
自己株式消却、組織再編等でも変動するため、このシートは自動補正ではなく
一次資料で確認する対象を絞り込むための診断用です。

## TDnet配当関連開示

TDnet適時開示情報閲覧サービスの公開一覧を低頻度で確認し、現在の
銘柄マスターに含まれる会社の配当・株主還元関連表題を抽出します。
既定では直近7日、最大31日までを対象とし、以下へ出力します。

- `TDnet配当開示`: 開示ID、日時、表題、分類、PDF URLの追記履歴
- `累進配当方針候補`: 累進配当、DOE、配当方針、株主還元方針などが
  表題に含まれる銘柄ごとの最新開示

新着開示だけをDiscordへ通知し、開示IDで重複を防止します。
「累進配当候補」にもTDnet方針候補と最新の配当関連開示について、
開示日、分類、表題、PDF URLを追加します。最新開示が減配・無配の場合は
警戒フラグを付け、Discordの候補ランキングにも表示します。
PDF本文は自動ダウンロードせず、公開一覧のメタデータとリンクのみを
取得します。方針判定は表題キーワードによる候補抽出であり、確定情報では
ありません。PDF本文と会社IRの一次資料を確認してください。

取得日数は環境変数`TDNET_LOOKBACK_DAYS`で1〜31日の範囲に変更できます。
公開TDnet一覧の一時障害が発生しても、財務・株式指標・候補シートの更新は
失敗扱いにせず、TDnet処理だけをDiscordへエラー通知します。

## 今後追加する機能

- TDnet・会社IRのPDF本文による累進配当方針の確定
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

EDINET APIキーは財務更新、J-Quants APIキーは株式分割・併合による
過去配当補正で使用します。

## データ出典

- 日本取引所グループ
- 金融庁EDINET
- J-Quants API V2
- 各上場会社の公式IR情報

## 注意事項

本ツールは個人の情報収集を目的としています。
投資判断は利用者自身の責任で行ってください。

取得したデータの完全性・正確性・最新性を保証するものでは
ありません。
