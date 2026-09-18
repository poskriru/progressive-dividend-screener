"""
JPXが無料公開する月次PDFおよび日次Excelから、
株式分割・株式併合等を読み取る。

このファイルでは、JPXの比率表記を既存の
screener.corporate_actionsで使用する調整係数へ変換する。
取得・PDF解析・DB保存処理は後続の修正で追加する。
"""

from __future__ import annotations

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlparse

# ============================================================
# 外部ライブラリ
# ============================================================

import pdfplumber
import requests
from bs4 import BeautifulSoup

# ============================================================
# 定数
# ============================================================

JPX_SOURCE = "JPX"

FACTOR_QUANTUM = Decimal("0.0000000001")

SECURITY_CODE_PATTERN = re.compile(
    r"^[0-9A-Z]{4}$"
)

SECURITY_CODE_IN_CELL_PATTERN = re.compile(
    r"(?<![0-9A-Z])"
    r"(?P<code>(?:[0-9]{4}|[0-9]{3}[A-Z]))"
    r"(?![0-9A-Z])",
    re.IGNORECASE,
)

DATE_IN_CELL_PATTERN = re.compile(
    r"(?P<date>"
    r"20[0-9]{2}"
    r"[./-]"
    r"[0-9]{2}"
    r"[./-]"
    r"[0-9]{2}"
    r")"
)

RATIO_PATTERN = re.compile(
    r"(?P<before>\d+(?:\.\d+)?)"
    r"\s*:\s*"
    r"(?P<after>\d+(?:\.\d+)?)"
)

PDF_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "intersection_tolerance": 5,
    "text_tolerance": 3,
}

JPX_ALLOWED_HOST = "www.jpx.co.jp"

JPX_MONTHLY_PDF_PATH_PREFIX = (
    "/markets/statistics-equities/monthly/"
)

REQUEST_TIMEOUT_SECONDS = 120
MAX_DOWNLOAD_RETRIES = 4
MAX_PDF_CONTENT_BYTES = 25 * 1024 * 1024
MAX_HTML_CONTENT_BYTES = 5 * 1024 * 1024

HTTP_USER_AGENT = (
    "progressive-dividend-screener/"
    "jpx-corporate-actions"
)

JPX_MONTHLY_INDEX_URL = (
    "https://www.jpx.co.jp/"
    "markets/statistics-equities/monthly/index.html"
)

JPX_MONTHLY_PAGE_PATH_PATTERN = re.compile(
    r"^/markets/statistics-equities/monthly/"
    r"(?:index|00-archives-[0-9]{2})\.html$"
)

JPX_MONTHLY_PDF_FILENAME_PATTERN = re.compile(
    r"^(?:17|18)_kenri"
    r"(?P<year>[0-9]{2})"
    r"(?P<month>0[1-9]|1[0-2])"
    r"\.pdf$",
    re.IGNORECASE,
)

# ============================================================
# データ型
# ============================================================

@dataclass(frozen=True)
class JpxCorporateAction:
    """JPX資料から読み取った企業行動。"""

    security_code: str
    effective_date: date
    adjustment_factor: Decimal
    ex_right_type: str
    description: str


@dataclass(frozen=True)
class ParsedJpxPdf:
    """JPX月次PDF全体の解析結果。"""

    content_sha256: str
    page_count: int
    table_count: int
    row_count: int
    actions: tuple[JpxCorporateAction, ...]


@dataclass(frozen=True)
class JpxMonthlyPdfSource:
    """JPX統計月報ページから取得した月次PDF情報。"""

    coverage_month: date
    source_url: str

# ============================================================
# 共通変換
# ============================================================

def normalize_text(value: Any) -> str:
    """改行と連続空白を単一の半角空白へ揃える。"""

    return " ".join(
        str(value or "").replace("\u3000", " ").split()
    )


def normalize_security_code(value: Any) -> str:
    """JPXの銘柄コードを4文字の大文字表記へ揃える。"""

    code = normalize_text(value).upper()

    if code.endswith(".0"):
        code = code[:-2]

    if not SECURITY_CODE_PATTERN.fullmatch(code):
        raise RuntimeError(
            "JPXの銘柄コードを読み取れません。"
            f"値={value}"
        )

    return code


def parse_jpx_date(value: Any, *, field_name: str) -> date:
    """JPXの日付表記をdateへ変換する。"""

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = normalize_text(value)

    for date_format in (
        "%Y.%m.%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(
                text,
                date_format,
            ).date()
        except ValueError:
            continue

    raise RuntimeError(
        f"{field_name}を日付として読み取れません。"
        f"値={text}"
    )


def parse_positive_decimal(
    value: str,
    *,
    field_name: str,
) -> Decimal:
    """有限かつ正のDecimalへ変換する。"""

    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise RuntimeError(
            f"{field_name}が数値ではありません。"
            f"値={value}"
        ) from error

    if not number.is_finite() or number <= 0:
        raise RuntimeError(
            f"{field_name}は有限の正数である必要があります。"
            f"値={value}"
        )

    return number


def quantize_adjustment_factor(
    value: Decimal,
) -> Decimal:
    """DBのnumeric(20, 10)に合わせて小数点以下10桁へ丸める。"""

    return value.quantize(
        FACTOR_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


# ============================================================
# JPX企業行動表記
# ============================================================

def extract_ratio(
    description: str,
) -> tuple[Decimal, Decimal] | None:
    """説明文から「変更前:変更後」の比率を取得する。"""

    match = RATIO_PATTERN.search(description)

    if match is None:
        return None

    before = parse_positive_decimal(
        match.group("before"),
        field_name="比率の変更前",
    )
    after = parse_positive_decimal(
        match.group("after"),
        field_name="比率の変更後",
    )

    return before, after


def parse_action_description(
    value: Any,
) -> tuple[Decimal, str] | None:
    """
    JPXの説明文を調整係数と企業行動種別へ変換する。

    戻り値の種別は既存のExRT表現に合わせる。
    1: 株式分割・株式無償割当て
    2: 株式併合
    3: 自動補正しない株主割当て等

    単純な株式分割・株式併合以外は、誤補正を避けるため
    種別3として保存する。
    """

    description = normalize_text(value)

    if not description:
        return None

    # ETFの受益権分割やREITの投資口分割は、
    # 上場株式の配当補正対象に含めない。
    if (
        "受益権分割" in description
        or "投資口分割" in description
    ):
        return None

    ratio = extract_ratio(description)

    if "株式併合" in description:
        if ratio is None:
            raise RuntimeError(
                "株式併合の比率を読み取れません。"
                f"説明={description}"
            )

        before, after = ratio
        factor = quantize_adjustment_factor(
            before / after
        )
        return factor, "2"

    if (
        "株式分割" in description
        or (
            "分割" in description
            and "受益権" not in description
            and "投資口" not in description
        )
    ):
        if ratio is None:
            raise RuntimeError(
                "株式分割の比率を読み取れません。"
                f"説明={description}"
            )

        before, after = ratio
        factor = quantize_adjustment_factor(
            before / after
        )
        return factor, "1"

    # 株主無償割当ては、割当率が「既存株式:追加株式」で
    # 表記されるため、分割比率と同じ計算をしない。
    if "株主無償割当" in description:
        if ratio is None:
            raise RuntimeError(
                "株主無償割当ての比率を読み取れません。"
                f"説明={description}"
            )

        before, additional = ratio
        factor = quantize_adjustment_factor(
            before / (before + additional)
        )
        return factor, "1"

    # 有償の株主割当てや株式配当は、比率だけでは
    # 適切な調整係数を確定できないため自動補正しない。
    if (
        "株主割当" in description
        or "株式配当" in description
        or "新株予約権" in description
    ):
        return Decimal("1.0000000000"), "3"

    return None


# ============================================================
# JPX月次PDFの行解析
# ============================================================

def find_security_code_in_row(
    row: list[Any],
) -> str | None:
    """PDF表の1行から上場銘柄コードを取得する。"""

    normalized_cells = [
        normalize_text(cell)
        for cell in row
    ]

    # 通常はコード専用セルに4文字だけ入っている。
    for cell in normalized_cells:
        candidate = cell.upper()

        if SECURITY_CODE_PATTERN.fullmatch(candidate):
            return normalize_security_code(candidate)

    # PDFの列結合により市場区分などと同じセルへ入った場合は、
    # 日付セルを除外したうえで独立したコード表記を探す。
    for cell in normalized_cells:
        if DATE_IN_CELL_PATTERN.search(cell):
            continue

        match = SECURITY_CODE_IN_CELL_PATTERN.search(
            cell.upper()
        )

        if match is not None:
            return normalize_security_code(
                match.group("code")
            )

    return None


def find_effective_date_in_row(
    row: list[Any],
) -> date | None:
    """PDF表の1行から最初の権利落ち日を取得する。"""

    for cell in row:
        normalized_cell = normalize_text(cell)
        match = DATE_IN_CELL_PATTERN.search(
            normalized_cell
        )

        if match is not None:
            return parse_jpx_date(
                match.group("date"),
                field_name="権利落ち日",
            )

    return None


def parse_pdf_table_row(
    row: list[Any],
) -> JpxCorporateAction | None:
    """
    pdfplumberが抽出した表の1行を企業行動へ変換する。

    株式分割等を含まないヘッダー行や株主総会基準日行は無視する。
    対象企業行動が見つかったのにコードまたは権利落ち日がない場合は、
    読み飛ばさずエラーにして取得元ファイルをcompleteにしない。
    """

    if not isinstance(row, list):
        raise RuntimeError(
            "JPX PDFの表行がlistではありません。"
        )

    description = normalize_text(
        " ".join(
            normalize_text(cell)
            for cell in row
        )
    )

    parsed_action = parse_action_description(
        description
    )

    if parsed_action is None:
        return None

    security_code = find_security_code_in_row(
        row
    )

    if security_code is None:
        raise RuntimeError(
            "企業行動がある行から銘柄コードを"
            "読み取れません。"
            f"行={description}"
        )

    effective_date = find_effective_date_in_row(
        row
    )

    if effective_date is None:
        raise RuntimeError(
            "企業行動がある行から権利落ち日を"
            "読み取れません。"
            f"行={description}"
        )

    adjustment_factor, ex_right_type = (
        parsed_action
    )

    return JpxCorporateAction(
        security_code=security_code,
        effective_date=effective_date,
        adjustment_factor=adjustment_factor,
        ex_right_type=ex_right_type,
        description=description,
    )



# ============================================================
# JPX月次PDF全体の解析
# ============================================================

def validate_pdf_content(
    content: bytes,
) -> None:
    """ダウンロード内容がPDFバイナリであることを確認する。"""

    if not isinstance(content, bytes):
        raise RuntimeError(
            "JPX PDFの内容がbytesではありません。"
        )

    if not content:
        raise RuntimeError(
            "JPX PDFの内容が空です。"
        )

    if not content.startswith(b"%PDF"):
        raise RuntimeError(
            "JPXから取得した内容がPDFではありません。"
        )


def parse_monthly_pdf(
    content: bytes,
) -> ParsedJpxPdf:
    """
    JPX月次PDFの全ページ・全表を解析する。

    タイトル、ページ数、表数、行数を確認し、表を1件も
    抽出できない場合はcompleteとして扱わない。
    同一銘柄・権利落ち日の内容が矛盾する場合も失敗させる。
    """

    validate_pdf_content(content)

    content_sha256 = hashlib.sha256(
        content
    ).hexdigest()

    page_count = 0
    table_count = 0
    row_count = 0
    extracted_text_parts: list[str] = []

    actions_by_key: dict[
        tuple[str, date],
        JpxCorporateAction,
    ] = {}

    try:
        with pdfplumber.open(
            BytesIO(content)
        ) as pdf:
            page_count = len(pdf.pages)

            if page_count < 1:
                raise RuntimeError(
                    "JPX PDFにページがありません。"
                )

            for page in pdf.pages:
                page_text = normalize_text(
                    page.extract_text() or ""
                )

                if page_text:
                    extracted_text_parts.append(
                        page_text
                    )

                tables = page.extract_tables(
                    PDF_TABLE_SETTINGS
                )

                for table in tables:
                    if not isinstance(table, list):
                        raise RuntimeError(
                            "JPX PDFの表がlistではありません。"
                        )

                    table_count += 1

                    for row in table:
                        row_count += 1
                        action = parse_pdf_table_row(
                            row
                        )

                        if action is None:
                            continue

                        key = (
                            action.security_code,
                            action.effective_date,
                        )

                        existing = actions_by_key.get(
                            key
                        )

                        if existing is not None:
                            same_action = (
                                existing.adjustment_factor
                                == action.adjustment_factor
                                and existing.ex_right_type
                                == action.ex_right_type
                            )

                            if not same_action:
                                raise RuntimeError(
                                    "JPX PDF内の同一銘柄・"
                                    "権利落ち日に矛盾する"
                                    "企業行動があります。"
                                    f"code={action.security_code}, "
                                    f"date={action.effective_date}"
                                )

                            continue

                        actions_by_key[key] = action

    except RuntimeError:
        raise

    except Exception as error:
        raise RuntimeError(
            "JPX月次PDFの解析に失敗しました。"
            f"エラー種別={type(error).__name__}"
        ) from error

    extracted_text = normalize_text(
        " ".join(extracted_text_parts)
    )

    if "新株落・権利落等一覧" not in extracted_text:
        raise RuntimeError(
            "JPX月次PDFのタイトルを確認できません。"
        )

    if table_count < 1:
        raise RuntimeError(
            "JPX月次PDFから表を1件も"
            "抽出できませんでした。"
        )

    if row_count < 1:
        raise RuntimeError(
            "JPX月次PDFから表の行を1件も"
            "抽出できませんでした。"
        )

    actions = tuple(
        sorted(
            actions_by_key.values(),
            key=lambda action: (
                action.effective_date,
                action.security_code,
            ),
        )
    )

    return ParsedJpxPdf(
        content_sha256=content_sha256,
        page_count=page_count,
        table_count=table_count,
        row_count=row_count,
        actions=actions,
    )



# ============================================================
# JPX月次PDFの取得
# ============================================================

def validate_jpx_monthly_pdf_url(
    value: Any,
) -> str:
    """許可されたJPX月次PDFのURLだけを受け付ける。"""

    url = str(value or "").strip()

    if not url:
        raise RuntimeError(
            "JPX月次PDFのURLが空です。"
        )

    if any(
        character.isspace()
        for character in url
    ):
        raise RuntimeError(
            "JPX月次PDFのURLに空白文字を"
            "含めることはできません。"
        )

    parsed = urlparse(url)

    if parsed.scheme.lower() != "https":
        raise RuntimeError(
            "JPX月次PDFのURLはhttpsである"
            "必要があります。"
        )

    if parsed.hostname is None:
        raise RuntimeError(
            "JPX月次PDFのホスト名がありません。"
        )

    if parsed.hostname.lower() != JPX_ALLOWED_HOST:
        raise RuntimeError(
            "許可されていないJPX月次PDFの"
            f"ホストです: {parsed.hostname}"
        )

    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(
            "JPX月次PDFのURLに認証情報を"
            "含めることはできません。"
        )

    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(
            "JPX月次PDFのポート番号が不正です。"
        ) from error

    if port not in (None, 443):
        raise RuntimeError(
            "JPX月次PDFのURLには標準HTTPS"
            "ポートだけを指定できます。"
        )

    if not parsed.path.startswith(
        JPX_MONTHLY_PDF_PATH_PREFIX
    ):
        raise RuntimeError(
            "許可されていないJPX月次PDFの"
            f"パスです: {parsed.path}"
        )

    path_segments = parsed.path.split("/")

    if (
        ".." in path_segments
        or "%2e" in parsed.path.lower()
    ):
        raise RuntimeError(
            "JPX月次PDFのURLに不正な"
            "パス要素があります。"
        )

    if not parsed.path.lower().endswith(".pdf"):
        raise RuntimeError(
            "JPX月次PDFのURLが.pdfで"
            "終わっていません。"
        )

    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "JPX月次PDFのURLにクエリまたは"
            "フラグメントは指定できません。"
        )

    return url


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


def download_monthly_pdf(
    session: requests.Session,
    source_url: str,
) -> bytes:
    """
    JPX公式URLから月次PDFを取得する。

    一時的な通信障害、429、5xxだけを再試行する。
    リダイレクト後のURLもJPX月次PDFの許可範囲内か確認する。
    ファイルはストリーミングで読み込み、上限超過時点で停止する。
    """

    if not isinstance(
        session,
        requests.Session,
    ):
        raise RuntimeError(
            "sessionがrequests.Sessionではありません。"
        )

    validated_url = validate_jpx_monthly_pdf_url(
        source_url
    )

    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_DOWNLOAD_RETRIES + 1,
    ):
        response: requests.Response | None = None

        try:
            response = session.get(
                validated_url,
                headers={
                    "Accept": "application/pdf",
                    "User-Agent": HTTP_USER_AGENT,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
                stream=True,
            )

            response.raise_for_status()

            final_url = validate_jpx_monthly_pdf_url(
                response.url
            )

            if final_url != validated_url:
                validated_url = final_url

            content_length = response.headers.get(
                "Content-Length",
                "",
            ).strip()

            if content_length:
                try:
                    declared_size = int(
                        content_length
                    )
                except ValueError as error:
                    raise RuntimeError(
                        "JPX PDFのContent-Lengthが"
                        "整数ではありません。"
                    ) from error

                if declared_size < 0:
                    raise RuntimeError(
                        "JPX PDFのContent-Lengthが"
                        "負数です。"
                    )

                if declared_size > MAX_PDF_CONTENT_BYTES:
                    raise RuntimeError(
                        "JPX PDFのContent-Lengthが"
                        "上限を超えています。"
                    )

            content_buffer = bytearray()

            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):
                if not chunk:
                    continue

                content_buffer.extend(chunk)

                if (
                    len(content_buffer)
                    > MAX_PDF_CONTENT_BYTES
                ):
                    raise RuntimeError(
                        "JPX PDFの実データサイズが"
                        "上限を超えています。"
                    )

            content = bytes(content_buffer)

            validate_pdf_content(content)

            response.close()
            return content

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
            "JPX月次PDFの取得を再試行します。"
            f"試行={attempt}/{MAX_DOWNLOAD_RETRIES}, "
            f"待機={wait_seconds:.1f}秒"
        )

        time.sleep(wait_seconds)

    raise RuntimeError(
        "JPX月次PDFの取得に失敗しました。"
        f"エラー種別={type(last_error).__name__}, "
        f"エラー={last_error}"
    ) from last_error

# ============================================================
# JPX統計月報ページの解析
# ============================================================

def validate_jpx_monthly_page_url(value: Any) -> str:
    """JPX統計月報またはバックナンバーページのURLを検証する。"""

    if not isinstance(value, str):
        raise RuntimeError(
            "JPX統計月報ページのURLがstrではありません。"
        )

    page_url = value.strip()

    if not page_url:
        raise RuntimeError(
            "JPX統計月報ページのURLが空です。"
        )

    if any(character.isspace() for character in page_url):
        raise RuntimeError(
            "JPX統計月報ページのURLに空白文字があります。"
        )

    parsed = urlparse(page_url)

    if parsed.scheme != "https":
        raise RuntimeError(
            "JPX統計月報ページのURLはHTTPSである必要があります。"
        )

    if parsed.hostname != JPX_ALLOWED_HOST:
        raise RuntimeError(
            "JPX統計月報ページのホストが許可されていません。"
        )

    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(
            "JPX統計月報ページのURLに認証情報を指定できません。"
        )

    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(
            "JPX統計月報ページのポート番号が不正です。"
        ) from error

    if port not in (None, 443):
        raise RuntimeError(
            "JPX統計月報ページのポート番号が許可されていません。"
        )

    normalized_path = parsed.path.lower()

    if ".." in normalized_path or "%2e" in normalized_path:
        raise RuntimeError(
            "JPX統計月報ページのパスが不正です。"
        )

    if JPX_MONTHLY_PAGE_PATH_PATTERN.fullmatch(
        normalized_path
    ) is None:
        raise RuntimeError(
            "JPX統計月報ページのパスが許可されていません。"
        )

    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "JPX統計月報ページのURLにクエリまたは"
            "フラグメントを指定できません。"
        )

    return page_url


def extract_month_from_pdf_url(
    source_url: str,
) -> date | None:
    """月次PDFのファイル名から対象年月を取得する。"""

    parsed = urlparse(source_url)
    filename = parsed.path.rsplit("/", 1)[-1]

    match = JPX_MONTHLY_PDF_FILENAME_PATTERN.fullmatch(
        filename
    )

    if match is None:
        return None

    year = 2000 + int(match.group("year"))
    month = int(match.group("month"))

    return date(year, month, 1)


def parse_monthly_pdf_sources(
    html: str,
    *,
    page_url: str,
) -> tuple[JpxMonthlyPdfSource, ...]:
    """JPX統計月報ページから企業行動PDFのURLを抽出する。"""

    validated_page_url = validate_jpx_monthly_page_url(
        page_url
    )

    if not isinstance(html, str):
        raise RuntimeError(
            "JPX統計月報ページの内容がstrではありません。"
        )

    if not html.strip():
        raise RuntimeError(
            "JPX統計月報ページの内容が空です。"
        )

    soup = BeautifulSoup(html, "html.parser")

    sources_by_month: dict[
        date,
        JpxMonthlyPdfSource,
    ] = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")

        if not isinstance(href, str):
            continue

        href = href.strip()

        if not href:
            continue

        source_url = urljoin(
            validated_page_url,
            href,
        )

        coverage_month = extract_month_from_pdf_url(
            source_url
        )

        if coverage_month is None:
            continue

        validated_source_url = (
            validate_jpx_monthly_pdf_url(source_url)
        )

        source = JpxMonthlyPdfSource(
            coverage_month=coverage_month,
            source_url=validated_source_url,
        )

        existing = sources_by_month.get(coverage_month)

        if existing is not None:
            if existing.source_url != source.source_url:
                raise RuntimeError(
                    "JPX統計月報ページに同一対象年月の"
                    "異なるPDFがあります。"
                    f" month={coverage_month:%Y-%m},"
                    f" first={existing.source_url},"
                    f" second={source.source_url}"
                )

            continue

        sources_by_month[coverage_month] = source

    if not sources_by_month:
        raise RuntimeError(
            "JPX統計月報ページから"
            "新株落・権利落等一覧PDFを取得できませんでした。"
        )

    return tuple(
        sorted(
            sources_by_month.values(),
            key=lambda source: (
                source.coverage_month,
                source.source_url,
            ),
        )
    )

# ============================================================
# JPX統計月報ページの取得
# ============================================================

def validate_html_content(content: bytes) -> str:
    """取得内容がUTF-8のHTML文書であることを確認する。"""

    if not isinstance(content, bytes):
        raise RuntimeError(
            "JPX統計月報ページの内容がbytesではありません。"
        )

    if not content:
        raise RuntimeError(
            "JPX統計月報ページの内容が空です。"
        )

    if len(content) > MAX_HTML_CONTENT_BYTES:
        raise RuntimeError(
            "JPX統計月報ページの実データサイズが"
            "上限を超えています。"
        )

    try:
        html = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RuntimeError(
            "JPX統計月報ページをUTF-8として"
            "読み取れません。"
        ) from error

    normalized_html = html.lstrip().lower()

    if not (
        normalized_html.startswith("<!doctype html")
        or normalized_html.startswith("<html")
    ):
        raise RuntimeError(
            "JPXから取得した内容がHTMLではありません。"
        )

    return html


def download_monthly_page(
    session: requests.Session,
    page_url: str,
) -> str:
    """
    JPX統計月報ページを安全に取得する。

    一時的な通信障害、429、5xxだけを再試行する。
    リダイレクト後のURLも許可範囲内か確認する。
    ページはストリーミングで読み込み、上限超過時点で停止する。
    """

    if not isinstance(session, requests.Session):
        raise RuntimeError(
            "sessionはrequests.Sessionである必要があります。"
        )

    validated_url = validate_jpx_monthly_page_url(
        page_url
    )

    last_error: Exception | None = None

    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        response: requests.Response | None = None
        retryable = False
        wait_seconds = attempt * 5

        try:
            response = session.get(
                validated_url,
                headers={
                    "User-Agent": HTTP_USER_AGENT,
                    "Accept": (
                        "text/html,"
                        "application/xhtml+xml"
                    ),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
                stream=True,
            )

            response.raise_for_status()

            final_url = validate_jpx_monthly_page_url(
                response.url
            )

            if final_url != response.url:
                raise RuntimeError(
                    "JPX統計月報ページの最終URLを"
                    "正規化できません。"
                )

            content_type = response.headers.get(
                "Content-Type",
                "",
            )
            media_type = content_type.split(
                ";",
                1,
            )[0].strip().lower()

            if media_type and media_type not in (
                "text/html",
                "application/xhtml+xml",
            ):
                raise RuntimeError(
                    "JPX統計月報ページのContent-Typeが"
                    "HTMLではありません。"
                    f" Content-Type={content_type}"
                )

            content_length = response.headers.get(
                "Content-Length"
            )

            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as error:
                    raise RuntimeError(
                        "JPX統計月報ページの"
                        "Content-Lengthが不正です。"
                    ) from error

                if declared_size < 0:
                    raise RuntimeError(
                        "JPX統計月報ページの"
                        "Content-Lengthが負数です。"
                    )

                if declared_size > MAX_HTML_CONTENT_BYTES:
                    raise RuntimeError(
                        "JPX統計月報ページの"
                        "Content-Lengthが上限を超えています。"
                    )

            content_buffer = bytearray()

            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):
                if not chunk:
                    continue

                content_buffer.extend(chunk)

                if (
                    len(content_buffer)
                    > MAX_HTML_CONTENT_BYTES
                ):
                    raise RuntimeError(
                        "JPX統計月報ページの"
                        "実データサイズが上限を超えています。"
                    )

            html = validate_html_content(
                bytes(content_buffer)
            )

            response.close()
            return html

        except requests.RequestException as error:
            last_error = error

            status_code = (
                response.status_code
                if response is not None
                else None
            )

            retryable = (
                response is None
                or status_code == 429
                or (
                    status_code is not None
                    and status_code >= 500
                )
            )

            if response is not None:
                wait_seconds = get_retry_wait_seconds(
                    response,
                    attempt,
                )

        except RuntimeError as error:
            last_error = error
            retryable = False

        if response is not None:
            response.close()

        if (
            not retryable
            or attempt == MAX_DOWNLOAD_RETRIES
        ):
            break

        time.sleep(wait_seconds)

    if last_error is None:
        raise RuntimeError(
            "JPX統計月報ページの取得に失敗しました。"
        )

    raise RuntimeError(
        "JPX統計月報ページの取得に失敗しました。"
        f" URL={validated_url},"
        f" エラー種別={type(last_error).__name__},"
        f" エラー={last_error}"
    ) from last_error
