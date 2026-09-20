"""
TDnet適時開示情報閲覧サービスの公開一覧から、
配当・株主還元方針に関連する表題を取得する。

PDF本文の内容を断定せず、表題キーワードによる一次抽出として
Google Sheetsへ履歴と累進配当方針候補を出力する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import os
import re
import sys
import time
import traceback
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin


# ============================================================
# 外部ライブラリ
# ============================================================

import requests
from bs4 import BeautifulSoup


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from load_tdnet_policy_pdf_analyses import (
    load_tdnet_policy_pdf_analysis_results,
)

from store_tdnet_policy_pdf_analyses import (
    TdnetPolicyPdfTarget,
    process_tdnet_policy_pdf_targets,
)

from sync_tdnet_policy_analysis_sheets import (
    TDNET_DISCLOSURE_BASE_HEADERS,
    TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
    sync_tdnet_policy_analysis_sheets,
)

from update_edinet_financials import (
    JST,
    create_google_sheets_service,
    get_or_create_sheet,
    get_required_environment_variable,
    read_sheet,
    send_discord_notification,
    write_sheet,
)

# ============================================================
# 定数
# ============================================================

TDNET_BASE_URL = "https://www.release.tdnet.info/inbs/"
TDNET_INDEX_URL = urljoin(TDNET_BASE_URL, "I_main_00.html")
TDNET_SOURCE_NAME = "TDnet適時開示情報閲覧サービス"

TDNET_DISCLOSURE_SHEET_NAME = "TDnet配当開示"
PROGRESSIVE_POLICY_SHEET_NAME = "累進配当方針候補"

TDNET_DISCLOSURE_HEADERS = [
    "開示ID",
    "公開日",
    "公開時刻",
    "証券コード",
    "会社名",
    "表題",
    "分類",
    "累進配当方針候補",
    "一致キーワード",
    "PDF URL",
    "上場取引所",
    "データ出典",
]

PROGRESSIVE_POLICY_HEADERS = [
    "更新日時",
    "証券コード",
    "会社名",
    "最新開示日",
    "最新開示時刻",
    "最新表題",
    "分類",
    "一致キーワード",
    "PDF URL",
    "判定注記",
]

DEFAULT_LOOKBACK_DAYS = 7
MAXIMUM_LOOKBACK_DAYS = 31
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 0.2
MAXIMUM_PAGE_REQUESTS = 100

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; progressive-dividend-screener/0.4; "
    "+https://github.com/poskriru/"
    "progressive-dividend-screener)"
)

POLICY_KEYWORDS = (
    "累進配当",
    "DOE",
    "株主還元方針",
    "配当方針",
    "配当政策",
)

DIVIDEND_KEYWORDS = (
    *POLICY_KEYWORDS,
    "配当予想",
    "剰余金の配当",
    "増配",
    "減配",
    "復配",
    "無配",
    "記念配当",
    "配当",
    "株主還元",
)

POLICY_CAUTION = (
    "TDnetの表題キーワードによる候補判定です。"
    "累進配当方針を確定するものではありません。"
    "PDF本文と会社IRの一次資料を確認してください。"
)


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class TdnetDisclosure:
    """TDnet配当関連開示の一覧レコード。"""

    disclosure_id: str
    published_date: str
    published_time: str
    security_code: str
    company_name: str
    title: str
    category: str
    is_policy_candidate: bool
    matched_keywords: tuple[str, ...]
    pdf_url: str
    exchange: str


# ============================================================
# 設定・文字列処理
# ============================================================

def get_lookback_days() -> int:
    """TDnet一覧を遡る日数を取得する。"""

    raw_value = os.getenv(
        "TDNET_LOOKBACK_DAYS",
        str(DEFAULT_LOOKBACK_DAYS),
    ).strip()

    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "TDNET_LOOKBACK_DAYSは整数で指定してください。"
            f"指定値: {raw_value}"
        ) from error

    if not 1 <= value <= MAXIMUM_LOOKBACK_DAYS:
        raise RuntimeError(
            "TDNET_LOOKBACK_DAYSは1〜"
            f"{MAXIMUM_LOOKBACK_DAYS}で指定してください。"
            f"指定値: {raw_value}"
        )

    return value


def normalize_text(value: Any) -> str:
    """全半角と連続空白を正規化する。"""

    text = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )
    return re.sub(r"\s+", " ", text).strip()


def normalize_security_code(value: Any) -> str:
    """TDnetの5桁コードを銘柄マスターの4桁コードへ揃える。"""

    code = normalize_text(value).upper()

    if len(code) == 5 and code.endswith("0"):
        code = code[:4]

    if not re.fullmatch(r"[0-9A-Z]{4}", code):
        return ""

    return code


def classify_disclosure_title(
    title: str,
) -> tuple[str, bool, tuple[str, ...]]:
    """表題から配当関連分類と一致キーワードを返す。"""

    normalized_title = normalize_text(title)
    upper_title = normalized_title.upper()
    matched_keywords = tuple(
        keyword
        for keyword in DIVIDEND_KEYWORDS
        if keyword.upper() in upper_title
    )

    if not matched_keywords:
        return "", False, ()

    is_policy_candidate = any(
        keyword.upper() in upper_title
        for keyword in POLICY_KEYWORDS
    )

    if is_policy_candidate:
        category = "配当・株主還元方針"
    elif any(
        keyword in normalized_title
        for keyword in ("減配", "無配")
    ):
        category = "減配・無配"
    elif any(
        keyword in normalized_title
        for keyword in ("増配", "復配", "記念配当")
    ):
        category = "増配・復配"
    elif "配当予想" in normalized_title:
        category = "配当予想"
    else:
        category = "配当・株主還元"

    return (
        category,
        is_policy_candidate,
        matched_keywords,
    )


# ============================================================
# HTTP・TDnet一覧取得
# ============================================================

def create_http_session() -> requests.Session:
    """低頻度アクセス用のHTTPセッションを作成する。"""

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        }
    )
    return session


def fetch_html(
    session: requests.Session,
    url: str,
) -> str:
    """TDnet HTMLをUTF-8で取得する。"""

    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def discover_daily_page_urls(
    session: requests.Session,
    *,
    lookback_days: int,
    today: date | None = None,
) -> list[str]:
    """公開日選択肢から対象期間の日次一覧URLを取得する。"""

    if today is None:
        today = datetime.now(JST).date()

    cutoff_date = today - timedelta(
        days=lookback_days - 1
    )
    index_html = fetch_html(
        session,
        TDNET_INDEX_URL,
    )
    soup = BeautifulSoup(
        index_html,
        "html.parser",
    )
    page_urls: list[str] = []

    for option in soup.select(
        "select#day-selector option[value]"
    ):
        page_name = str(
            option.get("value", "")
        ).strip()
        match = re.fullmatch(
            r"I_list_001_(\d{8})\.html",
            page_name,
        )

        if not match:
            continue

        published_date = datetime.strptime(
            match.group(1),
            "%Y%m%d",
        ).date()

        if cutoff_date <= published_date <= today:
            page_urls.append(
                urljoin(TDNET_BASE_URL, page_name)
            )

    if not page_urls:
        raise RuntimeError(
            "TDnetトップページから対象期間の"
            "日次一覧URLを取得できませんでした。"
        )

    return sorted(set(page_urls))


def discover_paginated_urls(
    html: str,
    first_page_url: str,
) -> list[str]:
    """日次一覧HTMLから同日の全ページURLを取得する。"""

    first_page_name = first_page_url.rsplit("/", 1)[-1]
    date_match = re.fullmatch(
        r"I_list_\d{3}_(\d{8})\.html",
        first_page_name,
    )

    if not date_match:
        raise RuntimeError(
            "TDnet日次一覧URLの形式が不正です。"
            f"URL: {first_page_url}"
        )

    date_text = date_match.group(1)
    page_names = set(
        re.findall(
            rf"I_list_\d{{3}}_{date_text}\.html",
            html,
        )
    )
    page_names.add(first_page_name)

    return [
        urljoin(TDNET_BASE_URL, page_name)
        for page_name in sorted(page_names)
    ]


def parse_tdnet_list_page(
    html: str,
    page_url: str,
    active_security_codes: set[str],
) -> list[TdnetDisclosure]:
    """日次一覧から上場普通株式の配当関連開示を抽出する。"""

    page_name = page_url.rsplit("/", 1)[-1]
    date_match = re.fullmatch(
        r"I_list_\d{3}_(\d{8})\.html",
        page_name,
    )

    if not date_match:
        raise RuntimeError(
            "TDnet日次一覧URLから公開日を取得できません。"
            f"URL: {page_url}"
        )

    published_date = datetime.strptime(
        date_match.group(1),
        "%Y%m%d",
    ).date().isoformat()
    soup = BeautifulSoup(html, "html.parser")
    disclosures: list[TdnetDisclosure] = []

    for table_row in soup.select(
        "table#main-list-table tr"
    ):
        time_cell = table_row.select_one("td.kjTime")
        code_cell = table_row.select_one("td.kjCode")
        name_cell = table_row.select_one("td.kjName")
        title_link = table_row.select_one(
            "td.kjTitle a[href]"
        )
        exchange_cell = table_row.select_one("td.kjPlace")

        if not all(
            [
                time_cell,
                code_cell,
                name_cell,
                title_link,
            ]
        ):
            continue

        security_code = normalize_security_code(
            code_cell.get_text(" ", strip=True)
        )

        if security_code not in active_security_codes:
            continue

        title = normalize_text(
            title_link.get_text(" ", strip=True)
        )
        (
            category,
            is_policy_candidate,
            matched_keywords,
        ) = classify_disclosure_title(title)

        if not category:
            continue

        pdf_url = urljoin(
            page_url,
            str(title_link.get("href", "")).strip(),
        )
        disclosure_id = pdf_url.rsplit("/", 1)[-1]
        disclosure_id = re.sub(
            r"\.pdf(?:\?.*)?$",
            "",
            disclosure_id,
            flags=re.IGNORECASE,
        )

        if not disclosure_id:
            continue

        disclosures.append(
            TdnetDisclosure(
                disclosure_id=disclosure_id,
                published_date=published_date,
                published_time=normalize_text(
                    time_cell.get_text(" ", strip=True)
                ),
                security_code=security_code,
                company_name=normalize_text(
                    name_cell.get_text(" ", strip=True)
                ),
                title=title,
                category=category,
                is_policy_candidate=is_policy_candidate,
                matched_keywords=matched_keywords,
                pdf_url=pdf_url,
                exchange=(
                    normalize_text(
                        exchange_cell.get_text(" ", strip=True)
                    )
                    if exchange_cell
                    else ""
                ),
            )
        )

    return disclosures


def load_active_security_codes() -> set[str]:
    """銘柄マスターから現在有効な証券コードを取得する。"""

    with create_database_connection(
        "tdnet_active_securities"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT security_code
                FROM screener.securities
                WHERE is_active = TRUE
                ORDER BY security_code;
                """
            )
            codes = {
                str(row["security_code"])
                for row in cursor.fetchall()
            }

    if not codes:
        raise RuntimeError(
            "TDnet照合用の有効銘柄がありません。"
        )

    return codes


def fetch_tdnet_dividend_disclosures(
    *,
    lookback_days: int,
) -> list[TdnetDisclosure]:
    """対象期間のTDnet配当関連開示を重複なく取得する。"""

    active_security_codes = load_active_security_codes()
    session = create_http_session()
    first_page_urls = discover_daily_page_urls(
        session,
        lookback_days=lookback_days,
    )
    disclosures_by_id: dict[str, TdnetDisclosure] = {}
    request_count = 1

    for first_page_url in first_page_urls:
        first_html = fetch_html(
            session,
            first_page_url,
        )
        request_count += 1
        page_urls = discover_paginated_urls(
            first_html,
            first_page_url,
        )

        for page_url in page_urls:
            if request_count > MAXIMUM_PAGE_REQUESTS:
                raise RuntimeError(
                    "TDnet一覧のリクエスト数が上限を超えました。"
                )

            if page_url == first_page_url:
                html = first_html
            else:
                time.sleep(REQUEST_INTERVAL_SECONDS)
                html = fetch_html(
                    session,
                    page_url,
                )
                request_count += 1

            for disclosure in parse_tdnet_list_page(
                html,
                page_url,
                active_security_codes,
            ):
                disclosures_by_id[
                    disclosure.disclosure_id
                ] = disclosure

        time.sleep(REQUEST_INTERVAL_SECONDS)

    disclosures = sorted(
        disclosures_by_id.values(),
        key=lambda item: (
            item.published_date,
            item.published_time,
            item.disclosure_id,
        ),
    )

    print(
        "TDnetから配当関連開示を取得しました。"
        f"対象日数: {len(first_page_urls)}, "
        f"件数: {len(disclosures):,}"
    )

    return disclosures

# ============================================================
# PDF本文解析連携
# ============================================================

def parse_disclosure_published_date(
    value: str,
) -> date:
    """TDnet公開日をdate型へ変換する。"""

    normalized_value = normalize_text(value)

    try:
        return date.fromisoformat(
            normalized_value
        )
    except ValueError as error:
        raise RuntimeError(
            "TDnet公開日の形式が不正です。"
            "期待形式: YYYY-MM-DD, "
            f"指定値: {normalized_value}"
        ) from error


def parse_disclosure_published_time(
    value: str,
):
    """TDnet公開時刻をtime型へ変換する。"""

    normalized_value = normalize_text(value)

    if not normalized_value:
        return None

    for time_format in (
        "%H:%M",
        "%H:%M:%S",
    ):
        try:
            return datetime.strptime(
                normalized_value,
                time_format,
            ).time()
        except ValueError:
            continue

    raise RuntimeError(
        "TDnet公開時刻の形式が不正です。"
        "期待形式: HH:MMまたはHH:MM:SS, "
        f"指定値: {normalized_value}"
    )


def disclosure_to_policy_pdf_target(
    disclosure: TdnetDisclosure,
) -> TdnetPolicyPdfTarget:
    """TDnet開示をPDF本文解析対象へ変換する。"""

    if not disclosure.is_policy_candidate:
        raise ValueError(
            "方針候補ではないTDnet開示は"
            "PDF本文解析対象へ変換できません。"
            f"開示ID: {disclosure.disclosure_id}"
        )

    return TdnetPolicyPdfTarget(
        disclosure_id=(
            disclosure.disclosure_id
        ),
        security_code=(
            disclosure.security_code
        ),
        published_date=(
            parse_disclosure_published_date(
                disclosure.published_date
            )
        ),
        published_time=(
            parse_disclosure_published_time(
                disclosure.published_time
            )
        ),
        company_name=(
            disclosure.company_name
        ),
        title=disclosure.title,
        pdf_url=disclosure.pdf_url,
    )


def build_policy_pdf_targets(
    disclosures: list[TdnetDisclosure],
) -> list[TdnetPolicyPdfTarget]:
    """方針候補だけをPDF本文解析対象へ変換する。"""

    targets_by_id: dict[
        str,
        TdnetPolicyPdfTarget,
    ] = {}

    for disclosure in disclosures:
        if not disclosure.is_policy_candidate:
            continue

        target = (
            disclosure_to_policy_pdf_target(
                disclosure
            )
        )

        if (
            target.disclosure_id
            in targets_by_id
        ):
            raise RuntimeError(
                "TDnet PDF本文解析対象の"
                "開示IDが重複しています。"
                f"開示ID: {target.disclosure_id}"
            )

        targets_by_id[
            target.disclosure_id
        ] = target

    targets = sorted(
        targets_by_id.values(),
        key=lambda target: (
            target.published_date,
            (
                target.published_time
                or datetime.min.time()
            ),
            target.disclosure_id,
        ),
    )

    print(
        "TDnet方針候補をPDF本文解析対象へ"
        "変換しました。"
        f"件数: {len(targets):,}"
    )

    return targets


def analyze_fetched_policy_disclosures(
    disclosures: list[TdnetDisclosure],
) -> dict[str, int]:
    """
    今回取得したTDnet開示の方針候補を解析する。

    完了済みの同一開示はDB側の解析対象選択で省略する。
    直近期間に残っている失敗開示は最大3回まで再試行する。
    """

    targets = build_policy_pdf_targets(
        disclosures
    )

    return process_tdnet_policy_pdf_targets(
        targets,
        maximum_attempts=3,
    )

# ============================================================
# Google Sheets履歴
# ============================================================

def disclosure_to_row(
    disclosure: TdnetDisclosure,
) -> list[Any]:
    """TDnet開示を履歴シートの基本12列へ変換する。"""

    return [
        disclosure.disclosure_id,
        disclosure.published_date,
        disclosure.published_time,
        disclosure.security_code,
        disclosure.company_name,
        disclosure.title,
        disclosure.category,
        disclosure.is_policy_candidate,
        ", ".join(disclosure.matched_keywords),
        disclosure.pdf_url,
        disclosure.exchange,
        TDNET_SOURCE_NAME,
    ]


def row_to_disclosure(
    headers: list[str],
    row: list[Any],
) -> TdnetDisclosure:
    """履歴シートの行をTDnet開示へ戻す。"""

    padded_row = list(row) + [""] * max(
        0,
        len(headers) - len(row),
    )
    values = dict(zip(headers, padded_row))
    policy_text = normalize_text(
        values.get("累進配当方針候補", "")
    ).lower()

    return TdnetDisclosure(
        disclosure_id=normalize_text(
            values.get("開示ID", "")
        ),
        published_date=normalize_text(
            values.get("公開日", "")
        ),
        published_time=normalize_text(
            values.get("公開時刻", "")
        ),
        security_code=normalize_security_code(
            values.get("証券コード", "")
        ),
        company_name=normalize_text(
            values.get("会社名", "")
        ),
        title=normalize_text(
            values.get("表題", "")
        ),
        category=normalize_text(
            values.get("分類", "")
        ),
        is_policy_candidate=(
            policy_text
            in {
                "true",
                "1",
                "yes",
                "はい",
            }
        ),
        matched_keywords=tuple(
            keyword.strip()
            for keyword in normalize_text(
                values.get("一致キーワード", "")
            ).split(",")
            if keyword.strip()
        ),
        pdf_url=normalize_text(
            values.get("PDF URL", "")
        ),
        exchange=normalize_text(
            values.get("上場取引所", "")
        ),
    )


def prepare_tdnet_disclosure_sheet(
    sheets_service,
    spreadsheet_id: str,
) -> list[list[Any]]:
    """
    TDnet履歴シートを作成し、現在値を返す。

    従来の基本12列と、本文解析結果を追加した17列の
    どちらも読み込めるようにする。
    """

    get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        TDNET_DISCLOSURE_SHEET_NAME,
    )
    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        TDNET_DISCLOSURE_SHEET_NAME,
    )

    if not values:
        write_sheet(
            sheets_service,
            spreadsheet_id,
            TDNET_DISCLOSURE_SHEET_NAME,
            TDNET_DISCLOSURE_BASE_HEADERS,
            [],
        )
        return [
            TDNET_DISCLOSURE_BASE_HEADERS
        ]

    headers = [
        normalize_text(value)
        for value in values[0]
    ]

    allowed_headers = (
        TDNET_DISCLOSURE_BASE_HEADERS,
        TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
    )

    if headers not in allowed_headers:
        raise RuntimeError(
            "TDnet配当開示シートの列が一致しません。"
            "期待列は従来12列または"
            "本文解析付き17列です。"
            f"実際の列: {headers}"
        )

    if (
        headers
        == TDNET_DISCLOSURE_BASE_HEADERS
    ):
        print(
            "TDnet配当開示シートは従来の"
            "12列形式です。"
            "PDF解析後に17列形式へ移行します。"
        )

    return values


def append_new_disclosures(
    sheets_service,
    spreadsheet_id: str,
    disclosures: list[TdnetDisclosure],
    existing_values: list[list[Any]],
) -> list[TdnetDisclosure]:
    """
    履歴にないTDnet開示だけを基本12列へ追記する。

    本文解析完了後にシート全体を17列形式で
    再同期するため、ここでは基本列だけを追記する。
    """

    existing_ids = {
        normalize_text(row[0])
        for row in existing_values[1:]
        if row and normalize_text(row[0])
    }
    new_disclosures = [
        disclosure
        for disclosure in disclosures
        if (
            disclosure.disclosure_id
            not in existing_ids
        )
    ]

    if not new_disclosures:
        print(
            "新しいTDnet配当関連開示はありません。"
        )
        return []

    rows = [
        disclosure_to_row(disclosure)
        for disclosure in new_disclosures
    ]

    (
        sheets_service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=(
                f"'{TDNET_DISCLOSURE_SHEET_NAME}'"
                "!A:L"
            ),
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        )
        .execute()
    )

    print(
        "TDnet配当関連開示を履歴へ追記しました。"
        f"件数: {len(new_disclosures):,}"
    )

    return new_disclosures

# ============================================================
# 累進配当方針候補
# ============================================================

def build_progressive_policy_rows(
    disclosures: list[TdnetDisclosure],
) -> list[list[Any]]:
    """銘柄ごとの最新方針候補をGoogle Sheets行へ変換する。"""

    latest_by_security: dict[str, TdnetDisclosure] = {}

    for disclosure in disclosures:
        if not disclosure.is_policy_candidate:
            continue

        current = latest_by_security.get(
            disclosure.security_code
        )

        if current is None or (
            disclosure.published_date,
            disclosure.published_time,
            disclosure.disclosure_id,
        ) > (
            current.published_date,
            current.published_time,
            current.disclosure_id,
        ):
            latest_by_security[
                disclosure.security_code
            ] = disclosure

    updated_at = datetime.now(JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    rows: list[list[Any]] = []

    for security_code in sorted(latest_by_security):
        disclosure = latest_by_security[security_code]
        rows.append(
            [
                updated_at,
                disclosure.security_code,
                disclosure.company_name,
                disclosure.published_date,
                disclosure.published_time,
                disclosure.title,
                disclosure.category,
                ", ".join(
                    disclosure.matched_keywords
                ),
                disclosure.pdf_url,
                POLICY_CAUTION,
            ]
        )

    return rows


def load_all_stored_disclosures(
    values: list[list[Any]],
    new_disclosures: list[TdnetDisclosure],
) -> list[TdnetDisclosure]:
    """保存済みと新規の開示を重複なく統合する。"""

    headers = [
        normalize_text(value)
        for value in values[0]
    ]
    disclosures_by_id: dict[str, TdnetDisclosure] = {}

    for row in values[1:]:
        disclosure = row_to_disclosure(
            headers,
            row,
        )

        if disclosure.disclosure_id:
            disclosures_by_id[
                disclosure.disclosure_id
            ] = disclosure

    for disclosure in new_disclosures:
        disclosures_by_id[
            disclosure.disclosure_id
        ] = disclosure

    return list(disclosures_by_id.values())


# ============================================================
# Discord通知
# ============================================================

def notify_new_tdnet_disclosures(
    new_disclosures: list[TdnetDisclosure],
) -> None:
    """新しい配当関連開示だけをDiscordへ通知する。"""

    if not new_disclosures:
        return

    webhook_url = os.getenv(
        "DISCORD_WEBHOOK_URL",
        "",
    ).strip()

    if not webhook_url:
        print(
            "DISCORD_WEBHOOK_URLが未設定のため、"
            "TDnet配当開示の通知を省略します。"
        )
        return

    policy_count = sum(
        disclosure.is_policy_candidate
        for disclosure in new_disclosures
    )
    lines = [
        f"新着件数: **{len(new_disclosures):,}件**",
        f"方針候補: **{policy_count:,}件**",
        "",
        "**新着開示（最大10件）**",
    ]

    for disclosure in reversed(
        new_disclosures[-10:]
    ):
        marker = (
            " [方針候補]"
            if disclosure.is_policy_candidate
            else ""
        )
        lines.append(
            f"- `{disclosure.security_code}` "
            f"{disclosure.company_name}{marker}\n"
            f"  {disclosure.published_date} "
            f"{disclosure.published_time} "
            f"[{disclosure.title}]({disclosure.pdf_url})"
        )

    lines.extend(
        [
            "",
            f"注意: {POLICY_CAUTION}",
        ]
    )

    send_discord_notification(
        webhook_url,
        "TDnet配当関連開示を検出しました",
        "\n".join(lines),
        success=True,
    )


# ============================================================
# 更新処理
# ============================================================

def update_tdnet_dividend_disclosures(
    sheets_service,
    spreadsheet_id: str,
) -> dict[str, int]:
    """
    TDnet履歴、PDF本文解析結果、
    方針候補シート、Discord通知を更新する。
    """

    lookback_days = get_lookback_days()

    # 従来12列と新17列の両方を読み込める。
    existing_values = (
        prepare_tdnet_disclosure_sheet(
            sheets_service,
            spreadsheet_id,
        )
    )

    fetched_disclosures = (
        fetch_tdnet_dividend_disclosures(
            lookback_days=lookback_days,
        )
    )

    # PDF解析前でも新着開示履歴を失わないよう、
    # 基本12列を先に追記する。
    new_disclosures = append_new_disclosures(
        sheets_service,
        spreadsheet_id,
        fetched_disclosures,
        existing_values,
    )

    all_disclosures = (
        load_all_stored_disclosures(
            existing_values,
            new_disclosures,
        )
    )

    # 履歴追記後すぐに通知する。
    # PDF取得失敗などがあっても新着通知を失わない。
    notify_new_tdnet_disclosures(
        new_disclosures
    )

    # 今回の取得範囲に含まれる方針候補を解析する。
    # 完了済みの同一バージョンはDB側で省略される。
    pdf_summary = (
        analyze_fetched_policy_disclosures(
            fetched_disclosures
        )
    )

    # 保存済み履歴に含まれる全方針候補について、
    # PostgreSQLから最新の本文解析結果を読み込む。
    policy_disclosure_ids = [
        disclosure.disclosure_id
        for disclosure in all_disclosures
        if disclosure.is_policy_candidate
    ]
    analysis_results = (
        load_tdnet_policy_pdf_analysis_results(
            policy_disclosure_ids
        )
    )

    # TDnet配当開示は12列から17列へ移行し、
    # 累進配当方針候補は10列から15列へ移行する。
    sheet_summary = (
        sync_tdnet_policy_analysis_sheets(
            sheets_service,
            spreadsheet_id,
            all_disclosures,
            analysis_results,
        )
    )

    result = {
        "fetched_count": len(
            fetched_disclosures
        ),
        "new_count": len(
            new_disclosures
        ),
        "stored_disclosure_count": (
            sheet_summary[
                "disclosure_row_count"
            ]
        ),
        "policy_candidate_count": (
            sheet_summary[
                "policy_row_count"
            ]
        ),
        "sheet_analysis_result_count": (
            sheet_summary[
                "result_count"
            ]
        ),
        "pdf_target_count": pdf_summary[
            "target_count"
        ],
        "pdf_processing_target_count": (
            pdf_summary[
                "processing_target_count"
            ]
        ),
        "pdf_completed_skipped_count": (
            pdf_summary[
                "completed_skipped_count"
            ]
        ),
        "pdf_retry_exhausted_count": (
            pdf_summary[
                "retry_exhausted_count"
            ]
        ),
        "pdf_completed_count": pdf_summary[
            "completed_count"
        ],
        "pdf_confirmed_count": pdf_summary[
            "confirmed_count"
        ],
        "pdf_not_confirmed_count": (
            pdf_summary[
                "not_confirmed_count"
            ]
        ),
        "pdf_manual_review_count": (
            pdf_summary[
                "manual_review_count"
            ]
        ),
        "pdf_fetch_failed_count": (
            pdf_summary[
                "fetch_failed_count"
            ]
        ),
        "pdf_text_extraction_failed_count": (
            pdf_summary[
                "text_extraction_failed_count"
            ]
        ),
        "sheet_confirmed_count": (
            sheet_summary[
                "confirmed_count"
            ]
        ),
        "sheet_not_confirmed_count": (
            sheet_summary[
                "not_confirmed_count"
            ]
        ),
        "sheet_manual_review_count": (
            sheet_summary[
                "manual_review_count"
            ]
        ),
        "sheet_fetch_failed_count": (
            sheet_summary[
                "fetch_failed_count"
            ]
        ),
        "sheet_text_extraction_failed_count": (
            sheet_summary[
                "text_extraction_failed_count"
            ]
        ),
    }

    print(
        "TDnet配当関連開示の更新が完了しました。"
        f"取得: {result['fetched_count']:,}, "
        f"新規: {result['new_count']:,}, "
        "保存済み開示: "
        f"{result['stored_disclosure_count']:,}, "
        "累進配当方針候補: "
        f"{result['policy_candidate_count']:,}, "
        "PDF処理対象: "
        f"{result['pdf_processing_target_count']:,}, "
        "PDF解析完了: "
        f"{result['pdf_completed_count']:,}, "
        "シート解析結果: "
        f"{result['sheet_analysis_result_count']:,}, "
        "本文confirmed: "
        f"{result['sheet_confirmed_count']:,}, "
        "本文manual_review: "
        f"{result['sheet_manual_review_count']:,}, "
        "PDF取得失敗: "
        f"{result['sheet_fetch_failed_count']:,}, "
        "本文抽出失敗: "
        f"{result['sheet_text_extraction_failed_count']:,}"
    )

    return result


def main() -> None:
    """認証情報を取得してTDnet配当開示を更新する。"""

    spreadsheet_id = (
        get_required_environment_variable(
            "GOOGLE_SPREADSHEET_ID"
        )
    )
    service_account_json = (
        get_required_environment_variable(
            "GOOGLE_SERVICE_ACCOUNT_JSON"
        )
    )
    sheets_service = (
        create_google_sheets_service(
            service_account_json
        )
    )

    update_tdnet_dividend_disclosures(
        sheets_service,
        spreadsheet_id,
    )

# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "TDnet配当関連開示の更新中に"
            "エラーが発生しました。",
            file=sys.stderr,
        )
        traceback.print_exc()
        print(
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
