"""
JPXが無料公開する月次PDFから、
株式分割・株式併合等を取得・解析してPostgreSQLへ保存する。

JPXの比率表記を既存の
screener.corporate_actionsで使用する調整係数へ変換する。

対象期間は環境変数で月単位に指定する。
月ごとに取得・解析・保存し、一部の月が失敗しても
後続月の処理を継続する。
"""

from __future__ import annotations

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
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
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

# ============================================================
# 定数
# ============================================================

JPX_SOURCE = "JPX"

DATABASE_APPLICATION_NAME = (
    "progressive-dividend-jpx-actions"
)

JPX_COVERAGE_START_ENVIRONMENT_VARIABLE = (
    "JPX_COVERAGE_START"
)

JPX_COVERAGE_END_ENVIRONMENT_VARIABLE = (
    "JPX_COVERAGE_END"
)

COVERAGE_MONTH_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})-"
    r"(?P<month>0[1-9]|1[0-2])$"
)

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
MAX_MONTHLY_PAGE_COUNT = 50
MAX_COVERAGE_MONTH_COUNT = 240

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


@dataclass(frozen=True)
class ParsedJpxMonthlySource:
    """取得・解析が完了したJPX月次PDF。"""

    source: JpxMonthlyPdfSource
    content_sha256: str
    page_count: int
    table_count: int
    row_count: int
    actions: tuple[JpxCorporateAction, ...]


@dataclass(frozen=True)
class ParsedJpxCoverage:
    """連続した対象期間のJPX企業行動解析結果。"""

    coverage_start: date
    coverage_end: date
    source_files: tuple[
        ParsedJpxMonthlySource,
        ...,
    ]
    actions: tuple[JpxCorporateAction, ...]

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


# ============================================================
# JPX企業行動表記
# ============================================================

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

    # pdfplumberでは、JPX月次PDFの列見出しが
    # 単独または他の列見出しと結合して抽出される。
    # 実際の企業行動を示す語と比率がない列見出しだけを
    # 株式分割として誤認せず無視する。
    if (
        ratio is None
        and "分割比率" in description
        and "割当率" in description
        and "株式分割" not in description
        and "株式併合" not in description
    ):
        return None

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

    # ========================================================
    # 自動補正しない企業行動
    # ========================================================

    # 「新株予約権の株主無償割当て」は、
    # 通常の株式そのものの無償割当てではない。
    #
    # 「株主無償割当て」という部分文字列にも一致するため、
    # 通常の株主無償割当てより先に判定する。
    #
    # JPX資料に調整比率がない場合でも月全体を失敗させず、
    # 自動補正対象外の企業行動として保存する。
    if "新株予約権" in description:
        return Decimal("1.0000000000"), "3"

    # ========================================================
    # 株式の無償割当て
    # ========================================================

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

    # ========================================================
    # その他の自動補正対象外
    # ========================================================

    # 有償の株主割当てや株式配当は、比率だけでは
    # 適切な調整係数を確定できないため自動補正しない。
    if (
        "株主割当" in description
        or "株式配当" in description
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

# ============================================================
# JPX月次PDFの対象市場
# ============================================================

PDF_MARKET_SCOPE_INCLUDED = "included"
PDF_MARKET_SCOPE_EXCLUDED = "excluded"

PDF_INCLUDED_MARKET_SECTION_MARKERS = (
    "プライム",
    "prime",
    "スタンダード",
    "standard",
    "グロース",
    "growth",
)

PDF_INCLUDED_MARKET_SECTION_LABELS = (
    "プライム",
    "prime",
    "プライムprime",
    "スタンダード",
    "standard",
    "スタンダードstandard",
    "グロース",
    "growth",
    "グロースgrowth",
)

PDF_EXCLUDED_MARKET_SECTION_MARKERS = (
    "tokyopromarket",
    "etf",
    "reit",
    "インフラファンド",
)


def compact_pdf_market_cell(
    value: Any,
) -> str:
    """市場区分判定用にPDFセル内の空白を除去する。"""

    return re.sub(
        r"\s+",
        "",
        normalize_text(value),
    ).casefold()


def classify_pdf_market_section(
    row: list[Any],
) -> str | None:
    """
    PDF表行の全セルから市場・商品区分を判定する。

    pdfplumberでは、市場区分の結合セルが先頭セル以外へ
    抽出されたり、TOKYO、PRO、Marketのように複数セルへ
    分割されたりする場合がある。

    対象外区分は、全セルを空白なしで結合した文字列から
    判定する。

    プライム、スタンダード、グロースは、従来形式との
    後方互換性のため先頭セルの複合表記を許可し、
    先頭セル以外では市場区分ラベルとの完全一致を採用する。
    """

    if not isinstance(row, list):
        raise RuntimeError(
            "JPX PDFの表行がlistではありません。"
        )

    if not row:
        return None

    compact_cells = tuple(
        compact_pdf_market_cell(cell)
        for cell in row
        if normalize_text(cell)
    )

    if not compact_cells:
        return None

    # 市場名が複数セルへ分割されても判定できるよう、
    # 行全体を空白なしで連結する。
    compact_row = "".join(compact_cells)

    if any(
        marker in compact_row
        for marker in PDF_EXCLUDED_MARKET_SECTION_MARKERS
    ):
        return PDF_MARKET_SCOPE_EXCLUDED

    first_cell = compact_pdf_market_cell(row[0])

    # 従来のPDFでは市場区分と銘柄コードが
    # 先頭セルへ結合される場合がある。
    if any(
        marker in first_cell
        for marker in PDF_INCLUDED_MARKET_SECTION_MARKERS
    ):
        return PDF_MARKET_SCOPE_INCLUDED

    # 新しいPDFでは市場区分ラベルが先頭以外の
    # 独立セルへ抽出される場合がある。
    if any(
        compact_cell
        in PDF_INCLUDED_MARKET_SECTION_LABELS
        for compact_cell in compact_cells[1:]
    ):
        return PDF_MARKET_SCOPE_INCLUDED

    return None

def is_foreign_stock_pdf_row(
    row: list[Any],
) -> bool:
    """JPX月次PDFの外国株行かどうかを判定する。"""

    if not isinstance(row, list):
        raise RuntimeError(
            "JPX PDFの表行がlistではありません。"
        )

    description = normalize_text(
        " ".join(
            normalize_text(cell)
            for cell in row
        )
    ).casefold()

    return (
        "外国株" in description
        or "<foreign>" in description
        or "＜foreign＞" in description
    )


def is_explicitly_excluded_pdf_row(
    row: list[Any],
) -> bool:
    """行自体に対象外区分が明記されているか判定する。"""

    market_scope = classify_pdf_market_section(
        row
    )

    return (
        market_scope == PDF_MARKET_SCOPE_EXCLUDED
        or is_foreign_stock_pdf_row(row)
    )


def parse_pdf_table_row(
    row: list[Any],
) -> JpxCorporateAction | None:
    """
    pdfplumberが抽出した表の1行を企業行動へ変換する。

    株式分割等を含まないヘッダー行や株主総会基準日行は無視する。
    TOKYO PRO Market、ETF、REIT、インフラファンドおよび
    外国株は銘柄マスターの対象外であるため解析対象から除外する。
    対象企業行動が見つかったのにコードまたは権利落ち日がない場合は、
    読み飛ばさずエラーにして取得元ファイルをcompleteにしない。
    """

    if not isinstance(row, list):
        raise RuntimeError(
            "JPX PDFの表行がlistではありません。"
        )

    if is_explicitly_excluded_pdf_row(row):
        return None

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

    pdfplumberで結合された市場区分セルが後続行で空欄に
    なることがあるため、直前に確認した市場区分を保持する。
    """

    validate_pdf_content(content)

    content_sha256 = hashlib.sha256(
        content
    ).hexdigest()

    page_count = 0
    table_count = 0
    row_count = 0
    extracted_text_parts: list[str] = []

    current_market_scope: str | None = None

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

                        market_scope = (
                            classify_pdf_market_section(
                                row
                            )
                        )

                        if market_scope is not None:
                            current_market_scope = (
                                market_scope
                            )

                        if (
                            current_market_scope
                            == PDF_MARKET_SCOPE_EXCLUDED
                        ):
                            continue

                        if is_foreign_stock_pdf_row(row):
                            continue

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

# ============================================================
# JPX月次PDF取得対象の統合
# ============================================================

def parse_monthly_page_urls(
    html: str,
    *,
    page_url: str,
) -> tuple[str, ...]:
    """月報トップページからバックナンバーページを抽出する。"""

    validated_page_url = validate_jpx_monthly_page_url(
        page_url
    )

    if not isinstance(html, str):
        raise RuntimeError(
            "JPX月報ページ一覧の内容がstrではありません。"
        )

    if not html.strip():
        raise RuntimeError(
            "JPX月報ページ一覧の内容が空です。"
        )

    soup = BeautifulSoup(html, "html.parser")

    page_urls: set[str] = {
        validated_page_url,
    }
    archive_urls: set[str] = set()

    for element in soup.find_all(
        ["a", "option"]
    ):
        attribute_name = (
            "href"
            if element.name == "a"
            else "value"
        )
        value = element.get(attribute_name)

        if not isinstance(value, str):
            continue

        value = value.strip()

        if not value:
            continue

        candidate_url = urljoin(
            validated_page_url,
            value,
        )
        candidate_path = urlparse(
            candidate_url
        ).path.lower()

        if (
            JPX_MONTHLY_PAGE_PATH_PATTERN.fullmatch(
                candidate_path
            )
            is None
        ):
            continue

        candidate_url = validate_jpx_monthly_page_url(
            candidate_url
        )
        page_urls.add(candidate_url)

        if "00-archives-" in candidate_path:
            archive_urls.add(candidate_url)

        if len(page_urls) > MAX_MONTHLY_PAGE_COUNT:
            raise RuntimeError(
                "JPX月報ページの件数が"
                "安全上限を超えています。"
            )

    if (
        validated_page_url == JPX_MONTHLY_INDEX_URL
        and not archive_urls
    ):
        raise RuntimeError(
            "JPX月報トップページから"
            "バックナンバーページを取得できませんでした。"
        )

    return tuple(sorted(page_urls))


def merge_monthly_pdf_sources(
    source_groups: list[
        tuple[JpxMonthlyPdfSource, ...]
    ],
) -> tuple[JpxMonthlyPdfSource, ...]:
    """複数ページから取得した月次PDF情報を統合する。"""

    sources_by_month: dict[
        date,
        JpxMonthlyPdfSource,
    ] = {}

    for sources in source_groups:
        if not isinstance(sources, tuple):
            raise RuntimeError(
                "JPX月次PDF情報のグループが"
                "tupleではありません。"
            )

        for source in sources:
            if not isinstance(
                source,
                JpxMonthlyPdfSource,
            ):
                raise RuntimeError(
                    "JPX月次PDF情報の型が不正です。"
                )

            existing = sources_by_month.get(
                source.coverage_month
            )

            if existing is not None:
                if (
                    existing.source_url
                    != source.source_url
                ):
                    raise RuntimeError(
                        "複数のJPX月報ページに"
                        "同一対象年月の異なるPDFがあります。"
                        f" month="
                        f"{source.coverage_month:%Y-%m},"
                        f" first={existing.source_url},"
                        f" second={source.source_url}"
                    )

                continue

            sources_by_month[
                source.coverage_month
            ] = source

    if not sources_by_month:
        raise RuntimeError(
            "JPX月報ページ全体から"
            "企業行動PDFを取得できませんでした。"
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


def discover_monthly_pdf_sources(
    session: requests.Session,
) -> tuple[JpxMonthlyPdfSource, ...]:
    """JPX月報とバックナンバーから全PDF情報を取得する。"""

    if not isinstance(session, requests.Session):
        raise RuntimeError(
            "sessionはrequests.Sessionである必要があります。"
        )

    index_html = download_monthly_page(
        session,
        JPX_MONTHLY_INDEX_URL,
    )

    page_urls = parse_monthly_page_urls(
        index_html,
        page_url=JPX_MONTHLY_INDEX_URL,
    )

    source_groups: list[
        tuple[JpxMonthlyPdfSource, ...]
    ] = []

    for page_url in page_urls:
        if page_url == JPX_MONTHLY_INDEX_URL:
            page_html = index_html
        else:
            page_html = download_monthly_page(
                session,
                page_url,
            )

        sources = parse_monthly_pdf_sources(
            page_html,
            page_url=page_url,
        )
        source_groups.append(sources)

    return merge_monthly_pdf_sources(
        source_groups
    )

# ============================================================
# JPX月次PDFの対象期間選択
# ============================================================

def normalize_month(
    value: Any,
    *,
    field_name: str,
) -> date:
    """日付を対象月の月初へ正規化する。"""

    if not isinstance(value, date):
        raise RuntimeError(
            f"{field_name}がdateではありません。"
        )

    return date(
        value.year,
        value.month,
        1,
    )


def next_month(value: date) -> date:
    """翌月の月初を返す。"""

    normalized = normalize_month(
        value,
        field_name="対象月",
    )

    if normalized.month == 12:
        return date(
            normalized.year + 1,
            1,
            1,
        )

    return date(
        normalized.year,
        normalized.month + 1,
        1,
    )


def build_month_range(
    coverage_start: date,
    coverage_end: date,
) -> tuple[date, ...]:
    """開始月から終了月までの月初一覧を生成する。"""

    normalized_start = normalize_month(
        coverage_start,
        field_name="取得開始月",
    )
    normalized_end = normalize_month(
        coverage_end,
        field_name="取得終了月",
    )

    if normalized_start > normalized_end:
        raise RuntimeError(
            "取得開始月が取得終了月より後です。"
            f" start={normalized_start:%Y-%m},"
            f" end={normalized_end:%Y-%m}"
        )

    months: list[date] = []
    current_month = normalized_start

    while current_month <= normalized_end:
        months.append(current_month)

        if len(months) > MAX_COVERAGE_MONTH_COUNT:
            raise RuntimeError(
                "JPX月次PDFの取得対象月数が"
                "安全上限を超えています。"
            )

        current_month = next_month(
            current_month
        )

    return tuple(months)


def select_monthly_pdf_sources(
    sources: tuple[JpxMonthlyPdfSource, ...],
    *,
    coverage_start: date,
    coverage_end: date,
) -> tuple[JpxMonthlyPdfSource, ...]:
    """指定期間に必要な月次PDFだけを欠落なく選択する。"""

    if not isinstance(sources, tuple):
        raise RuntimeError(
            "JPX月次PDF情報がtupleではありません。"
        )

    required_months = build_month_range(
        coverage_start,
        coverage_end,
    )
    required_month_set = set(required_months)

    sources_by_month: dict[
        date,
        JpxMonthlyPdfSource,
    ] = {}

    for source in sources:
        if not isinstance(
            source,
            JpxMonthlyPdfSource,
        ):
            raise RuntimeError(
                "JPX月次PDF情報の型が不正です。"
            )

        normalized_source_month = normalize_month(
            source.coverage_month,
            field_name="PDF対象月",
        )

        if (
            normalized_source_month
            != source.coverage_month
        ):
            raise RuntimeError(
                "JPX月次PDFの対象年月が"
                "月初ではありません。"
                f" value={source.coverage_month}"
            )

        validated_source_url = (
            validate_jpx_monthly_pdf_url(
                source.source_url
            )
        )

        if (
            normalized_source_month
            not in required_month_set
        ):
            continue

        normalized_source = JpxMonthlyPdfSource(
            coverage_month=normalized_source_month,
            source_url=validated_source_url,
        )

        existing = sources_by_month.get(
            normalized_source_month
        )

        if existing is not None:
            if (
                existing.source_url
                != normalized_source.source_url
            ):
                raise RuntimeError(
                    "取得対象期間に同一対象年月の"
                    "異なるJPX月次PDFがあります。"
                    f" month="
                    f"{normalized_source_month:%Y-%m},"
                    f" first={existing.source_url},"
                    f" second="
                    f"{normalized_source.source_url}"
                )

            continue

        sources_by_month[
            normalized_source_month
        ] = normalized_source

    missing_months = tuple(
        month
        for month in required_months
        if month not in sources_by_month
    )

    if missing_months:
        missing_text = ", ".join(
            month.strftime("%Y-%m")
            for month in missing_months
        )

        raise RuntimeError(
            "取得対象期間のJPX月次PDFが不足しています。"
            f" missing={missing_text}"
        )

    return tuple(
        sources_by_month[month]
        for month in required_months
    )

# ============================================================
# JPX月次PDFの一括取得・解析
# ============================================================

def download_and_parse_monthly_sources(
    session: requests.Session,
    sources: tuple[JpxMonthlyPdfSource, ...],
) -> ParsedJpxCoverage:
    """選択済みの月次PDFを取得・解析して企業行動を統合する。"""

    if not isinstance(session, requests.Session):
        raise RuntimeError(
            "sessionはrequests.Sessionである必要があります。"
        )

    if not isinstance(sources, tuple):
        raise RuntimeError(
            "JPX月次PDF情報がtupleではありません。"
        )

    if not sources:
        raise RuntimeError(
            "取得対象のJPX月次PDF情報が空です。"
        )

    sources_by_month: dict[
        date,
        JpxMonthlyPdfSource,
    ] = {}

    for source in sources:
        if not isinstance(
            source,
            JpxMonthlyPdfSource,
        ):
            raise RuntimeError(
                "JPX月次PDF情報の型が不正です。"
            )

        coverage_month = normalize_month(
            source.coverage_month,
            field_name="PDF対象月",
        )

        if coverage_month != source.coverage_month:
            raise RuntimeError(
                "JPX月次PDFの対象年月が"
                "月初ではありません。"
                f" value={source.coverage_month}"
            )

        source_url = validate_jpx_monthly_pdf_url(
            source.source_url
        )
        filename_month = extract_month_from_pdf_url(
            source_url
        )

        if filename_month is None:
            raise RuntimeError(
                "JPX月次PDFのファイル名から"
                "対象年月を取得できません。"
                f" URL={source_url}"
            )

        if filename_month != coverage_month:
            raise RuntimeError(
                "JPX月次PDFの対象年月と"
                "ファイル名の年月が一致しません。"
                f" expected={coverage_month:%Y-%m},"
                f" actual={filename_month:%Y-%m},"
                f" URL={source_url}"
            )

        normalized_source = JpxMonthlyPdfSource(
            coverage_month=coverage_month,
            source_url=source_url,
        )

        existing = sources_by_month.get(
            coverage_month
        )

        if existing is not None:
            if (
                existing.source_url
                != normalized_source.source_url
            ):
                raise RuntimeError(
                    "同一対象年月の異なる"
                    "JPX月次PDFがあります。"
                    f" month={coverage_month:%Y-%m},"
                    f" first={existing.source_url},"
                    f" second="
                    f"{normalized_source.source_url}"
                )

            continue

        sources_by_month[
            coverage_month
        ] = normalized_source

    sorted_months = tuple(
        sorted(sources_by_month)
    )
    coverage_start = sorted_months[0]
    coverage_end = sorted_months[-1]

    required_months = build_month_range(
        coverage_start,
        coverage_end,
    )

    missing_months = tuple(
        month
        for month in required_months
        if month not in sources_by_month
    )

    if missing_months:
        missing_text = ", ".join(
            month.strftime("%Y-%m")
            for month in missing_months
        )

        raise RuntimeError(
            "一括解析するJPX月次PDFに"
            "欠落月があります。"
            f" missing={missing_text}"
        )

    parsed_source_files: list[
        ParsedJpxMonthlySource
    ] = []
    actions_by_key: dict[
        tuple[str, date],
        JpxCorporateAction,
    ] = {}

    for coverage_month in required_months:
        source = sources_by_month[
            coverage_month
        ]

        content = download_monthly_pdf(
            session,
            source.source_url,
        )
        parsed_pdf = parse_monthly_pdf(
            content
        )

        expected_sha256 = hashlib.sha256(
            content
        ).hexdigest()

        if (
            parsed_pdf.content_sha256
            != expected_sha256
        ):
            raise RuntimeError(
                "JPX月次PDFのSHA-256が"
                "解析結果と一致しません。"
                f" month={coverage_month:%Y-%m}"
            )

        for action in parsed_pdf.actions:
            action_month = date(
                action.effective_date.year,
                action.effective_date.month,
                1,
            )

            if action_month != coverage_month:
                raise RuntimeError(
                    "JPX月次PDFに対象年月外の"
                    "企業行動があります。"
                    f" PDF月={coverage_month:%Y-%m},"
                    f" code={action.security_code},"
                    f" effective_date="
                    f"{action.effective_date}"
                )

            key = (
                action.security_code,
                action.effective_date,
            )
            existing_action = actions_by_key.get(
                key
            )

            if existing_action is not None:
                same_action = (
                    existing_action.adjustment_factor
                    == action.adjustment_factor
                    and existing_action.ex_right_type
                    == action.ex_right_type
                )

                if not same_action:
                    raise RuntimeError(
                        "複数のJPX月次PDFに"
                        "矛盾する企業行動があります。"
                        f" code={action.security_code},"
                        f" effective_date="
                        f"{action.effective_date}"
                    )

                continue

            actions_by_key[key] = action

        parsed_source_files.append(
            ParsedJpxMonthlySource(
                source=source,
                content_sha256=(
                    parsed_pdf.content_sha256
                ),
                page_count=parsed_pdf.page_count,
                table_count=parsed_pdf.table_count,
                row_count=parsed_pdf.row_count,
                actions=parsed_pdf.actions,
            )
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

    return ParsedJpxCoverage(
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        source_files=tuple(
            parsed_source_files
        ),
        actions=actions,
    )

# ============================================================
# JPX解析結果のDB保存
# ============================================================

def get_month_end(value: date) -> date:
    """指定月の末日を返す。"""

    month_start = normalize_month(
        value,
        field_name="対象月",
    )

    return next_month(
        month_start
    ) - timedelta(days=1)


def save_failed_monthly_source(
    source: JpxMonthlyPdfSource,
    error: Exception,
) -> None:
    """月次PDFの取得・解析失敗をDBへ保存する。"""

    if not isinstance(
        source,
        JpxMonthlyPdfSource,
    ):
        raise RuntimeError(
            "JPX月次PDF情報の型が不正です。"
        )

    coverage_start = normalize_month(
        source.coverage_month,
        field_name="PDF対象月",
    )

    if coverage_start != source.coverage_month:
        raise RuntimeError(
            "JPX月次PDFの対象年月が"
            "月初ではありません。"
        )

    source_url = validate_jpx_monthly_pdf_url(
        source.source_url
    )
    filename_month = extract_month_from_pdf_url(
        source_url
    )

    if filename_month != coverage_start:
        raise RuntimeError(
            "JPX月次PDFの対象年月と"
            "ファイル名の年月が一致しません。"
        )

    coverage_end = get_month_end(
        coverage_start
    )
    fetched_at = datetime.now(timezone.utc)
    error_text = (
        f"{type(error).__name__}: {error}"
    )[:2000]

    with create_database_connection(
        DATABASE_APPLICATION_NAME
    ) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO
                        screener.jpx_corporate_action_source_files (
                            source_kind,
                            coverage_start,
                            coverage_end,
                            publication_date,
                            source_url,
                            content_sha256,
                            sync_status,
                            record_count,
                            last_error,
                            fetched_at
                        )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (source_url)
                    DO UPDATE SET
                        source_kind =
                            EXCLUDED.source_kind,
                        coverage_start =
                            EXCLUDED.coverage_start,
                        coverage_end =
                            EXCLUDED.coverage_end,
                        publication_date =
                            COALESCE(
                                EXCLUDED.publication_date,
                                screener
                                    .jpx_corporate_action_source_files
                                    .publication_date
                            ),
                        content_sha256 =
                            EXCLUDED.content_sha256,
                        sync_status =
                            EXCLUDED.sync_status,
                        record_count =
                            EXCLUDED.record_count,
                        last_error =
                            EXCLUDED.last_error,
                        fetched_at =
                            EXCLUDED.fetched_at
                    """,
                    (
                        "monthly_pdf",
                        coverage_start,
                        coverage_end,
                        None,
                        source_url,
                        None,
                        "failed",
                        0,
                        error_text,
                        fetched_at,
                    ),
                )


def save_complete_jpx_coverage(
    result: ParsedJpxCoverage,
) -> None:
    """完全解析済みのJPX月次PDFと企業行動を一括保存する。"""

    if not isinstance(
        result,
        ParsedJpxCoverage,
    ):
        raise RuntimeError(
            "JPX解析結果の型が不正です。"
        )

    if not result.source_files:
        raise RuntimeError(
            "JPX解析結果に取得元ファイルがありません。"
        )

    normalized_coverage_start = normalize_month(
        result.coverage_start,
        field_name="保証開始月",
    )
    normalized_coverage_end = normalize_month(
        result.coverage_end,
        field_name="保証終了月",
    )

    if (
        normalized_coverage_start
        != result.coverage_start
        or normalized_coverage_end
        != result.coverage_end
    ):
        raise RuntimeError(
            "JPX解析結果の保証期間が"
            "月初ではありません。"
        )

    required_months = build_month_range(
        result.coverage_start,
        result.coverage_end,
    )

    parsed_sources_by_month: dict[
        date,
        ParsedJpxMonthlySource,
    ] = {}

    for parsed_source in result.source_files:
        if not isinstance(
            parsed_source,
            ParsedJpxMonthlySource,
        ):
            raise RuntimeError(
                "JPX解析済みファイル情報の型が不正です。"
            )

        source = parsed_source.source
        coverage_month = normalize_month(
            source.coverage_month,
            field_name="PDF対象月",
        )

        if coverage_month != source.coverage_month:
            raise RuntimeError(
                "JPX月次PDFの対象年月が"
                "月初ではありません。"
            )

        source_url = validate_jpx_monthly_pdf_url(
            source.source_url
        )
        filename_month = extract_month_from_pdf_url(
            source_url
        )

        if filename_month != coverage_month:
            raise RuntimeError(
                "JPX月次PDFの対象年月と"
                "ファイル名の年月が一致しません。"
            )

        if not re.fullmatch(
            r"[0-9a-f]{64}",
            parsed_source.content_sha256,
        ):
            raise RuntimeError(
                "JPX月次PDFのSHA-256が不正です。"
            )

        if (
            parsed_source.page_count < 1
            or parsed_source.table_count < 1
            or parsed_source.row_count < 1
        ):
            raise RuntimeError(
                "JPX月次PDFの解析件数が不正です。"
            )

        existing = parsed_sources_by_month.get(
            coverage_month
        )

        if existing is not None:
            if existing != parsed_source:
                raise RuntimeError(
                    "同一対象年月に異なる"
                    "JPX解析済みファイルがあります。"
                )

            continue

        parsed_sources_by_month[
            coverage_month
        ] = parsed_source

    missing_months = tuple(
        month
        for month in required_months
        if month not in parsed_sources_by_month
    )

    if missing_months:
        missing_text = ", ".join(
            month.strftime("%Y-%m")
            for month in missing_months
        )

        raise RuntimeError(
            "JPX解析結果に欠落月があります。"
            f" missing={missing_text}"
        )

    unexpected_months = tuple(
        month
        for month in parsed_sources_by_month
        if month not in set(required_months)
    )

    if unexpected_months:
        unexpected_text = ", ".join(
            month.strftime("%Y-%m")
            for month in sorted(
                unexpected_months
            )
        )

        raise RuntimeError(
            "JPX解析結果に保証期間外の月があります。"
            f" unexpected={unexpected_text}"
        )

    source_url_by_month = {
        month: parsed_sources_by_month[
            month
        ].source.source_url
        for month in required_months
    }

    action_rows: list[tuple[Any, ...]] = []
    action_keys: set[tuple[str, date]] = set()

    for action in result.actions:
        if not isinstance(
            action,
            JpxCorporateAction,
        ):
            raise RuntimeError(
                "JPX企業行動の型が不正です。"
            )

        action_month = date(
            action.effective_date.year,
            action.effective_date.month,
            1,
        )

        source_url = source_url_by_month.get(
            action_month
        )

        if source_url is None:
            raise RuntimeError(
                "JPX企業行動に対応する"
                "取得元PDFがありません。"
                f" code={action.security_code},"
                f" date={action.effective_date}"
            )

        action_key = (
            action.security_code,
            action.effective_date,
        )

        if action_key in action_keys:
            raise RuntimeError(
                "JPX解析結果に同一銘柄・日付の"
                "重複企業行動があります。"
                f" code={action.security_code},"
                f" date={action.effective_date}"
            )

        action_keys.add(action_key)
        action_rows.append(
            (
                action.security_code,
                action.effective_date,
                action.adjustment_factor,
                action.ex_right_type,
                JPX_SOURCE,
                source_url,
            )
        )

    fetched_at = datetime.now(timezone.utc)

    source_rows = [
        (
            "monthly_pdf",
            month,
            get_month_end(month),
            None,
            parsed_sources_by_month[
                month
            ].source.source_url,
            parsed_sources_by_month[
                month
            ].content_sha256,
            "complete",
            len(
                parsed_sources_by_month[
                    month
                ].actions
            ),
            None,
            fetched_at,
        )
        for month in required_months
    ]

    with create_database_connection(
        DATABASE_APPLICATION_NAME
    ) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                if action_rows:
                    security_codes = sorted({
                        row[0]
                        for row in action_rows
                    })

                    cursor.execute(
                        """
                        SELECT security_code
                        FROM screener.securities
                        WHERE security_code = ANY(%s)
                        """,
                        (
                            security_codes,
                        ),
                    )

                    existing_security_codes = {
                        str(
                            row["security_code"]
                        ).strip().upper()
                        for row in cursor.fetchall()
                    }

                    missing_security_codes = (
                        set(security_codes)
                        - existing_security_codes
                    )

                    if missing_security_codes:
                        print(
                            "現在の銘柄マスターに存在しない"
                            "過去銘柄を保存対象から除外します。"
                            f" 件数={len(missing_security_codes)},"
                            f" codes="
                            f"{sorted(missing_security_codes)}",
                            flush=True,
                        )

                        action_rows = [
                            action_row
                            for action_row in action_rows
                            if action_row[0]
                            not in missing_security_codes
                        ]

                cursor.executemany(
                    """
                    INSERT INTO
                        screener.jpx_corporate_action_source_files (
                            source_kind,
                            coverage_start,
                            coverage_end,
                            publication_date,
                            source_url,
                            content_sha256,
                            sync_status,
                            record_count,
                            last_error,
                            fetched_at
                        )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (source_url)
                    DO UPDATE SET
                        source_kind =
                            EXCLUDED.source_kind,
                        coverage_start =
                            EXCLUDED.coverage_start,
                        coverage_end =
                            EXCLUDED.coverage_end,
                        publication_date =
                            COALESCE(
                                EXCLUDED.publication_date,
                                screener
                                    .jpx_corporate_action_source_files
                                    .publication_date
                            ),
                        content_sha256 =
                            EXCLUDED.content_sha256,
                        sync_status =
                            EXCLUDED.sync_status,
                        record_count =
                            EXCLUDED.record_count,
                        last_error =
                            EXCLUDED.last_error,
                        fetched_at =
                            EXCLUDED.fetched_at
                    """,
                    source_rows,
                )

                delete_start = result.coverage_start
                delete_end = get_month_end(
                    result.coverage_end
                )

                cursor.execute(
                    """
                    DELETE FROM
                        screener.corporate_actions
                    WHERE source = %s
                      AND effective_date
                            BETWEEN %s AND %s
                    """,
                    (
                        JPX_SOURCE,
                        delete_start,
                        delete_end,
                    ),
                )

                if action_rows:
                    action_rows_with_time = [
                        row + (fetched_at,)
                        for row in action_rows
                    ]

                    cursor.executemany(
                        """
                        INSERT INTO
                            screener.corporate_actions (
                                security_code,
                                effective_date,
                                adjustment_factor,
                                ex_right_type,
                                source,
                                source_url,
                                fetched_at
                            )
                        VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        ON CONFLICT (
                            security_code,
                            effective_date,
                            source
                        )
                        DO UPDATE SET
                            adjustment_factor =
                                EXCLUDED.adjustment_factor,
                            ex_right_type =
                                EXCLUDED.ex_right_type,
                            source_url =
                                EXCLUDED.source_url,
                            fetched_at =
                                EXCLUDED.fetched_at
                        """,
                        action_rows_with_time,
                    )

    print(
        "JPX企業行動を保存しました。"
        f"期間={result.coverage_start:%Y-%m}"
        f"〜{result.coverage_end:%Y-%m}, "
        f"PDF={len(source_rows):,}件, "
        f"企業行動={len(action_rows):,}件"
    )

# ============================================================
# JPX月次企業行動更新の実行
# ============================================================

def get_required_environment_variable(
    name: str,
) -> str:
    """必須環境変数を取得する。"""

    if not isinstance(name, str):
        raise RuntimeError(
            "環境変数名がstrではありません。"
        )

    normalized_name = name.strip()

    if not normalized_name:
        raise RuntimeError(
            "環境変数名が空です。"
        )

    value = os.getenv(
        normalized_name,
        "",
    ).strip()

    if not value:
        raise RuntimeError(
            "必須環境変数が設定されていません。"
            f" name={normalized_name}"
        )

    return value


def parse_coverage_month(
    value: Any,
    *,
    field_name: str,
) -> date:
    """YYYY-MM形式の対象月を月初の日付へ変換する。"""

    if not isinstance(value, str):
        raise RuntimeError(
            f"{field_name}がstrではありません。"
        )

    normalized_value = value.strip()

    match = COVERAGE_MONTH_PATTERN.fullmatch(
        normalized_value
    )

    if match is None:
        raise RuntimeError(
            f"{field_name}はYYYY-MM形式で"
            "指定してください。"
            f" value={normalized_value!r}"
        )

    year = int(
        match.group("year")
    )
    month = int(
        match.group("month")
    )

    try:
        return date(
            year,
            month,
            1,
        )
    except ValueError as error:
        raise RuntimeError(
            f"{field_name}が有効な年月ではありません。"
            f" value={normalized_value!r}"
        ) from error


def get_requested_coverage_period(
) -> tuple[date, date]:
    """環境変数からJPXの取得対象期間を決定する。"""

    coverage_start_text = (
        get_required_environment_variable(
            JPX_COVERAGE_START_ENVIRONMENT_VARIABLE
        )
    )
    coverage_end_text = (
        get_required_environment_variable(
            JPX_COVERAGE_END_ENVIRONMENT_VARIABLE
        )
    )

    coverage_start = parse_coverage_month(
        coverage_start_text,
        field_name="JPX取得開始月",
    )
    coverage_end = parse_coverage_month(
        coverage_end_text,
        field_name="JPX取得終了月",
    )

    # 開始・終了の順序と安全上限を共通処理で検証する。
    build_month_range(
        coverage_start,
        coverage_end,
    )

    return (
        coverage_start,
        coverage_end,
    )


def create_jpx_http_session(
) -> requests.Session:
    """JPX取得用のHTTPセッションを作成する。"""

    session = requests.Session()
    session.headers.update({
        "User-Agent": HTTP_USER_AGENT,
    })

    return session


def run_jpx_monthly_update(
) -> tuple[int, int]:
    """指定期間のJPX月次PDFを月単位で更新する。"""

    (
        coverage_start,
        coverage_end,
    ) = get_requested_coverage_period()

    print(
        "JPX月次企業行動の更新を開始します。"
        f" 期間={coverage_start:%Y-%m}"
        f"〜{coverage_end:%Y-%m}"
    )

    successful_month_count = 0
    failed_month_count = 0

    with create_jpx_http_session() as session:
        discovered_sources = (
            discover_monthly_pdf_sources(
                session
            )
        )

        selected_sources = (
            select_monthly_pdf_sources(
                discovered_sources,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
            )
        )

        for source in selected_sources:
            coverage_month = (
                source.coverage_month
            )

            try:
                parsed_coverage = (
                    download_and_parse_monthly_sources(
                        session,
                        (
                            source,
                        ),
                    )
                )

                save_complete_jpx_coverage(
                    parsed_coverage
                )
            except Exception as error:
                failed_month_count += 1

                print(
                    "JPX月次企業行動の更新に"
                    "失敗しました。"
                    f" 対象月={coverage_month:%Y-%m},"
                    f" エラー種別="
                    f"{type(error).__name__}",
                    file=sys.stderr,
                )

                try:
                    save_failed_monthly_source(
                        source,
                        error,
                    )
                except Exception as save_error:
                    print(
                        "JPX月次PDFの失敗状態を"
                        "保存できませんでした。"
                        f" 対象月="
                        f"{coverage_month:%Y-%m},"
                        f" エラー種別="
                        f"{type(save_error).__name__}",
                        file=sys.stderr,
                    )

                continue

            successful_month_count += 1

            print(
                "JPX月次企業行動の更新が"
                "完了しました。"
                f" 対象月={coverage_month:%Y-%m},"
                f" 企業行動="
                f"{len(parsed_coverage.actions):,}件"
            )

    print(
        "JPX月次企業行動の更新処理が"
        "終了しました。"
        f" 期間={coverage_start:%Y-%m}"
        f"〜{coverage_end:%Y-%m},"
        f" 成功月={successful_month_count:,}件,"
        f" 失敗月={failed_month_count:,}件"
    )

    return (
        successful_month_count,
        failed_month_count,
    )


def main() -> None:
    """JPX月次企業行動更新のエントリーポイント。"""

    try:
        (
            successful_month_count,
            failed_month_count,
        ) = run_jpx_monthly_update()
    except Exception as error:
        print(
            "JPX月次企業行動の更新処理を"
            "開始または完了できませんでした。"
            f" エラー種別={type(error).__name__}",
            file=sys.stderr,
        )

        raise SystemExit(1) from error

    if failed_month_count > 0:
        print(
            "一部の対象月で更新に失敗しました。"
            f" 成功月={successful_month_count:,}件,"
            f" 失敗月={failed_month_count:,}件",
            file=sys.stderr,
        )

        raise SystemExit(1)


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    main()
