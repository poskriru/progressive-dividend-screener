"""
EDINET APIから提出書類一覧を取得し、
対象となる法定開示書類をGoogleスプレッドシートへ保存する。

対象書類:
- 有価証券報告書
- 訂正有価証券報告書
- 半期報告書
- 訂正半期報告書

書類管理番号（docID）を一意キーとして重複を防止する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import io
import json
import math
import os
import sys
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


# ============================================================
# 外部ライブラリ
# ============================================================

import pandas as pd
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# 定数
# ============================================================

EDINET_DOCUMENTS_API_URL = (
    "https://api.edinet-fsa.go.jp/api/v2/documents.json"
)

EDINET_CODE_LIST_URL = (
    "https://disclosure2dl.edinet-fsa.go.jp/"
    "searchdocument/codelist/Edinetcode.zip"
)

EDINET_VIEWER_URL = (
    "https://disclosure2.edinet-fsa.go.jp/"
)

MASTER_SHEET_NAME = "銘柄マスター"
EDINET_SHEET_NAME = "EDINET書類"

GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

JST = ZoneInfo("Asia/Tokyo")

REQUEST_TIMEOUT_SECONDS = 120

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; progressive-dividend-screener/0.3; "
    "+https://github.com/)"
)

DEFAULT_LOOKBACK_DAYS = 7
MAX_LOOKBACK_DAYS = 366

API_REQUEST_INTERVAL_SECONDS = 0.5

TARGET_ORDINANCE_CODE = "010"

TARGET_DOCUMENT_TYPES = {
    "120": "有価証券報告書",
    "130": "訂正有価証券報告書",
    "160": "半期報告書",
    "170": "訂正半期報告書",
}

OUTPUT_HEADERS = [
    "取得日時",
    "提出日時",
    "提出日",
    "証券コード",
    "銘柄名",
    "市場",
    "提出者名",
    "EDINETコード",
    "書類管理番号",
    "書類種別",
    "書類種別コード",
    "書類概要",
    "対象期間開始",
    "対象期間終了",
    "府令コード",
    "様式コード",
    "親書類管理番号",
    "取下げ状態",
    "書類情報修正状態",
    "開示状態",
    "XBRL",
    "PDF",
    "CSV",
    "法定状態",
    "データ出典",
    "EDINET閲覧サイト",
]


# ============================================================
# 環境変数
# ============================================================

def get_required_environment_variable(name: str) -> str:
    """
    必須環境変数を取得する。
    """

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"必須環境変数が設定されていません: {name}"
        )

    return value


def get_lookback_days() -> int:
    """
    EDINETの取得対象日数を環境変数から取得する。
    """

    raw_value = os.getenv(
        "EDINET_LOOKBACK_DAYS",
        str(DEFAULT_LOOKBACK_DAYS),
    ).strip()

    try:
        lookback_days = int(raw_value)

    except ValueError as error:
        raise RuntimeError(
            "EDINET_LOOKBACK_DAYSは整数で指定してください。"
            f"指定値: {raw_value}"
        ) from error

    if not 1 <= lookback_days <= MAX_LOOKBACK_DAYS:
        raise RuntimeError(
            "EDINET_LOOKBACK_DAYSは1から"
            f"{MAX_LOOKBACK_DAYS}の範囲で指定してください。"
            f"指定値: {lookback_days}"
        )

    return lookback_days


# ============================================================
# HTTP
# ============================================================

def create_http_session() -> requests.Session:
    """
    リトライ機能付きHTTPセッションを作成する。
    """

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=2,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=[
            "GET",
            "POST",
        ],
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=5,
        pool_maxsize=5,
    )

    session = requests.Session()

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        }
    )

    return session


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
    Discord Webhookへ通知する。
    """

    webhook_url = os.getenv(
        "DISCORD_WEBHOOK_URL",
        "",
    ).strip()

    if not webhook_url:
        print(
            "DISCORD_WEBHOOK_URLが未設定のため、"
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
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat(),
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
# Google認証
# ============================================================

def create_google_sheets_service():
    """
    Google Sheets APIクライアントを作成する。
    """

    credentials_json = get_required_environment_variable(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    try:
        credentials_info = json.loads(credentials_json)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSONを"
            "JSONとして読み込めませんでした。"
        ) from error

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
# Googleスプレッドシート共通処理
# ============================================================

def get_or_create_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
) -> int:
    """
    シートを取得し、存在しない場合は作成する。
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
        response["replies"][0]
        ["addSheet"]
        ["properties"]
        ["sheetId"]
    )


def read_sheet_values(
    sheets_service,
    spreadsheet_id: str,
    range_name: str,
) -> list[list[Any]]:
    """
    スプレッドシートから値を取得する。
    """

    response = (
        sheets_service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=range_name,
        )
        .execute()
    )

    return response.get("values", [])


def column_number_to_letter(column_number: int) -> str:
    """
    列番号をA1記法の列文字へ変換する。
    """

    result = ""
    number = column_number

    while number > 0:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result

    return result


def normalize_sheet_row(
    row: list[Any],
    width: int,
) -> list[Any]:
    """
    行の長さを指定列数へ合わせる。
    """

    normalized = list(row[:width])

    if len(normalized) < width:
        normalized.extend(
            [""] * (width - len(normalized))
        )

    return normalized


# ============================================================
# 銘柄コード
# ============================================================

def normalize_security_code(value: Any) -> str:
    """
    EDINETの5桁証券コードを東証4桁コードへ変換する。

    例:
    72030 -> 7203
    130A0 -> 130A
    """

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    text = str(value).strip().upper()

    if text.endswith(".0"):
        text = text[:-2]

    text = "".join(text.split())

    if len(text) == 5:
        text = text[:4]

    return text


# ============================================================
# 銘柄マスター
# ============================================================

def load_stock_master(
    sheets_service,
    spreadsheet_id: str,
) -> dict[str, dict[str, str]]:
    """
    銘柄マスターを読み込む。
    """

    values = read_sheet_values(
        sheets_service,
        spreadsheet_id,
        f"'{MASTER_SHEET_NAME}'!A:N",
    )

    if len(values) < 2:
        raise RuntimeError(
            "銘柄マスターにデータがありません。"
        )

    headers = values[0]

    required_headers = [
        "証券コード",
        "銘柄名",
        "市場",
    ]

    missing_headers = [
        header
        for header in required_headers
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            "銘柄マスターに必要な列がありません。"
            f"不足列: {missing_headers}"
        )

    code_index = headers.index("証券コード")
    company_index = headers.index("銘柄名")
    market_index = headers.index("市場")

    stock_master: dict[str, dict[str, str]] = {}

    for row in values[1:]:
        row = normalize_sheet_row(
            row,
            len(headers),
        )

        code = normalize_security_code(
            row[code_index]
        )

        if not code:
            continue

        stock_master[code] = {
            "company_name": str(
                row[company_index]
            ).strip(),
            "market": str(
                row[market_index]
            ).strip(),
        }

    if len(stock_master) < 3000:
        raise RuntimeError(
            "銘柄マスターの件数が少なすぎます。"
            f"件数: {len(stock_master):,}"
        )

    print(
        "銘柄マスターを読み込みました。"
        f"件数: {len(stock_master):,}"
    )

    return stock_master


# ============================================================
# EDINETコード一覧
# ============================================================

def find_dataframe_column(
    dataframe: pd.DataFrame,
    candidates: list[str],
) -> str:
    """
    候補名からDataFrameの列を探す。
    """

    normalized_columns = {
        str(column)
        .replace(" ", "")
        .replace("　", "")
        .strip(): column
        for column in dataframe.columns
    }

    for candidate in candidates:
        normalized_candidate = (
            candidate
            .replace(" ", "")
            .replace("　", "")
            .strip()
        )

        if normalized_candidate in normalized_columns:
            return normalized_columns[
                normalized_candidate
            ]

    raise RuntimeError(
        "EDINETコード一覧に必要な列がありません。"
        f"候補: {candidates}, "
        f"実際の列: {list(dataframe.columns)}"
    )


def download_edinet_code_map(
    session: requests.Session,
) -> dict[str, str]:
    """
    EDINETコード一覧を取得し、
    EDINETコードから証券コードへの対応表を作成する。
    """

    print("EDINETコード一覧を取得します。")

    response = session.get(
        EDINET_CODE_LIST_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    if len(response.content) < 10_000:
        raise RuntimeError(
            "EDINETコード一覧ZIPのサイズが"
            "小さすぎます。"
        )

    with zipfile.ZipFile(
        io.BytesIO(response.content)
    ) as archive:
        csv_names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
        ]

        if not csv_names:
            raise RuntimeError(
                "EDINETコード一覧ZIP内に"
                "CSVがありません。"
            )

        csv_content = archive.read(
            csv_names[0]
        )

    dataframe = None
    last_error: Exception | None = None

    for skip_rows in [1, 0]:
        try:
            candidate = pd.read_csv(
                io.BytesIO(csv_content),
                encoding="cp932",
                dtype=str,
                skiprows=skip_rows,
            )

            if any(
                "ＥＤＩＮＥＴコード" in str(column)
                or "EDINETコード" in str(column)
                for column in candidate.columns
            ):
                dataframe = candidate
                break

        except Exception as error:
            last_error = error

    if dataframe is None:
        raise RuntimeError(
            "EDINETコード一覧CSVを"
            "読み込めませんでした。"
        ) from last_error

    edinet_column = find_dataframe_column(
        dataframe,
        [
            "ＥＤＩＮＥＴコード",
            "EDINETコード",
        ],
    )

    security_column = find_dataframe_column(
        dataframe,
        [
            "証券コード",
        ],
    )

    code_map: dict[str, str] = {}

    for _, record in dataframe.iterrows():
        edinet_code_raw = record[edinet_column]

        if pd.isna(edinet_code_raw):
            continue

        edinet_code = str(
            edinet_code_raw
        ).strip().upper()

        security_code = normalize_security_code(
            record[security_column]
        )

        if not edinet_code or not security_code:
            continue

        code_map[edinet_code] = security_code

    if len(code_map) < 3000:
        raise RuntimeError(
            "EDINETコード対応表の件数が"
            "少なすぎます。"
            f"件数: {len(code_map):,}"
        )

    print(
        "EDINETコード対応表を作成しました。"
        f"件数: {len(code_map):,}"
    )

    return code_map


# ============================================================
# EDINET API
# ============================================================

def build_target_dates(
    lookback_days: int,
) -> list[date]:
    """
    取得対象日を作成する。
    """

    end_date = datetime.now(JST).date()

    start_date = (
        end_date
        - timedelta(days=lookback_days - 1)
    )

    return [
        start_date + timedelta(days=offset)
        for offset in range(lookback_days)
    ]


def fetch_edinet_documents_for_date(
    session: requests.Session,
    api_key: str,
    target_date: date,
) -> list[dict[str, Any]]:
    """
    指定日のEDINET提出書類一覧を取得する。
    """

    response = session.get(
        EDINET_DOCUMENTS_API_URL,
        params={
            "date": target_date.isoformat(),
            "type": 2,
            "Subscription-Key": api_key,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    try:
        payload = response.json()

    except requests.JSONDecodeError as error:
        raise RuntimeError(
            "EDINET APIのレスポンスを"
            "JSONとして解析できませんでした。"
            f"対象日: {target_date}"
        ) from error

    metadata = payload.get(
        "metadata",
        {},
    )

    status = str(
        metadata.get("status", "")
    )

    message = str(
        metadata.get("message", "")
    )

    if status and status != "200":
        raise RuntimeError(
            "EDINET APIがエラーを返しました。"
            f"対象日: {target_date}, "
            f"status: {status}, "
            f"message: {message}"
        )

    results = payload.get("results", [])

    if not isinstance(results, list):
        raise RuntimeError(
            "EDINET APIのresultsが"
            "配列ではありません。"
        )

    print(
        f"EDINET {target_date}: "
        f"{len(results):,}件"
    )

    return results


def is_target_document(
    document: dict[str, Any],
) -> bool:
    """
    対象書類か判定する。
    """

    ordinance_code = str(
        document.get("ordinanceCode") or ""
    )

    document_type_code = str(
        document.get("docTypeCode") or ""
    )

    if ordinance_code != TARGET_ORDINANCE_CODE:
        return False

    if document_type_code not in TARGET_DOCUMENT_TYPES:
        return False

    description = str(
        document.get("docDescription") or ""
    )

    excluded_words = [
        "内国投資信託受益証券",
        "外国投資信託受益証券",
        "投資法人",
    ]

    if any(
        word in description
        for word in excluded_words
    ):
        return False

    return True


def fetch_target_documents(
    session: requests.Session,
    api_key: str,
    target_dates: list[date],
) -> list[dict[str, Any]]:
    """
    指定期間の対象書類を取得する。
    """

    target_documents: list[dict[str, Any]] = []

    for index, target_date in enumerate(
        target_dates,
        start=1,
    ):
        print(
            "EDINET書類一覧取得: "
            f"{index}/{len(target_dates)} "
            f"{target_date}"
        )

        documents = fetch_edinet_documents_for_date(
            session,
            api_key,
            target_date,
        )

        target_documents.extend(
            document
            for document in documents
            if is_target_document(document)
        )

        if index < len(target_dates):
            time.sleep(
                API_REQUEST_INTERVAL_SECONDS
            )

    unique_documents: dict[
        str,
        dict[str, Any],
    ] = {}

    for document in target_documents:
        doc_id = str(
            document.get("docID") or ""
        ).strip()

        if not doc_id:
            continue

        unique_documents[doc_id] = document

    result = list(
        unique_documents.values()
    )

    print(
        "対象書類を抽出しました。"
        f"件数: {len(result):,}"
    )

    return result


# ============================================================
# EDINET書類の変換
# ============================================================

def flag_text(value: Any) -> str:
    """
    EDINETの0/1フラグを表示用文字列に変換する。
    """

    text = str(
        value
        if value is not None
        else ""
    ).strip()

    if text == "1":
        return "あり"

    if text == "0":
        return "なし"

    return text


def withdrawal_status_text(value: Any) -> str:
    """
    取下げ状態を表示用文字列に変換する。
    """

    text = str(
        value
        if value is not None
        else ""
    ).strip()

    status_map = {
        "0": "通常",
        "1": "取下げ",
    }

    return status_map.get(
        text,
        text,
    )


def normalize_value(value: Any) -> str:
    """
    NoneやNaNを空文字へ変換する。
    """

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def resolve_security_code(
    document: dict[str, Any],
    edinet_code_map: dict[str, str],
) -> str:
    """
    APIレスポンスまたはEDINETコード一覧から
    証券コードを取得する。
    """

    security_code = normalize_security_code(
        document.get("secCode")
    )

    if security_code:
        return security_code

    edinet_code = normalize_value(
        document.get("edinetCode")
    ).upper()

    return edinet_code_map.get(
        edinet_code,
        "",
    )


def convert_document_to_row(
    document: dict[str, Any],
    stock_master: dict[str, dict[str, str]],
    edinet_code_map: dict[str, str],
) -> list[Any] | None:
    """
    EDINET書類をスプレッドシート行へ変換する。
    """

    security_code = resolve_security_code(
        document,
        edinet_code_map,
    )

    if security_code not in stock_master:
        return None

    master = stock_master[security_code]

    document_type_code = normalize_value(
        document.get("docTypeCode")
    )

    submit_datetime = normalize_value(
        document.get("submitDateTime")
    )

    submit_date = (
        submit_datetime[:10]
        if len(submit_datetime) >= 10
        else ""
    )

    return [
        datetime.now(JST).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        submit_datetime,
        submit_date,
        security_code,
        master["company_name"],
        master["market"],
        normalize_value(
            document.get("filerName")
        ),
        normalize_value(
            document.get("edinetCode")
        ),
        normalize_value(
            document.get("docID")
        ),
        TARGET_DOCUMENT_TYPES.get(
            document_type_code,
            document_type_code,
        ),
        document_type_code,
        normalize_value(
            document.get("docDescription")
        ),
        normalize_value(
            document.get("periodStart")
        ),
        normalize_value(
            document.get("periodEnd")
        ),
        normalize_value(
            document.get("ordinanceCode")
        ),
        normalize_value(
            document.get("formCode")
        ),
        normalize_value(
            document.get("parentDocID")
        ),
        withdrawal_status_text(
            document.get("withdrawalStatus")
        ),
        flag_text(
            document.get("docInfoEditStatus")
        ),
        normalize_value(
            document.get("disclosureStatus")
        ),
        flag_text(
            document.get("xbrlFlag")
        ),
        flag_text(
            document.get("pdfFlag")
        ),
        flag_text(
            document.get("csvFlag")
        ),
        normalize_value(
            document.get("legalStatus")
        ),
        "金融庁EDINET API",
        EDINET_VIEWER_URL,
    ]


def convert_documents_to_rows(
    documents: list[dict[str, Any]],
    stock_master: dict[str, dict[str, str]],
    edinet_code_map: dict[str, str],
) -> list[list[Any]]:
    """
    対象書類をスプレッドシート行へ変換する。
    """

    rows: list[list[Any]] = []

    for document in documents:
        row = convert_document_to_row(
            document,
            stock_master,
            edinet_code_map,
        )

        if row is not None:
            rows.append(row)

    print(
        "東証銘柄マスターと一致した書類: "
        f"{len(rows):,}/{len(documents):,}件"
    )

    return rows


# ============================================================
# 既存データとの統合
# ============================================================

def load_existing_document_rows(
    sheets_service,
    spreadsheet_id: str,
) -> list[list[Any]]:
    """
    EDINET書類シートの既存行を取得する。
    """

    values = read_sheet_values(
        sheets_service,
        spreadsheet_id,
        f"'{EDINET_SHEET_NAME}'!A:Z",
    )

    if not values:
        return []

    existing_headers = values[0]

    if existing_headers != OUTPUT_HEADERS:
        raise RuntimeError(
            "EDINET書類シートの列構成が"
            "現在のプログラムと一致しません。"
            "シートを削除して再実行するか、"
            "列構成を確認してください。"
        )

    return [
        normalize_sheet_row(
            row,
            len(OUTPUT_HEADERS),
        )
        for row in values[1:]
        if row
    ]


def merge_document_rows(
    existing_rows: list[list[Any]],
    fetched_rows: list[list[Any]],
) -> tuple[list[list[Any]], list[list[Any]]]:
    """
    docIDをキーに既存データと取得データを統合する。

    戻り値:
    - 統合後の全行
    - 今回初めて取得した新着行
    """

    doc_id_index = OUTPUT_HEADERS.index(
        "書類管理番号"
    )

    submit_datetime_index = OUTPUT_HEADERS.index(
        "提出日時"
    )

    existing_by_doc_id: dict[
        str,
        list[Any],
    ] = {}

    for row in existing_rows:
        doc_id = str(
            row[doc_id_index]
        ).strip()

        if doc_id:
            existing_by_doc_id[doc_id] = row

    existing_doc_ids = set(
        existing_by_doc_id.keys()
    )

    new_rows: list[list[Any]] = []

    for row in fetched_rows:
        doc_id = str(
            row[doc_id_index]
        ).strip()

        if not doc_id:
            continue

        if doc_id not in existing_doc_ids:
            new_rows.append(row)

        existing_by_doc_id[doc_id] = row

    merged_rows = list(
        existing_by_doc_id.values()
    )

    merged_rows.sort(
        key=lambda row: str(
            row[submit_datetime_index]
        ),
        reverse=True,
    )

    return merged_rows, new_rows


# ============================================================
# シート書き込み
# ============================================================

def write_edinet_sheet(
    sheets_service,
    spreadsheet_id: str,
    sheet_id: int,
    rows: list[list[Any]],
) -> None:
    """
    EDINET書類シートを更新する。
    """

    (
        sheets_service.spreadsheets()
        .values()
        .clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{EDINET_SHEET_NAME}'",
            body={},
        )
        .execute()
    )

    values = [
        OUTPUT_HEADERS,
        *rows,
    ]

    last_column = column_number_to_letter(
        len(OUTPUT_HEADERS)
    )

    last_row = len(values)

    target_range = (
        f"'{EDINET_SHEET_NAME}'!"
        f"A1:{last_column}{last_row}"
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
                "fields": (
                    "gridProperties.frozenRowCount"
                ),
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(
                        OUTPUT_HEADERS
                    ),
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
                        "endColumnIndex": len(
                            OUTPUT_HEADERS
                        ),
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
                    "endIndex": len(
                        OUTPUT_HEADERS
                    ),
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
        "EDINET書類シートを更新しました。"
        f"保存件数: {len(rows):,}"
    )


# ============================================================
# 通知用集計
# ============================================================

def count_by_document_type(
    rows: list[list[Any]],
) -> dict[str, int]:
    """
    書類種別別に件数を集計する。
    """

    type_index = OUTPUT_HEADERS.index(
        "書類種別"
    )

    result = {
        document_name: 0
        for document_name
        in TARGET_DOCUMENT_TYPES.values()
    }

    for row in rows:
        document_type = str(
            row[type_index]
        )

        if document_type in result:
            result[document_type] += 1

    return result


def create_new_document_preview(
    rows: list[list[Any]],
    limit: int = 8,
) -> str:
    """
    Discord通知用の新着書類一覧を作成する。
    """

    if not rows:
        return ""

    submit_index = OUTPUT_HEADERS.index(
        "提出日時"
    )

    code_index = OUTPUT_HEADERS.index(
        "証券コード"
    )

    company_index = OUTPUT_HEADERS.index(
        "銘柄名"
    )

    type_index = OUTPUT_HEADERS.index(
        "書類種別"
    )

    sorted_rows = sorted(
        rows,
        key=lambda row: str(
            row[submit_index]
        ),
        reverse=True,
    )

    lines = []

    for row in sorted_rows[:limit]:
        lines.append(
            f"- `{row[code_index]}` "
            f"{row[company_index]}："
            f"{row[type_index]}"
        )

    remaining = len(rows) - limit

    if remaining > 0:
        lines.append(
            f"- ほか{remaining:,}件"
        )

    return "\n".join(lines)


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    EDINET書類一覧更新処理を実行する。
    """

    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )

    edinet_api_key = get_required_environment_variable(
        "EDINET_API_KEY"
    )

    lookback_days = get_lookback_days()

    print(
        "EDINET取得期間を設定しました。"
        f"直近{lookback_days}日"
    )

    sheets_service = create_google_sheets_service()

    sheet_id = get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        EDINET_SHEET_NAME,
    )

    stock_master = load_stock_master(
        sheets_service,
        spreadsheet_id,
    )

    existing_rows = load_existing_document_rows(
        sheets_service,
        spreadsheet_id,
    )

    existing_count = len(existing_rows)

    print(
        "既存のEDINET書類を読み込みました。"
        f"件数: {existing_count:,}"
    )

    session = create_http_session()

    edinet_code_map = download_edinet_code_map(
        session
    )

    target_dates = build_target_dates(
        lookback_days
    )

    documents = fetch_target_documents(
        session,
        edinet_api_key,
        target_dates,
    )

    fetched_rows = convert_documents_to_rows(
        documents,
        stock_master,
        edinet_code_map,
    )

    merged_rows, new_rows = merge_document_rows(
        existing_rows,
        fetched_rows,
    )

    write_edinet_sheet(
        sheets_service,
        spreadsheet_id,
        sheet_id,
        merged_rows,
    )

    print(
        "EDINET書類一覧の更新が完了しました。"
        f"新着: {len(new_rows):,}件"
    )

    if not new_rows:
        print(
            "新着書類がないため、"
            "Discord通知を省略します。"
        )
        return

    type_counts = count_by_document_type(
        new_rows
    )

    preview = create_new_document_preview(
        new_rows
    )

    description_lines = [
        "EDINETから新着書類を取得しました。",
        "",
        f"**取得対象:** 直近{lookback_days}日",
        f"**新着:** {len(new_rows):,}件",
        (
            "**有価証券報告書:** "
            f"{type_counts['有価証券報告書']:,}件"
        ),
        (
            "**訂正有価証券報告書:** "
            f"{type_counts['訂正有価証券報告書']:,}件"
        ),
        (
            "**半期報告書:** "
            f"{type_counts['半期報告書']:,}件"
        ),
        (
            "**訂正半期報告書:** "
            f"{type_counts['訂正半期報告書']:,}件"
        ),
        f"**保存総数:** {len(merged_rows):,}件",
        "",
        "**新着書類:**",
        preview,
        "",
        f"**更新先:** `{EDINET_SHEET_NAME}`",
        "**データ出典:** 金融庁EDINET API",
    ]

    send_discord_notification(
        "📄 EDINET新着書類",
        "\n".join(description_lines),
        success=True,
    )


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
            "EDINET書類一覧更新中に"
            "エラーが発生しました。",
            file=sys.stderr,
        )

        traceback.print_exc()

        try:
            send_discord_notification(
                "❌ EDINET書類一覧更新失敗",
                "\n".join(
                    [
                        "EDINET書類一覧の更新中に"
                        "エラーが発生しました。",
                        "",
                        "```text",
                        error_message[:3000],
                        "```",
                        "",
                        "GitHub Actionsのログを"
                        "確認してください。",
                    ]
                ),
                success=False,
            )

        except Exception:
            print(
                "Discordへのエラー通知にも"
                "失敗しました。",
                file=sys.stderr,
            )

            traceback.print_exc()

        sys.exit(1)
