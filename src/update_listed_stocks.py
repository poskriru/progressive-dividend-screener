"""
JPX公式の東証上場銘柄一覧を取得し、
Googleスプレッドシートへ書き込むプログラム。

対象:
- 東証プライム市場の内国株式
- 東証スタンダード市場の内国株式
- 東証グロース市場の内国株式
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import io
import json
import os
import sys
import traceback
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


# ============================================================
# 外部ライブラリ
# ============================================================

import pandas as pd
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build


# ============================================================
# 定数
# ============================================================

JPX_LISTED_STOCKS_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/"
    "misc/tvdivq0000001vg2-att/data_j.xls"
)

SPREADSHEET_SHEET_NAME = "銘柄マスター"

GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

JST = ZoneInfo("Asia/Tokyo")

REQUEST_TIMEOUT_SECONDS = 60

USER_AGENT = (
    "progressive-dividend-screener/0.1 "
    "(personal investment data management)"
)


# ============================================================
# 環境変数
# ============================================================

def get_required_environment_variable(name: str) -> str:
    """
    必須環境変数を取得する。

    空文字または未登録の場合は例外を発生させる。
    """

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"必須環境変数が設定されていません: {name}"
        )

    return value


# ============================================================
# Discord通知
# ============================================================

def send_discord_notification(
    title: str,
    description: str,
    *,
    success: bool,
) -> None:
    """
    Discord WebhookへEmbed形式で通知する。
    """

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    if not webhook_url:
        print(
            "DISCORD_WEBHOOK_URLが設定されていないため、"
            "Discord通知を省略します。",
            file=sys.stderr,
        )
        return

    color = 0x2ECC71 if success else 0xE74C3C

    payload = {
        "username": "累進配当スクリーナー",
        "embeds": [
            {
                "title": title[:256],
                "description": description[:4000],
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {
                    "text": "progressive-dividend-screener",
                },
            }
        ],
    }

    response = requests.post(
        webhook_url,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()


# ============================================================
# JPXデータ取得
# ============================================================

def download_jpx_listed_stocks() -> bytes:
    """
    JPX公式の東証上場銘柄一覧Excelを取得する。
    """

    print(f"JPX上場銘柄一覧を取得します: {JPX_LISTED_STOCKS_URL}")

    response = requests.get(
        JPX_LISTED_STOCKS_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/vnd.ms-excel,"
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet,*/*"
            ),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "JPXから取得したExcelファイルが空です。"
        )

    print(
        "JPX上場銘柄一覧を取得しました。"
        f"サイズ: {len(response.content):,} bytes"
    )

    return response.content


# ============================================================
# データ変換
# ============================================================

def normalize_column_name(value: Any) -> str:
    """
    Excelの列名を比較しやすい形式へ変換する。
    """

    return (
        str(value)
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("　", "")
        .strip()
    )


def find_column(
    dataframe: pd.DataFrame,
    candidates: list[str],
    *,
    required: bool = True,
) -> str | None:
    """
    候補名からDataFrame内の列を検索する。
    """

    normalized_columns = {
        normalize_column_name(column): column
        for column in dataframe.columns
    }

    for candidate in candidates:
        normalized_candidate = normalize_column_name(candidate)

        if normalized_candidate in normalized_columns:
            return normalized_columns[normalized_candidate]

    if required:
        raise RuntimeError(
            "必要な列が見つかりません。"
            f"候補={candidates}, "
            f"実際の列={list(dataframe.columns)}"
        )

    return None


def normalize_security_code(value: Any) -> str:
    """
    証券コードを文字列へ統一する。

    数字コードだけでなく、英字を含むコードにも対応する。
    """

    if pd.isna(value):
        return ""

    text = str(value).strip().upper()

    if text.endswith(".0"):
        text = text[:-2]

    return text


def convert_to_cell_value(value: Any) -> str | int | float:
    """
    Googleスプレッドシートへ書き込める形式へ変換する。
    """

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, datetime):
        return value.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value.is_integer():
            return int(value)

        return value

    return str(value).strip()


def normalize_reference_date(value: Any) -> str:
    """
    JPX一覧の基準日をYYYY-MM-DD形式へ変換する。
    """

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()

    if not text:
        return ""

    parsed = pd.to_datetime(text, errors="coerce")

    if not pd.isna(parsed):
        return parsed.strftime("%Y-%m-%d")

    return text


def extract_market_name(market_category: str) -> str:
    """
    JPXの市場・商品区分から市場名を抽出する。
    """

    if "プライム" in market_category:
        return "プライム"

    if "スタンダード" in market_category:
        return "スタンダード"

    if "グロース" in market_category:
        return "グロース"

    return market_category


def load_and_filter_jpx_data(
    excel_content: bytes,
) -> tuple[list[str], list[list[Any]], str]:
    """
    JPXのExcelを読み込み、内国普通株式を抽出する。

    戻り値:
    - ヘッダー
    - データ行
    - JPX一覧基準日
    """

    dataframe = pd.read_excel(
        io.BytesIO(excel_content),
        dtype=object,
        engine="xlrd",
    )

    if dataframe.empty:
        raise RuntimeError(
            "JPX上場銘柄一覧のExcelにデータがありません。"
        )

    print(
        "JPX Excelを読み込みました。"
        f"読み込み行数: {len(dataframe):,}"
    )

    date_column = find_column(
        dataframe,
        ["日付", "基準日"],
        required=False,
    )

    code_column = find_column(
        dataframe,
        ["コード", "証券コード"],
    )

    company_name_column = find_column(
        dataframe,
        ["銘柄名", "会社名"],
    )

    market_category_column = find_column(
        dataframe,
        ["市場・商品区分", "市場商品区分", "市場区分"],
    )

    industry_33_code_column = find_column(
        dataframe,
        ["33業種コード"],
        required=False,
    )

    industry_33_name_column = find_column(
        dataframe,
        ["33業種区分", "33業種名"],
        required=False,
    )

    industry_17_code_column = find_column(
        dataframe,
        ["17業種コード"],
        required=False,
    )

    industry_17_name_column = find_column(
        dataframe,
        ["17業種区分", "17業種名"],
        required=False,
    )

    scale_code_column = find_column(
        dataframe,
        ["規模コード"],
        required=False,
    )

    scale_name_column = find_column(
        dataframe,
        ["規模区分"],
        required=False,
    )

    market_text = (
        dataframe[market_category_column]
        .fillna("")
        .astype(str)
    )

    is_domestic_stock = market_text.str.contains(
        "内国株式",
        regex=False,
    )

    is_target_market = market_text.str.contains(
        "プライム|スタンダード|グロース",
        regex=True,
    )

    filtered = dataframe[
        is_domestic_stock & is_target_market
    ].copy()

    if filtered.empty:
        market_values = sorted(
            dataframe[market_category_column]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        raise RuntimeError(
            "対象となる内国株式が見つかりません。"
            f"市場・商品区分={market_values}"
        )

    filtered["_証券コード"] = filtered[code_column].apply(
        normalize_security_code
    )

    filtered = filtered[
        filtered["_証券コード"] != ""
    ].copy()

    filtered["_市場"] = filtered[market_category_column].apply(
        lambda value: extract_market_name(str(value))
    )

    market_order = {
        "プライム": 1,
        "スタンダード": 2,
        "グロース": 3,
    }

    filtered["_市場順"] = (
        filtered["_市場"]
        .map(market_order)
        .fillna(99)
    )

    filtered = filtered.sort_values(
        by=["_市場順", "_証券コード"],
        kind="stable",
    )

    reference_date = ""

    if date_column is not None:
        non_empty_dates = (
            filtered[date_column]
            .dropna()
            .tolist()
        )

        if non_empty_dates:
            reference_date = normalize_reference_date(
                non_empty_dates[0]
            )

    update_time = datetime.now(JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    headers = [
        "更新日時",
        "一覧基準日",
        "証券コード",
        "銘柄名",
        "市場",
        "市場・商品区分",
        "33業種コード",
        "33業種区分",
        "17業種コード",
        "17業種区分",
        "規模コード",
        "規模区分",
        "データ出典",
        "出典URL",
    ]

    rows: list[list[Any]] = []

    for _, record in filtered.iterrows():
        row = [
            update_time,
            reference_date,
            record["_証券コード"],
            convert_to_cell_value(
                record[company_name_column]
            ),
            record["_市場"],
            convert_to_cell_value(
                record[market_category_column]
            ),
            convert_to_cell_value(
                record[industry_33_code_column]
                if industry_33_code_column
                else ""
            ),
            convert_to_cell_value(
                record[industry_33_name_column]
                if industry_33_name_column
                else ""
            ),
            convert_to_cell_value(
                record[industry_17_code_column]
                if industry_17_code_column
                else ""
            ),
            convert_to_cell_value(
                record[industry_17_name_column]
                if industry_17_name_column
                else ""
            ),
            convert_to_cell_value(
                record[scale_code_column]
                if scale_code_column
                else ""
            ),
            convert_to_cell_value(
                record[scale_name_column]
                if scale_name_column
                else ""
            ),
            "JPX",
            JPX_LISTED_STOCKS_URL,
        ]

        rows.append(row)

    print(
        "対象銘柄を抽出しました。"
        f"抽出件数: {len(rows):,}"
    )

    return headers, rows, reference_date


# ============================================================
# Google認証
# ============================================================

def create_google_sheets_service():
    """
    GitHub SecretsのサービスアカウントJSONから
    Google Sheets APIクライアントを作成する。
    """

    credentials_json = get_required_environment_variable(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    try:
        credentials_info = json.loads(credentials_json)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSONをJSONとして"
            "読み込めませんでした。"
            "JSONファイルの最初の{から最後の}までを"
            "登録してください。"
        ) from error

    required_fields = [
        "project_id",
        "private_key",
        "client_email",
        "token_uri",
    ]

    missing_fields = [
        field
        for field in required_fields
        if not credentials_info.get(field)
    ]

    if missing_fields:
        raise RuntimeError(
            "サービスアカウントJSONに必要な項目がありません。"
            f"不足項目: {missing_fields}"
        )

    credentials = (
        service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=GOOGLE_SHEETS_SCOPES,
        )
    )

    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )


# ============================================================
# Googleスプレッドシート操作
# ============================================================

def get_or_create_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
) -> int:
    """
    対象シートを取得する。
    存在しない場合は新規作成する。
    """

    spreadsheet = (
        sheets_service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties",
        )
        .execute()
    )

    for sheet in spreadsheet.get("sheets", []):
        properties = sheet.get("properties", {})

        if properties.get("title") == sheet_name:
            return int(properties["sheetId"])

    print(
        f"シート「{sheet_name}」が存在しないため作成します。"
    )

    response = (
        sheets_service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": sheet_name,
                                "gridProperties": {
                                    "frozenRowCount": 1,
                                },
                            }
                        }
                    }
                ]
            },
        )
        .execute()
    )

    return int(
        response["replies"][0]["addSheet"]["properties"]["sheetId"]
    )


def column_number_to_letter(column_number: int) -> str:
    """
    1始まりの列番号をA1記法の列文字へ変換する。
    """

    letters = ""
    number = column_number

    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters

    return letters


def write_to_google_sheets(
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    """
    Googleスプレッドシートへ一括書き込みする。
    """

    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )

    sheets_service = create_google_sheets_service()

    sheet_id = get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        SPREADSHEET_SHEET_NAME,
    )

    print(
        f"シート「{SPREADSHEET_SHEET_NAME}」を初期化します。"
    )

    (
        sheets_service.spreadsheets()
        .values()
        .clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{SPREADSHEET_SHEET_NAME}'",
            body={},
        )
        .execute()
    )

    values = [headers, *rows]

    last_column = column_number_to_letter(len(headers))
    last_row = len(values)

    target_range = (
        f"'{SPREADSHEET_SHEET_NAME}'!"
        f"A1:{last_column}{last_row}"
    )

    print(
        "Googleスプレッドシートへ書き込みます。"
        f"範囲: {target_range}"
    )

    (
        sheets_service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=target_range,
            valueInputOption="RAW",
            body={
                "majorDimension": "ROWS",
                "values": values,
            },
        )
        .execute()
    )

    formatting_requests = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "frozenRowCount": 1,
                    },
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(headers),
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0.18,
                            "green": 0.38,
                            "blue": 0.60,
                        },
                        "textFormat": {
                            "foregroundColor": {
                                "red": 1.0,
                                "green": 1.0,
                                "blue": 1.0,
                            },
                            "bold": True,
                        },
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.textFormat,"
                    "userEnteredFormat.horizontalAlignment"
                ),
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": last_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(headers),
                    }
                }
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(headers),
                }
            }
        },
    ]

    (
        sheets_service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": formatting_requests,
            },
        )
        .execute()
    )

    print(
        "Googleスプレッドシートの更新が完了しました。"
        f"データ件数: {len(rows):,}"
    )


# ============================================================
# 集計
# ============================================================

def count_markets(
    headers: list[str],
    rows: list[list[Any]],
) -> dict[str, int]:
    """
    市場別の銘柄数を集計する。
    """

    market_index = headers.index("市場")

    result = {
        "プライム": 0,
        "スタンダード": 0,
        "グロース": 0,
    }

    for row in rows:
        market = str(row[market_index])

        if market in result:
            result[market] += 1

    return result


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    JPX一覧取得からスプレッドシート更新までを実行する。
    """

    excel_content = download_jpx_listed_stocks()

    headers, rows, reference_date = load_and_filter_jpx_data(
        excel_content
    )

    write_to_google_sheets(headers, rows)

    market_counts = count_markets(headers, rows)

    description = "\n".join(
        [
            "JPXの東証上場銘柄一覧を更新しました。",
            "",
            f"**一覧基準日:** {reference_date or '取得できませんでした'}",
            f"**合計:** {len(rows):,}銘柄",
            f"**プライム:** {market_counts['プライム']:,}銘柄",
            f"**スタンダード:** {market_counts['スタンダード']:,}銘柄",
            f"**グロース:** {market_counts['グロース']:,}銘柄",
            "",
            f"**更新先:** `{SPREADSHEET_SHEET_NAME}`",
            "**データ出典:** JPX",
        ]
    )

    send_discord_notification(
        "✅ 銘柄マスター更新完了",
        description,
        success=True,
    )

    print(description)


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        error_message = (
            f"{type(error).__name__}: {error}"
        )

        print(
            "処理中にエラーが発生しました。",
            file=sys.stderr,
        )

        traceback.print_exc()

        try:
            send_discord_notification(
                "❌ 銘柄マスター更新失敗",
                "\n".join(
                    [
                        "銘柄マスターの更新中にエラーが発生しました。",
                        "",
                        "```text",
                        error_message[:3000],
                        "```",
                        "",
                        "GitHub Actionsのログを確認してください。",
                    ]
                ),
                success=False,
            )

        except Exception:
            print(
                "Discordへのエラー通知にも失敗しました。",
                file=sys.stderr,
            )
            traceback.print_exc()

        sys.exit(1)
