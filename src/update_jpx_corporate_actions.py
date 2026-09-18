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
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from typing import Any

# ============================================================
# 外部ライブラリ
# ============================================================

import pdfplumber

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
