"""
JPXが無料公開する当月の権利処理ページとCSVを取得し、
月次PDFで正式確定する前の企業行動候補を保存する。

当月ページに記載された日付は割当日であり、
月次PDFの権利落ち日とは限らない。

そのため、取得した候補をcorporate_actionsへ直接保存せず、
screener.jpx_current_rights_watchlistへ保存する。
"""

from __future__ import annotations

# ============================================================
# 標準ライブラリ
# ============================================================

import calendar
import csv
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
from io import StringIO
from typing import Any
from urllib.parse import (
    urljoin,
    urlparse,
)

# ============================================================
# 外部ライブラリ
# ============================================================

import requests
from bs4 import BeautifulSoup

# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

# ============================================================
# 定数
# ============================================================

DATABASE_APPLICATION_NAME = (
    "progressive-dividend-jpx-current-rights"
)

JPX_ALLOWED_HOST = "www.jpx.co.jp"

JPX_CURRENT_RIGHTS_PAGE_URL = (
    "https://www.jpx.co.jp/"
    "markets/equities/rights/index.html"
)

JPX_CURRENT_RIGHTS_PAGE_PATH = (
    "/markets/equities/rights/index.html"
)

JPX_CURRENT_RIGHTS_CSV_PATH_PREFIX = (
    "/markets/equities/rights/"
)

HTTP_USER_AGENT = (
    "progressive-dividend-screener/"
    "jpx-current-rights"
)

REQUEST_TIMEOUT_SECONDS = 120
MAX_DOWNLOAD_RETRIES = 4
MAX_HTML_CONTENT_BYTES = 5 * 1024 * 1024
MAX_CSV_CONTENT_BYTES = 5 * 1024 * 1024
MAX_CSV_SOURCE_COUNT = 50
MAX_CSV_RECORD_COUNT = 10_000

FACTOR_QUANTUM = Decimal("0.0000000001")

SECURITY_CODE_PATTERN = re.compile(
    r"^[0-9A-Z]{4}$"
)

RATIO_PATTERN = re.compile(
    r"^\s*"
    r"(?P<before>[0-9]+(?:\.[0-9]+)?)"
    r"\s*:\s*"
    r"(?P<after>[0-9]+(?:\.[0-9]+)?)"
    r"\s*$"
)

CSV_URL_PATTERN = re.compile(
    r"""url\s*:\s*['"](?P<url>[^'"]+\.csv)['"]""",
    re.IGNORECASE,
)

ALLOCATION_HEADING_PATTERN = re.compile(
    r"(?P<year>20[0-9]{2})年"
    r"\s*"
    r"(?P<month>[0-9]{1,2})月"
    r"\s*"
    r"(?P<day>末日|[0-9]{1,2}日)"
    r"\s*割当銘柄"
)

# ============================================================
# データ型
# ============================================================


@dataclass(frozen=True)
class JpxCurrentRightsCsvSource:
    """JPX当月ページから検出したCSV情報。"""

    allocation_date: date
    source_url: str


@dataclass(frozen=True)
class JpxCurrentRight:
    """月次PDF確定前の企業行動候補。"""

    coverage_month: date
    security_code: str
    allocation_date: date
    adjustment_factor: Decimal
    source_url: str


@dataclass(frozen=True)
class ParsedJpxCurrentRights:
    """JPX当月ページと参照CSVの解析結果。"""

    coverage_month: date
    page_url: str
    content_sha256: str
    source_count: int
    records: tuple[JpxCurrentRight, ...]


# ============================================================
# 共通変換
# ============================================================


def normalize_text(value: Any) -> str:
    """改行と連続空白を単一の半角空白へ揃える。"""

    return " ".join(
        str(value or "")
        .replace("\u3000", " ")
        .split()
    )


def normalize_security_code(value: Any) -> str:
    """JPX銘柄コードを4文字の大文字表記へ揃える。"""

    code = normalize_text(value).upper()

    if code.endswith(".0"):
        code = code[:-2]

    if SECURITY_CODE_PATTERN.fullmatch(code) is None:
        raise RuntimeError(
            "JPX当月CSVの銘柄コードを"
            "読み取れません。"
            f" value={value}"
        )

    return code


def parse_positive_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    """有限かつ正のDecimalへ変換する。"""

    normalized_value = normalize_text(value)

    try:
        number = Decimal(normalized_value)
    except InvalidOperation as error:
        raise RuntimeError(
            f"{field_name}が数値ではありません。"
            f" value={normalized_value}"
        ) from error

    if not number.is_finite() or number <= 0:
        raise RuntimeError(
            f"{field_name}は有限の正数である"
            "必要があります。"
            f" value={normalized_value}"
        )

    return number


def parse_split_adjustment_factor(
    value: Any,
) -> Decimal:
    """分割比率の変更前÷変更後を調整係数へ変換する。"""

    ratio_text = normalize_text(value)
    match = RATIO_PATTERN.fullmatch(ratio_text)

    if match is None:
        raise RuntimeError(
            "JPX当月CSVの分割比率を"
            "読み取れません。"
            f" value={ratio_text}"
        )

    before = parse_positive_decimal(
        match.group("before"),
        field_name="分割比率の変更前",
    )
    after = parse_positive_decimal(
        match.group("after"),
        field_name="分割比率の変更後",
    )

    return (before / after).quantize(
        FACTOR_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def get_month_start(value: date) -> date:
    """日付が属する月の月初を返す。"""

    if not isinstance(value, date):
        raise RuntimeError(
            "月初変換対象がdateではありません。"
        )

    return date(
        value.year,
        value.month,
        1,
    )


# ============================================================
# URL検証
# ============================================================


def validate_jpx_current_rights_page_url(
    value: Any,
) -> str:
    """JPX当月権利処理ページのURLを検証する。"""

    url = str(value or "").strip()

    if not url:
        raise RuntimeError(
            "JPX当月権利処理ページのURLが空です。"
        )

    if any(character.isspace() for character in url):
        raise RuntimeError(
            "JPX当月権利処理ページのURLに"
            "空白文字があります。"
        )

    parsed = urlparse(url)

    if parsed.scheme.lower() != "https":
        raise RuntimeError(
            "JPX当月権利処理ページのURLは"
            "HTTPSである必要があります。"
        )

    if parsed.hostname is None:
        raise RuntimeError(
            "JPX当月権利処理ページの"
            "ホスト名がありません。"
        )

    if parsed.hostname.lower() != JPX_ALLOWED_HOST:
        raise RuntimeError(
            "許可されていないJPXホストです。"
            f" host={parsed.hostname}"
        )

    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(
            "JPX URLに認証情報を"
            "含めることはできません。"
        )

    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(
            "JPX URLのポート番号が不正です。"
        ) from error

    if port not in (None, 443):
        raise RuntimeError(
            "JPX URLには標準HTTPSポートだけを"
            "指定できます。"
        )

    if parsed.path != JPX_CURRENT_RIGHTS_PAGE_PATH:
        raise RuntimeError(
            "許可されていないJPX当月ページの"
            f"パスです: {parsed.path}"
        )

    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "JPX当月ページのURLにクエリまたは"
            "フラグメントは指定できません。"
        )

    return url


def validate_jpx_current_rights_csv_url(
    value: Any,
) -> str:
    """JPX当月権利処理CSVのURLを検証する。"""

    url = str(value or "").strip()

    if not url:
        raise RuntimeError(
            "JPX当月CSVのURLが空です。"
        )

    if any(character.isspace() for character in url):
        raise RuntimeError(
            "JPX当月CSVのURLに空白文字があります。"
        )

    parsed = urlparse(url)

    if parsed.scheme.lower() != "https":
        raise RuntimeError(
            "JPX当月CSVのURLはHTTPSである"
            "必要があります。"
        )

    if parsed.hostname is None:
        raise RuntimeError(
            "JPX当月CSVのホスト名がありません。"
        )

    if parsed.hostname.lower() != JPX_ALLOWED_HOST:
        raise RuntimeError(
            "許可されていないJPX当月CSVの"
            f"ホストです: {parsed.hostname}"
        )

    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(
            "JPX当月CSVのURLに認証情報を"
            "含めることはできません。"
        )

    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(
            "JPX当月CSVのポート番号が不正です。"
        ) from error

    if port not in (None, 443):
        raise RuntimeError(
            "JPX当月CSVには標準HTTPSポートだけを"
            "指定できます。"
        )

    if not parsed.path.startswith(
        JPX_CURRENT_RIGHTS_CSV_PATH_PREFIX
    ):
        raise RuntimeError(
            "許可されていないJPX当月CSVの"
            f"パスです: {parsed.path}"
        )

    if (
        ".." in parsed.path.split("/")
        or "%2e" in parsed.path.lower()
    ):
        raise RuntimeError(
            "JPX当月CSVのパスが不正です。"
        )

    if not parsed.path.lower().endswith(".csv"):
        raise RuntimeError(
            "JPX当月CSVのURLが.csvで"
            "終わっていません。"
        )

    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "JPX当月CSVのURLにクエリまたは"
            "フラグメントは指定できません。"
        )

    return url


# ============================================================
# ダウンロード
# ============================================================


def get_retry_wait_seconds(
    response: requests.Response | None,
    attempt: int,
) -> float:
    """Retry-Afterまたは試行回数から待機秒数を決める。"""

    retry_after = ""

    if response is not None:
        retry_after = response.headers.get(
            "Retry-After",
            "",
        )

    try:
        return max(
            float(retry_after),
            float(attempt * 5),
        )
    except ValueError:
        return float(attempt * 5)


def download_content(
    session: requests.Session,
    source_url: str,
    *,
    maximum_bytes: int,
    accept: str,
    url_validator: Any,
) -> tuple[bytes, str]:
    """検証済みJPX URLからサイズ制限付きで取得する。"""

    if not isinstance(session, requests.Session):
        raise RuntimeError(
            "sessionがrequests.Sessionではありません。"
        )

    validated_url = url_validator(source_url)
    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_DOWNLOAD_RETRIES + 1,
    ):
        response: requests.Response | None = None
        retryable = False

        try:
            response = session.get(
                validated_url,
                headers={
                    "Accept": accept,
                    "User-Agent": HTTP_USER_AGENT,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
                stream=True,
            )

            response.raise_for_status()

            final_url = url_validator(response.url)

            content_length = response.headers.get(
                "Content-Length",
                "",
            ).strip()

            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError as error:
                    raise RuntimeError(
                        "JPXレスポンスのContent-Lengthが"
                        "整数ではありません。"
                    ) from error

                if (
                    declared_size < 0
                    or declared_size > maximum_bytes
                ):
                    raise RuntimeError(
                        "JPXレスポンスのContent-Lengthが"
                        "許可範囲外です。"
                    )

            content_buffer = bytearray()

            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):
                if not chunk:
                    continue

                content_buffer.extend(chunk)

                if len(content_buffer) > maximum_bytes:
                    raise RuntimeError(
                        "JPXレスポンスの実データサイズが"
                        "上限を超えています。"
                    )

            content = bytes(content_buffer)

            if not content:
                raise RuntimeError(
                    "JPXから取得した内容が空です。"
                )

            response.close()
            return content, final_url

        except (
            requests.ConnectionError,
            requests.Timeout,
        ) as error:
            last_error = error
            retryable = True

        except requests.HTTPError as error:
            last_error = error
            status_code = (
                error.response.status_code
                if error.response is not None
                else None
            )
            retryable = (
                status_code == 429
                or (
                    status_code is not None
                    and status_code >= 500
                )
            )

        except (
            requests.RequestException,
            RuntimeError,
        ) as error:
            last_error = error
            retryable = False

        if response is not None:
            response.close()

        if (
            not retryable
            or attempt == MAX_DOWNLOAD_RETRIES
        ):
            break

        wait_seconds = get_retry_wait_seconds(
            response,
            attempt,
        )

        print(
            "JPX当月権利処理データの取得を"
            "再試行します。"
            f" 試行={attempt}/{MAX_DOWNLOAD_RETRIES},"
            f" 待機={wait_seconds:.1f}秒",
            flush=True,
        )

        time.sleep(wait_seconds)

    raise RuntimeError(
        "JPX当月権利処理データの取得に"
        "失敗しました。"
        f" URL={validated_url},"
        f" エラー種別={type(last_error).__name__},"
        f" エラー={last_error}"
    ) from last_error


# ============================================================
# HTML解析
# ============================================================


def decode_html_content(content: bytes) -> str:
    """JPXページをUTF-8 HTMLとして読み取る。"""

    if not isinstance(content, bytes) or not content:
        raise RuntimeError(
            "JPX当月ページの内容が空です。"
        )

    try:
        html = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RuntimeError(
            "JPX当月ページをUTF-8として"
            "読み取れません。"
        ) from error

    normalized_html = html.lstrip().lower()

    if not (
        normalized_html.startswith("<!doctype html")
        or normalized_html.startswith("<html")
    ):
        raise RuntimeError(
            "JPX当月ページの内容がHTMLではありません。"
        )

    return html


def parse_allocation_date_from_heading(
    value: Any,
) -> date:
    """見出しから割当日を取得する。"""

    heading = normalize_text(value)
    match = ALLOCATION_HEADING_PATTERN.search(
        heading
    )

    if match is None:
        raise RuntimeError(
            "JPX当月ページの見出しから"
            "割当日を読み取れません。"
            f" heading={heading}"
        )

    year = int(match.group("year"))
    month = int(match.group("month"))
    day_text = match.group("day")

    if day_text == "末日":
        day = calendar.monthrange(
            year,
            month,
        )[1]
    else:
        day = int(day_text[:-1])

    try:
        return date(year, month, day)
    except ValueError as error:
        raise RuntimeError(
            "JPX当月ページの割当日が不正です。"
            f" heading={heading}"
        ) from error


def get_csv_heading_identifier(
    source_url: str,
) -> str:
    """CSV URLの親ディレクトリから見出しIDを取得する。"""

    validated_url = validate_jpx_current_rights_csv_url(
        source_url
    )
    parsed = urlparse(validated_url)
    parent_name = parsed.path.rstrip("/").split("/")[-2]

    if not parent_name.endswith("-att"):
        raise RuntimeError(
            "JPX当月CSVの親ディレクトリ形式が"
            "不正です。"
            f" URL={validated_url}"
        )

    heading_identifier = parent_name[:-4]

    if not re.fullmatch(
        r"[0-9A-Za-z]+",
        heading_identifier,
    ):
        raise RuntimeError(
            "JPX当月CSVの見出しIDが不正です。"
            f" value={heading_identifier}"
        )

    return heading_identifier


def parse_current_rights_csv_sources(
    html: str,
    *,
    page_url: str,
) -> tuple[JpxCurrentRightsCsvSource, ...]:
    """当月ページからCSV URLと割当日を抽出する。"""

    validated_page_url = (
        validate_jpx_current_rights_page_url(
            page_url
        )
    )

    if not isinstance(html, str) or not html.strip():
        raise RuntimeError(
            "JPX当月ページのHTMLが空です。"
        )

    soup = BeautifulSoup(html, "html.parser")
    source_urls: set[str] = set()

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text()

        for match in CSV_URL_PATTERN.finditer(
            script_text
        ):
            source_url = urljoin(
                validated_page_url,
                match.group("url"),
            )
            source_urls.add(
                validate_jpx_current_rights_csv_url(
                    source_url
                )
            )

            if len(source_urls) > MAX_CSV_SOURCE_COUNT:
                raise RuntimeError(
                    "JPX当月CSVの件数が"
                    "安全上限を超えています。"
                )

    if not source_urls:
        raise RuntimeError(
            "JPX当月ページからCSV URLを"
            "取得できませんでした。"
        )

    sources: list[JpxCurrentRightsCsvSource] = []

    for source_url in sorted(source_urls):
        heading_identifier = (
            get_csv_heading_identifier(
                source_url
            )
        )
        heading_element = soup.find(
            id=f"title_{heading_identifier}"
        )

        if heading_element is None:
            raise RuntimeError(
                "JPX当月CSVに対応する見出しが"
                "ありません。"
                f" id=title_{heading_identifier}"
            )

        allocation_date = (
            parse_allocation_date_from_heading(
                heading_element.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        sources.append(
            JpxCurrentRightsCsvSource(
                allocation_date=allocation_date,
                source_url=source_url,
            )
        )

    coverage_months = {
        get_month_start(source.allocation_date)
        for source in sources
    }

    if len(coverage_months) != 1:
        raise RuntimeError(
            "JPX当月ページに複数の対象月が"
            "混在しています。"
            f" months={sorted(coverage_months)}"
        )

    return tuple(
        sorted(
            sources,
            key=lambda source: (
                source.allocation_date,
                source.source_url,
            ),
        )
    )


# ============================================================
# CSV解析
# ============================================================


def decode_csv_content(content: bytes) -> str:
    """JPX CSVをWindows-31Jとして読み取る。"""

    if not isinstance(content, bytes) or not content:
        raise RuntimeError(
            "JPX当月CSVの内容が空です。"
        )

    try:
        return content.decode("cp932")
    except UnicodeDecodeError as error:
        raise RuntimeError(
            "JPX当月CSVをWindows-31Jとして"
            "読み取れません。"
        ) from error


def parse_current_rights_csv(
    content: bytes,
    *,
    source: JpxCurrentRightsCsvSource,
) -> tuple[JpxCurrentRight, ...]:
    """JPX当月CSVを企業行動候補へ変換する。"""

    if not isinstance(
        source,
        JpxCurrentRightsCsvSource,
    ):
        raise RuntimeError(
            "JPX当月CSV情報の型が不正です。"
        )

    source_url = validate_jpx_current_rights_csv_url(
        source.source_url
    )
    csv_text = decode_csv_content(content)
    coverage_month = get_month_start(
        source.allocation_date
    )

    records_by_code: dict[
        str,
        JpxCurrentRight,
    ] = {}

    reader = csv.reader(
        StringIO(csv_text),
    )

    for row_number, row in enumerate(
        reader,
        start=1,
    ):
        normalized_row = [
            normalize_text(cell)
            for cell in row
        ]

        if not any(normalized_row):
            continue

        if len(normalized_row) != 6:
            raise RuntimeError(
                "JPX当月CSVの列数が6ではありません。"
                f" row={row_number},"
                f" columns={len(normalized_row)}"
            )

        security_code = normalize_security_code(
            normalized_row[1]
        )
        adjustment_factor = (
            parse_split_adjustment_factor(
                normalized_row[3]
            )
        )

        record = JpxCurrentRight(
            coverage_month=coverage_month,
            security_code=security_code,
            allocation_date=source.allocation_date,
            adjustment_factor=adjustment_factor,
            source_url=source_url,
        )

        existing = records_by_code.get(
            security_code
        )

        if existing is not None:
            if existing != record:
                raise RuntimeError(
                    "JPX当月CSVに同一銘柄の"
                    "矛盾するデータがあります。"
                    f" code={security_code}"
                )

            continue

        records_by_code[security_code] = record

        if len(records_by_code) > MAX_CSV_RECORD_COUNT:
            raise RuntimeError(
                "JPX当月CSVの企業行動候補件数が"
                "安全上限を超えています。"
            )

    return tuple(
        sorted(
            records_by_code.values(),
            key=lambda record: (
                record.security_code,
                record.allocation_date,
            ),
        )
    )


# ============================================================
# ページ・CSV一括取得
# ============================================================


def download_and_parse_current_rights(
    session: requests.Session,
) -> ParsedJpxCurrentRights:
    """JPX当月ページと全参照CSVを取得・解析する。"""

    page_content, final_page_url = download_content(
        session,
        JPX_CURRENT_RIGHTS_PAGE_URL,
        maximum_bytes=MAX_HTML_CONTENT_BYTES,
        accept=(
            "text/html,"
            "application/xhtml+xml"
        ),
        url_validator=(
            validate_jpx_current_rights_page_url
        ),
    )

    html = decode_html_content(page_content)
    sources = parse_current_rights_csv_sources(
        html,
        page_url=final_page_url,
    )

    coverage_month = get_month_start(
        sources[0].allocation_date
    )

    records_by_key: dict[
        tuple[str, date, str],
        JpxCurrentRight,
    ] = {}

    digest = hashlib.sha256()
    digest.update(page_content)

    for source in sources:
        if (
            get_month_start(source.allocation_date)
            != coverage_month
        ):
            raise RuntimeError(
                "JPX当月CSVの対象月が"
                "一致していません。"
            )

        csv_content, final_csv_url = download_content(
            session,
            source.source_url,
            maximum_bytes=MAX_CSV_CONTENT_BYTES,
            accept="text/csv,text/plain,*/*",
            url_validator=(
                validate_jpx_current_rights_csv_url
            ),
        )

        normalized_source = JpxCurrentRightsCsvSource(
            allocation_date=source.allocation_date,
            source_url=final_csv_url,
        )

        parsed_records = parse_current_rights_csv(
            csv_content,
            source=normalized_source,
        )

        digest.update(b"\x00URL\x00")
        digest.update(
            final_csv_url.encode("utf-8")
        )
        digest.update(b"\x00CONTENT\x00")
        digest.update(csv_content)

        for record in parsed_records:
            key = (
                record.security_code,
                record.allocation_date,
                record.source_url,
            )
            existing = records_by_key.get(key)

            if existing is not None:
                if existing != record:
                    raise RuntimeError(
                        "JPX当月ページに矛盾する"
                        "企業行動候補があります。"
                    )

                continue

            records_by_key[key] = record

            if (
                len(records_by_key)
                > MAX_CSV_RECORD_COUNT
            ):
                raise RuntimeError(
                    "JPX当月ページ全体の候補件数が"
                    "安全上限を超えています。"
                )

    records = tuple(
        sorted(
            records_by_key.values(),
            key=lambda record: (
                record.allocation_date,
                record.security_code,
                record.source_url,
            ),
        )
    )

    return ParsedJpxCurrentRights(
        coverage_month=coverage_month,
        page_url=final_page_url,
        content_sha256=digest.hexdigest(),
        source_count=len(sources),
        records=records,
    )


# ============================================================
# DB保存
# ============================================================


def save_current_rights(
    result: ParsedJpxCurrentRights,
) -> None:
    """当月ページのスナップショットと候補を原子的に保存する。"""

    if not isinstance(
        result,
        ParsedJpxCurrentRights,
    ):
        raise RuntimeError(
            "JPX当月解析結果の型が不正です。"
        )

    if (
        result.coverage_month
        != get_month_start(result.coverage_month)
    ):
        raise RuntimeError(
            "JPX当月解析結果の対象月が"
            "月初ではありません。"
        )

    if result.source_count < 1:
        raise RuntimeError(
            "JPX当月解析結果のCSV数が"
            "1件未満です。"
        )

    if re.fullmatch(
        r"[0-9a-f]{64}",
        result.content_sha256,
    ) is None:
        raise RuntimeError(
            "JPX当月解析結果のSHA-256が不正です。"
        )

    page_url = validate_jpx_current_rights_page_url(
        result.page_url
    )
    fetched_at = datetime.now(timezone.utc)

    watchlist_rows: list[tuple[Any, ...]] = []
    unique_keys: set[tuple[str, date, str]] = set()

    for record in result.records:
        if not isinstance(record, JpxCurrentRight):
            raise RuntimeError(
                "JPX当月候補の型が不正です。"
            )

        if record.coverage_month != result.coverage_month:
            raise RuntimeError(
                "JPX当月候補の対象月が"
                "解析結果と一致しません。"
            )

        if (
            get_month_start(record.allocation_date)
            != result.coverage_month
        ):
            raise RuntimeError(
                "JPX当月候補の割当月が"
                "解析結果と一致しません。"
            )

        security_code = normalize_security_code(
            record.security_code
        )
        source_url = (
            validate_jpx_current_rights_csv_url(
                record.source_url
            )
        )

        if (
            not record.adjustment_factor.is_finite()
            or record.adjustment_factor <= 0
        ):
            raise RuntimeError(
                "JPX当月候補の調整係数が不正です。"
            )

        key = (
            security_code,
            record.allocation_date,
            source_url,
        )

        if key in unique_keys:
            raise RuntimeError(
                "JPX当月候補に重複があります。"
                f" key={key}"
            )

        unique_keys.add(key)

        watchlist_rows.append(
            (
                result.coverage_month,
                security_code,
                record.allocation_date,
                record.adjustment_factor,
                source_url,
                fetched_at,
            )
        )

    with create_database_connection(
        DATABASE_APPLICATION_NAME
    ) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO
                        screener
                            .jpx_current_rights_page_snapshots (
                                coverage_month,
                                page_url,
                                content_sha256,
                                source_count,
                                record_count,
                                fetched_at
                            )
                    VALUES (
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (coverage_month)
                    DO UPDATE SET
                        page_url =
                            EXCLUDED.page_url,
                        content_sha256 =
                            EXCLUDED.content_sha256,
                        source_count =
                            EXCLUDED.source_count,
                        record_count =
                            EXCLUDED.record_count,
                        fetched_at =
                            EXCLUDED.fetched_at
                    """,
                    (
                        result.coverage_month,
                        page_url,
                        result.content_sha256,
                        result.source_count,
                        len(watchlist_rows),
                        fetched_at,
                    ),
                )

                cursor.execute(
                    """
                    DELETE FROM
                        screener
                            .jpx_current_rights_watchlist
                    WHERE coverage_month = %s
                    """,
                    (
                        result.coverage_month,
                    ),
                )

                if watchlist_rows:
                    cursor.executemany(
                        """
                        INSERT INTO
                            screener
                                .jpx_current_rights_watchlist (
                                    coverage_month,
                                    security_code,
                                    allocation_date,
                                    adjustment_factor,
                                    source_url,
                                    fetched_at
                                )
                        VALUES (
                            %s, %s, %s, %s, %s, %s
                        )
                        """,
                        watchlist_rows,
                    )

    print(
        "JPX当月権利処理候補を保存しました。"
        f" 対象月={result.coverage_month:%Y-%m},"
        f" CSV={result.source_count:,}件,"
        f" 候補={len(watchlist_rows):,}件",
        flush=True,
    )


# ============================================================
# 実行
# ============================================================


def create_jpx_http_session() -> requests.Session:
    """JPX取得用のHTTPセッションを作成する。"""

    session = requests.Session()
    session.headers.update({
        "User-Agent": HTTP_USER_AGENT,
    })

    return session


def run_jpx_current_rights_update(
) -> ParsedJpxCurrentRights:
    """JPX当月権利処理ページを更新する。"""

    print(
        "JPX当月権利処理候補の更新を"
        "開始します。",
        flush=True,
    )

    with create_jpx_http_session() as session:
        result = download_and_parse_current_rights(
            session
        )

    save_current_rights(result)

    print(
        "JPX当月権利処理候補の更新が"
        "完了しました。",
        flush=True,
    )

    return result


def main() -> None:
    """JPX当月権利処理更新のエントリーポイント。"""

    try:
        run_jpx_current_rights_update()
    except Exception as error:
        print(
            "JPX当月権利処理候補の更新に"
            "失敗しました。"
            f" エラー種別={type(error).__name__},"
            f" エラー={error}",
            file=sys.stderr,
            flush=True,
        )

        raise SystemExit(1) from error


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    main()
