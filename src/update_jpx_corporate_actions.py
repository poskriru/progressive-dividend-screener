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

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


# ============================================================
# 定数
# ============================================================

JPX_SOURCE = "JPX"

FACTOR_QUANTUM = Decimal("0.0000000001")

SECURITY_CODE_PATTERN = re.compile(
    r"^[0-9A-Z]{4}$"
)

RATIO_PATTERN = re.compile(
    r"(?P<before>\d+(?:\.\d+)?)"
    r"\s*:\s*"
    r"(?P<after>\d+(?:\.\d+)?)"
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
