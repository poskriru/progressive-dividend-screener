"""
JPX公式の東京証券取引所日報から最新の株式相場表PDFを取得し、
東証上場普通株式の株価をGoogleスプレッドシートへ書き込む。

入力:
- JPX 東京証券取引所日報
- Googleスプレッドシートの「銘柄マスター」

出力:
- Googleスプレッドシートの「最新株価」
- Discord Webhook通知
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import io
import json
import math
import os
import re
import sys
import traceback
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo


# ============================================================
# 外部ライブラリ
# ============================================================

import pdfplumber
import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection


# ============================================================
# 定数
# ============================================================

JPX_DAILY_REPORT_URL = (
    "https://www.jpx.co.jp/markets/"
    "statistics-equities/daily/index.html"
)

MASTER_SHEET_NAME = "銘柄マスター"
PRICE_SHEET_NAME = "最新株価"

GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

JST = ZoneInfo("Asia/Tokyo")

REQUEST_TIMEOUT_SECONDS = 120

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; progressive-dividend-screener/0.2; "
    "+https://github.com/)"
)

MINIMUM_PARSED_RECORDS = 3000
MINIMUM_DATABASE_PRICE_RECORDS = 3000
MINIMUM_MATCH_RATE = 0.80

DATABASE_APPLICATION_NAME = (
    "progressive-dividend-screener-stock-prices"
)

DATABASE_PRICE_SOURCE = (
    "JPX東京証券取引所日報"
)

PDF_REFERENCE_WIDTH = 1191.0
PDF_REFERENCE_HEIGHT = 842.0

PDF_VERTICAL_LINES = [
    72,
    113,
    153,
    283,
    343,
    403,
    463,
    523,
    583,
    643,
    703,
    763,
    828,
    893,
    958,
    1038,
    1119,
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


# ============================================================
# 共通HTTP処理
# ============================================================

def create_http_session() -> requests.Session:
    """
    共通User-Agentを設定したHTTPセッションを作成する。
    """

    session = requests.Session()

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
    対象シートを取得する。
    存在しなければ作成する。
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


def read_sheet_values(
    sheets_service,
    spreadsheet_id: str,
    range_name: str,
) -> list[list[Any]]:
    """
    Googleスプレッドシートから値を取得する。
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


# ============================================================
# 銘柄マスター取得
# ============================================================

def normalize_security_code(value: Any) -> str:
    """
    証券コードを比較用文字列へ変換する。
    """

    if value is None:
        return ""

    text = str(value).strip().upper()

    if text.endswith(".0"):
        text = text[:-2]

    text = re.sub(r"\s+", "", text)

    return text


def load_stock_master(
    sheets_service,
    spreadsheet_id: str,
) -> dict[str, dict[str, str]]:
    """
    「銘柄マスター」を読み込む。
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
    name_index = headers.index("銘柄名")
    market_index = headers.index("市場")

    master: dict[str, dict[str, str]] = {}

    for row in values[1:]:
        if len(row) <= code_index:
            continue

        code = normalize_security_code(row[code_index])

        if not code:
            continue

        company_name = (
            str(row[name_index]).strip()
            if len(row) > name_index
            else ""
        )

        market = (
            str(row[market_index]).strip()
            if len(row) > market_index
            else ""
        )

        master[code] = {
            "company_name": company_name,
            "market": market,
        }

    if len(master) < 3000:
        raise RuntimeError(
            "銘柄マスターの件数が少なすぎます。"
            f"取得件数: {len(master):,}"
        )

    print(
        "銘柄マスターを読み込みました。"
        f"件数: {len(master):,}"
    )

    return master


# ============================================================
# JPX最新PDF URL取得
# ============================================================

def find_latest_stock_quotation_pdf(
    session: requests.Session,
) -> tuple[str, str]:
    """
    東京証券取引所日報ページから
    最新の株式相場表PDFを取得する。

    戻り値:
    - PDF URL
    - 立会日 YYYY-MM-DD
    """

    print(
        "JPX東京証券取引所日報ページを取得します。"
    )

    response = session.get(
        JPX_DAILY_REPORT_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.content,
        "html.parser",
    )

    candidates: list[tuple[str, str]] = []

    pattern = re.compile(
        r"stq_(\d{8})\.pdf(?:\?.*)?$",
        re.IGNORECASE,
    )

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()

        match = pattern.search(href)

        if not match:
            continue

        date_text = match.group(1)

        pdf_url = urljoin(
            JPX_DAILY_REPORT_URL,
            href,
        )

        candidates.append(
            (
                date_text,
                pdf_url,
            )
        )

    if not candidates:
        raise RuntimeError(
            "JPX日報ページから株式相場表PDFを"
            "発見できませんでした。"
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    date_text, pdf_url = candidates[0]

    trading_date = datetime.strptime(
        date_text,
        "%Y%m%d",
    ).strftime("%Y-%m-%d")

    print(
        "最新の株式相場表を発見しました。"
        f"立会日: {trading_date}"
    )

    print(f"PDF URL: {pdf_url}")

    return pdf_url, trading_date


def download_pdf(
    session: requests.Session,
    pdf_url: str,
) -> bytes:
    """
    JPX株式相場表PDFを取得する。
    """

    response = session.get(
        pdf_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        "",
    ).lower()

    if "pdf" not in content_type:
        raise RuntimeError(
            "取得したファイルがPDFではありません。"
            f"Content-Type: {content_type}"
        )

    if len(response.content) < 100_000:
        raise RuntimeError(
            "取得したPDFのサイズが小さすぎます。"
            f"サイズ: {len(response.content):,} bytes"
        )

    print(
        "株式相場表PDFを取得しました。"
        f"サイズ: {len(response.content):,} bytes"
    )

    return response.content


# ============================================================
# 数値変換
# ============================================================

def parse_number(value: Any) -> float | None:
    """
    PDF内の数値文字列をfloatへ変換する。

    対応例:
    - 1,234.5
    - △12.5
    - ▲12.5
    - -12.5
    - －
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = re.sub(r"\s+", "", text)

    if text in {
        "-",
        "－",
        "―",
        "ー",
        "/",
    }:
        return None

    negative = False

    if text.startswith(("△", "▲")):
        negative = True
        text = text[1:]

    text = (
        text
        .replace(",", "")
        .replace("＋", "+")
        .replace("−", "-")
        .replace("－", "-")
    )

    text = re.sub(
        r"^[カウ]",
        "",
        text,
    )

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text,
    )

    if not match:
        return None

    number = float(match.group(0))

    if negative:
        number = -abs(number)

    return number


def native_number(
    value: float | int | None,
) -> int | float | str:
    """
    Google Sheets APIで送信可能な数値へ変換する。
    """

    if value is None:
        return ""

    number = float(value)

    if not math.isfinite(number):
        return ""

    if number.is_integer():
        return int(number)

    return round(number, 6)


def first_number(
    *values: float | None,
) -> float | None:
    """
    最初に見つかった有効な数値を返す。
    """

    for value in values:
        if value is not None:
            return value

    return None


def maximum_number(
    *values: float | None,
) -> float | None:
    """
    有効値の最大値を返す。
    """

    valid_values = [
        value
        for value in values
        if value is not None
    ]

    if not valid_values:
        return None

    return max(valid_values)


def minimum_number(
    *values: float | None,
) -> float | None:
    """
    有効値の最小値を返す。
    """

    valid_values = [
        value
        for value in values
        if value is not None
    ]

    if not valid_values:
        return None

    return min(valid_values)


# ============================================================
# PDF解析
# ============================================================

def is_section_one_page(
    page,
) -> bool:
    """
    PDFページが「1-」で始まる立会市場・普通取引か判定する。
    """

    words = page.extract_words() or []

    first_words = [
        str(word.get("text", "")).strip()
        for word in words[:40]
    ]

    return any(
        re.match(r"^1-\d+", word)
        for word in first_words
    )


def get_page_table_settings(page) -> dict[str, Any]:
    """
    PDFサイズに合わせて表の縦線座標を調整する。
    """

    x_scale = page.width / PDF_REFERENCE_WIDTH

    vertical_lines = [
        coordinate * x_scale
        for coordinate in PDF_VERTICAL_LINES
    ]

    return {
        "vertical_strategy": "explicit",
        "explicit_vertical_lines": vertical_lines,
        "horizontal_strategy": "text",
        "intersection_tolerance": 5,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "text_tolerance": 3,
    }


def normalize_pdf_cell(value: Any) -> str:
    """
    PDFセル内の改行や余分な空白を整理する。
    """

    if value is None:
        return ""

    return (
        str(value)
        .replace("\r", "")
        .replace("\n", " ")
        .strip()
    )


def parse_pdf_row(
    row: list[Any],
) -> dict[str, Any] | None:
    """
    株式相場表の1行を解析する。
    """

    if len(row) != 16:
        return None

    cells = [
        normalize_pdf_cell(cell)
        for cell in row
    ]

    code = normalize_security_code(cells[0])

    if not re.fullmatch(
        r"[0-9A-Z]{4}",
        code,
    ):
        return None

    trading_unit = parse_number(cells[1])

    if trading_unit is None:
        return None

    morning_open = parse_number(cells[3])
    morning_high = parse_number(cells[4])
    morning_low = parse_number(cells[5])
    morning_close = parse_number(cells[6])

    afternoon_open = parse_number(cells[7])
    afternoon_high = parse_number(cells[8])
    afternoon_low = parse_number(cells[9])
    afternoon_close = parse_number(cells[10])

    final_quote = parse_number(cells[11])
    change = parse_number(cells[12])
    vwap = parse_number(cells[13])
    volume = parse_number(cells[14])
    turnover = parse_number(cells[15])

    open_price = first_number(
        morning_open,
        afternoon_open,
    )

    high_price = maximum_number(
        morning_high,
        afternoon_high,
    )

    low_price = minimum_number(
        morning_low,
        afternoon_low,
    )

    close_price = first_number(
        afternoon_close,
        morning_close,
    )

    previous_close = None
    change_rate = None

    if close_price is not None and change is not None:
        previous_close = close_price - change

        if previous_close != 0:
            change_rate = (
                change / previous_close * 100
            )

    return {
        "code": code,
        "pdf_name": cells[2],
        "trading_unit": trading_unit,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "final_quote": final_quote,
        "previous_close": previous_close,
        "change": change,
        "change_rate": change_rate,
        "vwap": vwap,
        "volume": volume,
        "turnover": turnover,
    }


def parse_stock_quotation_pdf(
    pdf_content: bytes,
) -> dict[str, dict[str, Any]]:
    """
    株式相場表PDFの立会市場・普通取引を解析する。
    """

    records: dict[str, dict[str, Any]] = {}

    section_started = False
    section_pages = 0

    with pdfplumber.open(
        io.BytesIO(pdf_content)
    ) as pdf:
        print(
            "PDF解析を開始します。"
            f"総ページ数: {len(pdf.pages):,}"
        )

        for page_index, page in enumerate(pdf.pages):
            is_target_page = is_section_one_page(page)

            if not is_target_page:
                if section_started:
                    break

                continue

            section_started = True
            section_pages += 1

            y_scale = page.height / PDF_REFERENCE_HEIGHT

            if section_pages == 1:
                top = 307 * y_scale
            else:
                top = 137 * y_scale

            bottom = 750 * y_scale

            cropped_page = page.within_bbox(
                (
                    0,
                    top,
                    page.width,
                    min(bottom, page.height),
                )
            )

            table = cropped_page.extract_table(
                get_page_table_settings(page)
            )

            if not table:
                print(
                    "表を取得できなかったページがあります。"
                    f"ページ: {page_index + 1}",
                    file=sys.stderr,
                )
                continue

            for row in table:
                parsed = parse_pdf_row(row)

                if parsed is None:
                    continue

                code = parsed["code"]

                records[code] = parsed

            if section_pages % 25 == 0:
                print(
                    "PDF解析中: "
                    f"{section_pages:,}ページ、"
                    f"{len(records):,}銘柄"
                )

    if not section_started:
        raise RuntimeError(
            "PDF内に立会市場・普通取引のページを"
            "発見できませんでした。"
        )

    if len(records) < MINIMUM_PARSED_RECORDS:
        raise RuntimeError(
            "PDFから取得した銘柄数が少なすぎます。"
            "PDF形式が変更された可能性があります。"
            f"取得件数: {len(records):,}"
        )

    print(
        "PDF解析が完了しました。"
        f"対象ページ数: {section_pages:,}、"
        f"解析件数: {len(records):,}"
    )

    return records


# ============================================================
# 銘柄マスターとの結合
# ============================================================

def create_price_rows(
    stock_master: dict[str, dict[str, str]],
    price_records: dict[str, dict[str, Any]],
    trading_date: str,
    pdf_url: str,
) -> tuple[list[str], list[list[Any]], int]:
    """
    銘柄マスターと株価データを結合する。
    """

    headers = [
        "更新日時",
        "株価基準日",
        "証券コード",
        "銘柄名",
        "市場",
        "売買単位",
        "始値",
        "高値",
        "安値",
        "終値",
        "前日終値",
        "前日比",
        "騰落率（%）",
        "最終気配",
        "VWAP",
        "出来高",
        "売買代金（千円）",
        "株価取得状態",
        "データ出典",
        "出典URL",
    ]

    update_time = datetime.now(JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    rows: list[list[Any]] = []
    matched_count = 0

    market_order = {
        "プライム": 1,
        "スタンダード": 2,
        "グロース": 3,
    }

    sorted_master = sorted(
        stock_master.items(),
        key=lambda item: (
            market_order.get(
                item[1]["market"],
                99,
            ),
            item[0],
        ),
    )

    for code, master_record in sorted_master:
        price = price_records.get(code)

        if price is None:
            row = [
                update_time,
                trading_date,
                code,
                master_record["company_name"],
                master_record["market"],
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "相場表に該当なし",
                "JPX東京証券取引所日報",
                pdf_url,
            ]

            rows.append(row)
            continue

        matched_count += 1

        row = [
            update_time,
            trading_date,
            code,
            master_record["company_name"],
            master_record["market"],
            native_number(price["trading_unit"]),
            native_number(price["open"]),
            native_number(price["high"]),
            native_number(price["low"]),
            native_number(price["close"]),
            native_number(price["previous_close"]),
            native_number(price["change"]),
            native_number(price["change_rate"]),
            native_number(price["final_quote"]),
            native_number(price["vwap"]),
            native_number(price["volume"]),
            native_number(price["turnover"]),
            (
                "終値あり"
                if price["close"] is not None
                else "終値なし"
            ),
            "JPX東京証券取引所日報",
            pdf_url,
        ]

        rows.append(row)

    match_rate = (
        matched_count / len(stock_master)
        if stock_master
        else 0
    )

    if match_rate < MINIMUM_MATCH_RATE:
        raise RuntimeError(
            "銘柄マスターと株価データの一致率が"
            "低すぎます。誤ったデータを書き込まないため"
            "処理を停止しました。"
            f"一致件数: {matched_count:,}/"
            f"{len(stock_master):,} "
            f"({match_rate:.1%})"
        )

    print(
        "銘柄マスターと株価を結合しました。"
        f"一致件数: {matched_count:,}/"
        f"{len(stock_master):,} "
        f"({match_rate:.1%})"
    )

    return headers, rows, matched_count

# ============================================================
# PostgreSQL保存
# ============================================================

def parse_database_date(
    value: Any,
) -> date:
    """
    PostgreSQLへ保存する日付へ変換する。
    """

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if not text:
        raise RuntimeError(
            "PostgreSQLへ保存する日付が空です。"
        )

    try:
        return date.fromisoformat(text)

    except ValueError as error:
        raise RuntimeError(
            "日付をYYYY-MM-DD形式として"
            "読み込めません。"
            f"値={text}"
        ) from error


def parse_database_datetime(
    value: Any,
) -> datetime:
    """
    PostgreSQLへ保存する日時へ変換する。

    タイムゾーンがない場合は日本時間として扱う。
    """

    if isinstance(value, datetime):
        parsed_datetime = value
    else:
        text = str(value).strip()

        if not text:
            raise RuntimeError(
                "PostgreSQLへ保存する日時が空です。"
            )

        try:
            parsed_datetime = datetime.fromisoformat(
                text
            )

        except ValueError as error:
            raise RuntimeError(
                "日時を読み込めません。"
                f"値={text}"
            ) from error

    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(
            tzinfo=JST
        )

    return parsed_datetime


def parse_database_number(
    value: Any,
) -> Decimal | None:
    """
    PostgreSQLのnumeric型へ保存する数値へ変換する。

    空文字およびNoneはNULLとして扱う。
    """

    if value in ("", None):
        return None

    if isinstance(value, bool):
        raise RuntimeError(
            "数値列に真偽値が含まれています。"
            f"値={value}"
        )

    text = str(value).strip().replace(",", "")

    if not text:
        return None

    try:
        number = Decimal(text)

    except InvalidOperation as error:
        raise RuntimeError(
            "PostgreSQLへ保存する数値を"
            "読み込めません。"
            f"値={text}"
        ) from error

    if not number.is_finite():
        raise RuntimeError(
            "有限値ではない数値が含まれています。"
            f"値={text}"
        )

    return number


def build_price_database_records(
    headers: list[str],
    rows: list[list[Any]],
) -> list[tuple[Any, ...]]:
    """
    スプレッドシート出力用の株価データを、
    PostgreSQL保存用レコードへ変換する。
    """

    required_headers = [
        "更新日時",
        "株価基準日",
        "証券コード",
        "売買単位",
        "始値",
        "高値",
        "安値",
        "終値",
        "前日終値",
        "前日比",
        "騰落率（%）",
        "最終気配",
        "VWAP",
        "出来高",
        "売買代金（千円）",
        "株価取得状態",
        "データ出典",
        "出典URL",
    ]

    missing_headers = [
        header
        for header in required_headers
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            "PostgreSQL保存に必要な株価列が"
            "ありません。"
            f"不足列: {missing_headers}"
        )

    header_indexes = {
        header: headers.index(header)
        for header in required_headers
    }

    records: list[tuple[Any, ...]] = []
    record_keys: set[tuple[str, date, str]] = set()

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        def get_value(header: str) -> Any:
            index = header_indexes[header]

            if index >= len(row):
                return ""

            return row[index]

        security_code = normalize_security_code(
            get_value("証券コード")
        )

        if not security_code:
            raise RuntimeError(
                "証券コードが空の株価行があります。"
                f"行番号: {row_number}"
            )

        trading_date = parse_database_date(
            get_value("株価基準日")
        )

        source = str(
            get_value("データ出典")
        ).strip()

        if source != DATABASE_PRICE_SOURCE:
            raise RuntimeError(
                "想定外の株価データ出典です。"
                f"行番号: {row_number}, "
                f"データ出典: {source}"
            )

        record_key = (
            security_code,
            trading_date,
            source,
        )

        if record_key in record_keys:
            raise RuntimeError(
                "PostgreSQL保存対象の株価が"
                "重複しています。"
                f"証券コード: {security_code}, "
                f"株価基準日: {trading_date}, "
                f"データ出典: {source}"
            )

        record_keys.add(record_key)

        records.append(
            (
                security_code,
                trading_date,
                parse_database_number(
                    get_value("売買単位")
                ),
                parse_database_number(
                    get_value("始値")
                ),
                parse_database_number(
                    get_value("高値")
                ),
                parse_database_number(
                    get_value("安値")
                ),
                parse_database_number(
                    get_value("終値")
                ),
                parse_database_number(
                    get_value("前日終値")
                ),
                parse_database_number(
                    get_value("前日比")
                ),
                parse_database_number(
                    get_value("騰落率（%）")
                ),
                parse_database_number(
                    get_value("最終気配")
                ),
                parse_database_number(
                    get_value("VWAP")
                ),
                parse_database_number(
                    get_value("出来高")
                ),
                parse_database_number(
                    get_value("売買代金（千円）")
                ),
                str(
                    get_value("株価取得状態")
                ).strip(),
                source,
                str(
                    get_value("出典URL")
                ).strip(),
                parse_database_datetime(
                    get_value("更新日時")
                ),
            )
        )

    if len(records) < MINIMUM_DATABASE_PRICE_RECORDS:
        raise RuntimeError(
            "PostgreSQLへ保存する株価件数が"
            "少なすぎます。"
            f"保存対象: {len(records):,}件"
        )

    return records


def get_database_price_count(
    trading_date: str,
) -> int:
    """
    指定した株価基準日のJPX株価件数を取得する。
    """

    parsed_trading_date = parse_database_date(
        trading_date
    )

    with create_database_connection(
        DATABASE_APPLICATION_NAME
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) AS record_count
                FROM screener.daily_prices
                WHERE trading_date = %s
                  AND source = %s
                """,
                (
                    parsed_trading_date,
                    DATABASE_PRICE_SOURCE,
                ),
            )

            result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "PostgreSQLから株価件数を"
            "取得できませんでした。"
        )

    return int(result["record_count"])


def save_prices_to_database(
    headers: list[str],
    rows: list[list[Any]],
) -> dict[str, int]:
    """
    JPXの最新株価をPostgreSQLへ保存する。

    証券コード・株価基準日・データ出典が
    同じレコードはUPSERTする。
    """

    records = build_price_database_records(
        headers,
        rows,
    )

    expected_trading_dates = {
        record[1]
        for record in records
    }

    if len(expected_trading_dates) != 1:
        raise RuntimeError(
            "複数の株価基準日が混在しています。"
            f"株価基準日: {sorted(expected_trading_dates)}"
        )

    trading_date = next(
        iter(expected_trading_dates)
    )

    print(
        "PostgreSQLへ最新株価を保存します。"
        f"株価基準日: {trading_date}, "
        f"保存対象: {len(records):,}件"
    )

    with create_database_connection(
        DATABASE_APPLICATION_NAME
    ) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TEMP TABLE daily_prices_sync (
                        security_code text NOT NULL,
                        trading_date date NOT NULL,
                        trading_unit numeric(20, 6),
                        open_price numeric(20, 6),
                        high_price numeric(20, 6),
                        low_price numeric(20, 6),
                        close_price numeric(20, 6),
                        previous_close numeric(20, 6),
                        price_change numeric(20, 6),
                        change_rate_percent numeric(20, 6),
                        final_quote numeric(20, 6),
                        vwap numeric(20, 6),
                        volume numeric(28, 6),
                        turnover_thousand_yen numeric(28, 6),
                        price_status text,
                        source text NOT NULL,
                        source_url text,
                        source_updated_at timestamptz
                    )
                    ON COMMIT DROP
                    """
                )

                with cursor.copy(
                    """
                    COPY daily_prices_sync (
                        security_code,
                        trading_date,
                        trading_unit,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        previous_close,
                        price_change,
                        change_rate_percent,
                        final_quote,
                        vwap,
                        volume,
                        turnover_thousand_yen,
                        price_status,
                        source,
                        source_url,
                        source_updated_at
                    )
                    FROM STDIN
                    """
                ) as copy:
                    for record in records:
                        copy.write_row(record)

                cursor.execute(
                    """
                    INSERT INTO screener.daily_prices (
                        security_code,
                        trading_date,
                        trading_unit,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        previous_close,
                        price_change,
                        change_rate_percent,
                        final_quote,
                        vwap,
                        volume,
                        turnover_thousand_yen,
                        adjustment_factor,
                        adjusted_close_price,
                        price_status,
                        source,
                        source_url,
                        source_updated_at,
                        fetched_at
                    )
                    SELECT
                        security_code,
                        trading_date,
                        trading_unit,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        previous_close,
                        price_change,
                        change_rate_percent,
                        final_quote,
                        vwap,
                        volume,
                        turnover_thousand_yen,
                        NULL,
                        NULL,
                        NULLIF(price_status, ''),
                        source,
                        NULLIF(source_url, ''),
                        source_updated_at,
                        CURRENT_TIMESTAMP
                    FROM daily_prices_sync
                    ON CONFLICT (
                        security_code,
                        trading_date,
                        source
                    )
                    DO UPDATE SET
                        trading_unit = EXCLUDED.trading_unit,
                        open_price = EXCLUDED.open_price,
                        high_price = EXCLUDED.high_price,
                        low_price = EXCLUDED.low_price,
                        close_price = EXCLUDED.close_price,
                        previous_close = EXCLUDED.previous_close,
                        price_change = EXCLUDED.price_change,
                        change_rate_percent = (
                            EXCLUDED.change_rate_percent
                        ),
                        final_quote = EXCLUDED.final_quote,
                        vwap = EXCLUDED.vwap,
                        volume = EXCLUDED.volume,
                        turnover_thousand_yen = (
                            EXCLUDED.turnover_thousand_yen
                        ),
                        price_status = EXCLUDED.price_status,
                        source_url = EXCLUDED.source_url,
                        source_updated_at = (
                            EXCLUDED.source_updated_at
                        ),
                        fetched_at = CURRENT_TIMESTAMP
                    """
                )

                cursor.execute(
                    """
                    SELECT
                        count(*) AS current_record_count,
                        count(*) FILTER (
                            WHERE price.close_price IS NOT NULL
                        ) AS close_price_count
                    FROM screener.daily_prices AS price
                    INNER JOIN daily_prices_sync AS sync
                        ON sync.security_code
                           = price.security_code
                       AND sync.trading_date
                           = price.trading_date
                       AND sync.source
                           = price.source
                    """
                )

                current_result = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT count(*) AS date_source_count
                    FROM screener.daily_prices
                    WHERE trading_date = %s
                      AND source = %s
                    """,
                    (
                        trading_date,
                        DATABASE_PRICE_SOURCE,
                    ),
                )

                total_result = cursor.fetchone()

    if current_result is None or total_result is None:
        raise RuntimeError(
            "PostgreSQL保存後の株価件数を"
            "確認できませんでした。"
        )

    result = {
        "saved_count": len(records),
        "current_record_count": int(
            current_result["current_record_count"]
        ),
        "close_price_count": int(
            current_result["close_price_count"]
        ),
        "date_source_count": int(
            total_result["date_source_count"]
        ),
    }

    if result["current_record_count"] != len(records):
        raise RuntimeError(
            "PostgreSQLへ保存された株価件数が"
            "保存対象件数と一致しません。"
            f"保存対象: {len(records):,}, "
            "DB確認件数: "
            f"{result['current_record_count']:,}"
        )

    print("PostgreSQLへの株価保存が完了しました。")
    print(
        "DB保存確認: "
        f"{result['current_record_count']:,}件"
    )
    print(
        "DB終値あり: "
        f"{result['close_price_count']:,}件"
    )
    print(
        "同一基準日・同一出典のDB総数: "
        f"{result['date_source_count']:,}件"
    )

    return result


# ============================================================
# 前回基準日取得
# ============================================================

def get_existing_trading_date(
    sheets_service,
    spreadsheet_id: str,
) -> str:
    """
    最新株価シートに記録されている基準日を取得する。
    """

    try:
        values = read_sheet_values(
            sheets_service,
            spreadsheet_id,
            f"'{PRICE_SHEET_NAME}'!B2",
        )

    except Exception:
        return ""

    if not values or not values[0]:
        return ""

    return str(values[0][0]).strip()


# ============================================================
# 最新株価シート書き込み
# ============================================================

def write_price_sheet(
    sheets_service,
    spreadsheet_id: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    """
    「最新株価」シートを更新する。
    """

    sheet_id = get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        PRICE_SHEET_NAME,
    )

    (
        sheets_service.spreadsheets()
        .values()
        .clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{PRICE_SHEET_NAME}'",
            body={},
        )
        .execute()
    )

    values = [headers, *rows]

    last_column = column_number_to_letter(
        len(headers)
    )

    last_row = len(values)

    target_range = (
        f"'{PRICE_SHEET_NAME}'!"
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
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": last_row,
                    "startColumnIndex": 6,
                    "endColumnIndex": 17,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": "NUMBER",
                            "pattern": "#,##0.00",
                        }
                    }
                },
                "fields": (
                    "userEnteredFormat.numberFormat"
                ),
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
        "最新株価シートを更新しました。"
        f"件数: {len(rows):,}"
    )


# ============================================================
# 集計
# ============================================================

def count_close_prices(
    headers: list[str],
    rows: list[list[Any]],
) -> int:
    """
    終値が取得できた銘柄数を数える。
    """

    close_index = headers.index("終値")

    return sum(
        1
        for row in rows
        if row[close_index] not in ("", None)
    )


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    最新株価をPostgreSQLと
    Googleスプレッドシートへ保存する。
    """

    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )

    sheets_service = create_google_sheets_service()

    stock_master = load_stock_master(
        sheets_service,
        spreadsheet_id,
    )

    session = create_http_session()

    pdf_url, trading_date = (
        find_latest_stock_quotation_pdf(session)
    )

    existing_sheet_date = get_existing_trading_date(
        sheets_service,
        spreadsheet_id,
    )

    existing_database_count = get_database_price_count(
        trading_date
    )

    sheet_is_current = (
        existing_sheet_date == trading_date
    )

    database_is_current = (
        existing_database_count
        == len(stock_master)
    )

    if sheet_is_current and database_is_current:
        print(
            "最新株価はPostgreSQLと"
            "Googleスプレッドシートの両方で"
            "更新済みです。"
        )
        print(f"株価基準日: {trading_date}")
        print(
            "DB登録件数: "
            f"{existing_database_count:,}件"
        )
        return

    if sheet_is_current and not database_is_current:
        print(
            "Googleスプレッドシートは更新済みですが、"
            "PostgreSQLの件数が一致しないため"
            "再取得してDBへ保存します。"
        )
        print(
            "DB既存件数: "
            f"{existing_database_count:,}件"
        )

    pdf_content = download_pdf(
        session,
        pdf_url,
    )

    price_records = parse_stock_quotation_pdf(
        pdf_content
    )

    headers, rows, matched_count = create_price_rows(
        stock_master,
        price_records,
        trading_date,
        pdf_url,
    )

    # PostgreSQLを正本候補として先に更新する。
    # DB更新に失敗した場合は、スプレッドシートを
    # 更新せず処理を停止する。
    database_result = save_prices_to_database(
        headers,
        rows,
    )

    # 移行期間中はGoogleスプレッドシートにも保存する。
    write_price_sheet(
        sheets_service,
        spreadsheet_id,
        headers,
        rows,
    )

    close_count = count_close_prices(
        headers,
        rows,
    )

    missing_count = (
        len(stock_master) - matched_count
    )

    if (
        close_count
        != database_result["close_price_count"]
    ):
        raise RuntimeError(
            "PostgreSQLとスプレッドシート用データの"
            "終値件数が一致しません。"
            f"スプレッドシート用: {close_count:,}, "
            "PostgreSQL: "
            f"{database_result['close_price_count']:,}"
        )

    description = "\n".join(
        [
            "JPX東京証券取引所日報から"
            "最新株価を更新しました。",
            "",
            f"**株価基準日:** {trading_date}",
            (
                f"**銘柄マスター:** "
                f"{len(stock_master):,}銘柄"
            ),
            (
                f"**相場表一致:** "
                f"{matched_count:,}銘柄"
            ),
            (
                f"**終値取得:** "
                f"{close_count:,}銘柄"
            ),
            (
                f"**相場表該当なし:** "
                f"{missing_count:,}銘柄"
            ),
            "",
            (
                "**DB保存確認:** "
                f"{database_result['current_record_count']:,}件"
            ),
            (
                "**DB終値あり:** "
                f"{database_result['close_price_count']:,}件"
            ),
            "",
            (
                f"**更新先:** "
                f"`PostgreSQL / {PRICE_SHEET_NAME}`"
            ),
            (
                "**データ出典:** "
                "JPX東京証券取引所日報"
            ),
        ]
    )

    send_discord_notification(
        "✅ 最新株価更新完了",
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
            "最新株価更新中にエラーが発生しました。",
            file=sys.stderr,
        )

        traceback.print_exc()

        try:
            send_discord_notification(
                "❌ 最新株価更新失敗",
                "\n".join(
                    [
                        "最新株価の更新中に"
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
