"""
PostgreSQLの累進配当指標から投資条件に合う銘柄を抽出し、
Google Sheetsの「累進配当候補」シートへランキング出力する。

抽出条件はGoogle Sheetsまたは環境変数で変更できる。
累進配当候補には、J-Quants補正範囲が完全で、
adjusted判定がTRUEの銘柄だけを採用する。
raw値とraw判定は監査・比較用として保持するが、
補正範囲不足時の候補判定には使用しない。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from export_database_indicators import (
    format_dividend_history,
    to_sheet_boolean,
    to_sheet_integer,
    to_sheet_number,
    yen_to_sheet_million,
)

from sheets_column_formatting import (
    apply_column_formats,
    column_formats_from_headers,
    to_sheet_serial_value,
)

from update_edinet_financials import (
    JST,
    create_google_sheets_service,
    get_or_create_sheet,
    get_required_environment_variable,
    get_spreadsheet_metadata,
    read_sheet,
    send_discord_notification,
    write_sheet,
)

from enrich_tdnet_policy_candidates import (
    enrich_candidates_with_tdnet_policy_results,
)
from load_latest_tdnet_policy_results import (
    load_latest_tdnet_policy_results,
)


# ============================================================
# 定数
# ============================================================

CANDIDATE_SHEET_NAME = "累進配当候補"
CANDIDATE_HISTORY_SHEET_NAME = "累進配当候補_変動履歴"
CANDIDATE_CRITERIA_SHEET_NAME = "累進配当条件"

CANDIDATE_CRITERIA_HEADERS = [
    "設定項目",
    "設定値",
    "説明",
]

CANDIDATE_CRITERIA_DESCRIPTIONS = {
    "CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT": (
        "配当利回りの下限（%）。0以上。"
    ),
    "CANDIDATE_MAX_PAYOUT_RATIO_PERCENT": (
        "配当性向の上限（%）。0より大きい値。"
    ),
    "CANDIDATE_MAX_PER_RATIO": (
        "PERの上限（倍）。0より大きい値。"
    ),
    "CANDIDATE_MAX_PBR_RATIO": (
        "PBRの上限（倍）。0より大きい値。"
    ),
    "CANDIDATE_MIN_ROE_PERCENT": (
        "ROEの下限（%）。"
    ),
    "CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW": (
        "フリーCFをプラス必須にするか。trueまたはfalse。"
    ),
    "CANDIDATE_MAX_ROWS": (
        "候補シートの最大出力件数。1〜5000の整数。"
    ),
}

CANDIDATE_HISTORY_HEADERS = [
    "イベントID",
    "検出日時",
    "変動種別",
    "証券コード",
    "銘柄名",
    "理由",
    "抽出条件",
]

CANDIDATE_HEADERS = [
    "更新日時",
    "順位",
    "株価基準日",
    "証券コード",
    "銘柄名",
    "市場",
    "業種",
    "終値",
    "配当利回り（%）",
    "配当性向（%）",
    "PER（倍）",
    "PBR（倍）",
    "ROE（%）",
    "自己資本比率（%）",
    "フリーCF（百万円）",
    "5期配当CAGR（%）",
    "5期増配回数",
    "5期据え置き回数",
    "連続非減配期数",
    "連続増配期数",
    "最新年間配当（円）",
    "5期最古年間配当（円）",
    "5期配当履歴",
    "EDINET閲覧URL",
    "TDnet方針候補",
    "TDnet最新方針開示日",
    "TDnet最新方針表題",
    "TDnet方針PDF URL",
    "TDnet本文確認状態",
    "TDnet本文判定",
    "TDnet本文一致フレーズ",
    "TDnet本文根拠",
    "TDnet本文根拠ページ",
    "TDnet本文Analyzer",
    "TDnet減配警戒",
    "TDnet最新配当開示日",
    "TDnet最新配当分類",
    "TDnet最新配当表題",
    "TDnet最新配当PDF URL",
    "判定注記",
    "採用判定種別",
    "5期累進配当判定_raw",
    "累進配当判定状態_raw",
    "配当補正状態",
    "補正データ範囲充足",
    "最新累積補正係数",
    "5期最古累積補正係数",
    "5期調整済み配当CAGR（%）",
    "5期調整済み累進配当判定",
    "調整済み累進配当判定状態",
    "5期調整済み配当履歴",
]

CANDIDATE_COLUMN_FORMATS_BY_HEADER: dict[str, str] = {
    "更新日時": "yyyy-mm-dd hh:mm:ss",
    "株価基準日": "yyyy-mm-dd",
    "TDnet最新方針開示日": "yyyy-mm-dd",
    "TDnet最新配当開示日": "yyyy-mm-dd",
    "順位": "#,##0",
    "終値": "0.##",
    "配当利回り（%）": "0.00",
    "配当性向（%）": "0.00",
    "PER（倍）": "0.##",
    "PBR（倍）": "0.##",
    "ROE（%）": "0.00",
    "自己資本比率（%）": "0.00",
    "フリーCF（百万円）": "#,##0.0",
    "5期配当CAGR（%）": "0.00",
    "5期増配回数": "#,##0",
    "5期据え置き回数": "#,##0",
    "連続非減配期数": "#,##0",
    "連続増配期数": "#,##0",
    "最新年間配当（円）": "0.##",
    "5期最古年間配当（円）": "0.##",
    "最新累積補正係数": "0.########",
    "5期最古累積補正係数": "0.########",
    "5期調整済み配当CAGR（%）": "0.00",
}

CANDIDATE_COLUMN_FORMATS = (
    column_formats_from_headers(
        CANDIDATE_HEADERS,
        CANDIDATE_COLUMN_FORMATS_BY_HEADER,
    )
)

DEFAULT_MIN_DIVIDEND_YIELD_PERCENT = Decimal("3.0")
DEFAULT_MAX_PAYOUT_RATIO_PERCENT = Decimal("70.0")
DEFAULT_MAX_PER_RATIO = Decimal("25.0")
DEFAULT_MAX_PBR_RATIO = Decimal("3.0")
DEFAULT_MIN_ROE_PERCENT = Decimal("8.0")
DEFAULT_REQUIRE_POSITIVE_FREE_CASH_FLOW = True
DEFAULT_MAX_CANDIDATES = 300

RAW_DIVIDEND_CAUTION = "株式分割・併合の過年度配当は未調整"
ADJUSTED_DIVIDEND_NOTE = "J-Quants補正範囲充足のため調整済み判定を採用"


# ============================================================
# 抽出条件
# ============================================================

@dataclass(frozen=True)
class CandidateCriteria:
    """累進配当候補の抽出条件。"""

    min_dividend_yield_percent: Decimal
    max_payout_ratio_percent: Decimal
    max_per_ratio: Decimal
    max_pbr_ratio: Decimal
    min_roe_percent: Decimal
    require_positive_free_cash_flow: bool
    max_candidates: int

    @classmethod
    def from_environment(cls) -> "CandidateCriteria":
        """環境変数から抽出条件を読み込み、妥当性を検証する。"""

        return cls(
            min_dividend_yield_percent=get_decimal_environment(
                "CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT",
                DEFAULT_MIN_DIVIDEND_YIELD_PERCENT,
                minimum=Decimal("0"),
            ),
            max_payout_ratio_percent=get_decimal_environment(
                "CANDIDATE_MAX_PAYOUT_RATIO_PERCENT",
                DEFAULT_MAX_PAYOUT_RATIO_PERCENT,
                minimum=Decimal("0"),
                minimum_inclusive=False,
            ),
            max_per_ratio=get_decimal_environment(
                "CANDIDATE_MAX_PER_RATIO",
                DEFAULT_MAX_PER_RATIO,
                minimum=Decimal("0"),
                minimum_inclusive=False,
            ),
            max_pbr_ratio=get_decimal_environment(
                "CANDIDATE_MAX_PBR_RATIO",
                DEFAULT_MAX_PBR_RATIO,
                minimum=Decimal("0"),
                minimum_inclusive=False,
            ),
            min_roe_percent=get_decimal_environment(
                "CANDIDATE_MIN_ROE_PERCENT",
                DEFAULT_MIN_ROE_PERCENT,
            ),
            require_positive_free_cash_flow=get_boolean_environment(
                "CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW",
                DEFAULT_REQUIRE_POSITIVE_FREE_CASH_FLOW,
            ),
            max_candidates=get_integer_environment(
                "CANDIDATE_MAX_ROWS",
                DEFAULT_MAX_CANDIDATES,
                minimum=1,
                maximum=5000,
            ),
        )

    @classmethod
    def from_settings(
        cls,
        settings: dict[str, str],
    ) -> "CandidateCriteria":
        """条件シートの設定値を検証して抽出条件へ変換する。"""

        return cls(
            min_dividend_yield_percent=parse_decimal_setting(
                "CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT",
                settings[
                    "CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT"
                ],
                minimum=Decimal("0"),
            ),
            max_payout_ratio_percent=parse_decimal_setting(
                "CANDIDATE_MAX_PAYOUT_RATIO_PERCENT",
                settings[
                    "CANDIDATE_MAX_PAYOUT_RATIO_PERCENT"
                ],
                minimum=Decimal("0"),
                minimum_inclusive=False,
            ),
            max_per_ratio=parse_decimal_setting(
                "CANDIDATE_MAX_PER_RATIO",
                settings["CANDIDATE_MAX_PER_RATIO"],
                minimum=Decimal("0"),
                minimum_inclusive=False,
            ),
            max_pbr_ratio=parse_decimal_setting(
                "CANDIDATE_MAX_PBR_RATIO",
                settings["CANDIDATE_MAX_PBR_RATIO"],
                minimum=Decimal("0"),
                minimum_inclusive=False,
            ),
            min_roe_percent=parse_decimal_setting(
                "CANDIDATE_MIN_ROE_PERCENT",
                settings["CANDIDATE_MIN_ROE_PERCENT"],
            ),
            require_positive_free_cash_flow=(
                parse_boolean_setting(
                    "CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW",
                    settings[
                        "CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW"
                    ],
                )
            ),
            max_candidates=parse_integer_setting(
                "CANDIDATE_MAX_ROWS",
                settings["CANDIDATE_MAX_ROWS"],
                minimum=1,
                maximum=5000,
            ),
        )

    def to_settings(self) -> dict[str, str]:
        """条件シートへ保存できる文字列設定へ変換する。"""

        return {
            "CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT": str(
                self.min_dividend_yield_percent
            ),
            "CANDIDATE_MAX_PAYOUT_RATIO_PERCENT": str(
                self.max_payout_ratio_percent
            ),
            "CANDIDATE_MAX_PER_RATIO": str(
                self.max_per_ratio
            ),
            "CANDIDATE_MAX_PBR_RATIO": str(
                self.max_pbr_ratio
            ),
            "CANDIDATE_MIN_ROE_PERCENT": str(
                self.min_roe_percent
            ),
            "CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW": (
                "true"
                if self.require_positive_free_cash_flow
                else "false"
            ),
            "CANDIDATE_MAX_ROWS": str(
                self.max_candidates
            ),
        }

    def describe(self) -> str:
        """ログ表示用の抽出条件を返す。"""

        free_cash_flow_condition = (
            "プラス必須"
            if self.require_positive_free_cash_flow
            else "条件なし"
        )

        return (
            "5期累進配当=TRUE, "
            f"配当利回り>={self.min_dividend_yield_percent}%, "
            f"配当性向=0〜{self.max_payout_ratio_percent}%, "
            f"PER=0超〜{self.max_per_ratio}, "
            f"PBR=0超〜{self.max_pbr_ratio}, "
            f"ROE>={self.min_roe_percent}%, "
            f"フリーCF={free_cash_flow_condition}, "
            f"最大{self.max_candidates}件"
        )


@dataclass(frozen=True)
class CandidateChanges:
    """前回出力と今回出力の候補差分。"""

    comparison_id: str
    is_first_export: bool
    added_candidates: tuple[tuple[str, str], ...]
    removed_candidates: tuple[tuple[str, str], ...]

    @property
    def has_changes(self) -> bool:
        """候補の追加または除外がある場合にTRUEを返す。"""

        return bool(
            self.added_candidates
            or self.removed_candidates
        )


def parse_decimal_setting(
    name: str,
    raw_value: Any,
    *,
    minimum: Decimal | None = None,
    minimum_inclusive: bool = True,
) -> Decimal:
    """条件シートの有限なDecimal設定を検証する。"""

    value_text = str(raw_value).strip()

    try:
        value = Decimal(value_text)
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(
            f"条件{name}は数値で指定してください。"
            f"指定値: {value_text}"
        ) from error

    if not value.is_finite():
        raise RuntimeError(
            f"条件{name}には有限値を指定してください。"
            f"指定値: {value_text}"
        )

    if minimum is not None:
        below_minimum = value < minimum
        equal_to_exclusive_minimum = (
            not minimum_inclusive and value == minimum
        )

        if below_minimum or equal_to_exclusive_minimum:
            operator = ">=" if minimum_inclusive else ">"
            raise RuntimeError(
                f"条件{name}は{operator}{minimum}で指定してください。"
                f"指定値: {value_text}"
            )

    return value


def parse_boolean_setting(
    name: str,
    raw_value: Any,
) -> bool:
    """条件シートのboolean設定を検証する。"""

    value_text = str(raw_value).strip().lower()

    if value_text in {"1", "true", "yes", "on", "はい"}:
        return True

    if value_text in {"0", "false", "no", "off", "いいえ"}:
        return False

    raise RuntimeError(
        f"条件{name}はtrueまたはfalseで指定してください。"
        f"指定値: {raw_value}"
    )


def parse_integer_setting(
    name: str,
    raw_value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """条件シートの範囲制限付き整数を検証する。"""

    value_text = str(raw_value).strip()

    try:
        value = int(value_text)
    except ValueError as error:
        raise RuntimeError(
            f"条件{name}は整数で指定してください。"
            f"指定値: {value_text}"
        ) from error

    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"条件{name}は{minimum}〜{maximum}で指定してください。"
            f"指定値: {value_text}"
        )

    return value


def get_decimal_environment(
    name: str,
    default: Decimal,
    *,
    minimum: Decimal | None = None,
    minimum_inclusive: bool = True,
) -> Decimal:
    """有限なDecimal型の環境変数を取得する。"""

    raw_value = os.getenv(name, str(default)).strip()

    try:
        value = Decimal(raw_value)
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(
            f"環境変数{name}は数値で指定してください。指定値: {raw_value}"
        ) from error

    if not value.is_finite():
        raise RuntimeError(
            f"環境変数{name}には有限値を指定してください。指定値: {raw_value}"
        )

    if minimum is not None:
        below_minimum = value < minimum
        equal_to_exclusive_minimum = (
            not minimum_inclusive and value == minimum
        )

        if below_minimum or equal_to_exclusive_minimum:
            operator = ">=" if minimum_inclusive else ">"
            raise RuntimeError(
                f"環境変数{name}は{operator}{minimum}で指定してください。"
                f"指定値: {raw_value}"
            )

    return value


def get_boolean_environment(name: str, default: bool) -> bool:
    """true/false形式の環境変数を取得する。"""

    raw_value = os.getenv(name)

    if raw_value is None or not raw_value.strip():
        return default

    normalized_value = raw_value.strip().lower()

    if normalized_value in {"1", "true", "yes", "on"}:
        return True

    if normalized_value in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(
        f"環境変数{name}はtrueまたはfalseで指定してください。"
        f"指定値: {raw_value}"
    )


def get_integer_environment(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """範囲制限付き整数の環境変数を取得する。"""

    raw_value = os.getenv(name, str(default)).strip()

    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            f"環境変数{name}は整数で指定してください。指定値: {raw_value}"
        ) from error

    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"環境変数{name}は{minimum}〜{maximum}で指定してください。"
            f"指定値: {raw_value}"
        )

    return value


# ============================================================
# Google Sheetsから抽出条件を取得
# ============================================================

def build_candidate_criteria_rows(
    criteria: CandidateCriteria,
) -> list[list[str]]:
    """抽出条件を条件シートの行へ変換する。"""

    settings = criteria.to_settings()

    return [
        [
            name,
            settings[name],
            description,
        ]
        for name, description
        in CANDIDATE_CRITERIA_DESCRIPTIONS.items()
    ]


def load_candidate_criteria_from_sheet(
    sheets_service,
    spreadsheet_id: str,
) -> CandidateCriteria:
    """条件シートを作成または読込し、検証済み条件を返す。"""

    metadata = get_spreadsheet_metadata(
        sheets_service,
        spreadsheet_id,
    )
    sheet_exists = any(
        sheet.get("properties", {}).get("title")
        == CANDIDATE_CRITERIA_SHEET_NAME
        for sheet in metadata.get("sheets", [])
    )

    if not sheet_exists:
        environment_criteria = (
            CandidateCriteria.from_environment()
        )
        write_sheet(
            sheets_service,
            spreadsheet_id,
            CANDIDATE_CRITERIA_SHEET_NAME,
            CANDIDATE_CRITERIA_HEADERS,
            build_candidate_criteria_rows(
                environment_criteria
            ),
        )
        print(
            "累進配当条件シートを初期値で作成しました。"
        )
        return environment_criteria

    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_CRITERIA_SHEET_NAME,
    )

    if not values:
        environment_criteria = (
            CandidateCriteria.from_environment()
        )
        write_sheet(
            sheets_service,
            spreadsheet_id,
            CANDIDATE_CRITERIA_SHEET_NAME,
            CANDIDATE_CRITERIA_HEADERS,
            build_candidate_criteria_rows(
                environment_criteria
            ),
        )
        print(
            "空の累進配当条件シートを初期値で更新しました。"
        )
        return environment_criteria

    headers = [
        str(value).strip()
        for value in values[0]
    ]

    if headers != CANDIDATE_CRITERIA_HEADERS:
        raise RuntimeError(
            "累進配当条件シートの列が一致しません。"
            f"期待列: {CANDIDATE_CRITERIA_HEADERS}, "
            f"実際の列: {headers}"
        )

    settings: dict[str, str] = {}
    allowed_names = set(
        CANDIDATE_CRITERIA_DESCRIPTIONS
    )

    for row_number, row in enumerate(
        values[1:],
        start=2,
    ):
        if not row or not str(row[0]).strip():
            continue

        name = str(row[0]).strip()

        if name not in allowed_names:
            raise RuntimeError(
                "累進配当条件シートに不明な設定があります。"
                f"行: {row_number}, 設定項目: {name}"
            )

        if name in settings:
            raise RuntimeError(
                "累進配当条件シートに設定の重複があります。"
                f"行: {row_number}, 設定項目: {name}"
            )

        value = (
            str(row[1]).strip()
            if len(row) > 1
            else ""
        )

        if not value:
            raise RuntimeError(
                "累進配当条件シートの設定値が空です。"
                f"行: {row_number}, 設定項目: {name}"
            )

        settings[name] = value

    missing_names = allowed_names - set(settings)

    if missing_names:
        raise RuntimeError(
            "累進配当条件シートに必要な設定がありません。"
            f"不足設定: {sorted(missing_names)}"
        )

    criteria = CandidateCriteria.from_settings(
        settings
    )

    print(
        "累進配当条件シートから抽出条件を読み込みました。"
    )

    return criteria


# ============================================================
# PostgreSQLから候補を取得
# ============================================================

def load_progressive_dividend_candidates(
    criteria: CandidateCriteria,
) -> list[dict[str, Any]]:
    """抽出条件に合う累進配当候補をランキング順で取得する。"""

    query = """
        SELECT
            security_code,
            company_name,
            market,
            industry_33_name,
            trading_date,
            close_price,
            dividend_yield_percent,
            payout_ratio_percent,
            per_ratio,
            pbr_ratio,
            roe_percent,
            equity_ratio_percent,
            free_cash_flow_jpy,
            dividend_cagr_5y_percent,
            dividend_increase_count_5y,
            dividend_unchanged_count_5y,
            consecutive_non_decrease_periods,
            consecutive_increase_periods,
            dividend_latest_annual_dividend_yen,
            oldest_annual_dividend_yen_5y,
            fiscal_periods_5y,
            annual_dividends_yen_5y,
            is_progressive_dividend_5y_raw,
            progressive_dividend_status_5y,
            dividend_adjustment_status,
            is_adjustment_coverage_complete,
            latest_cumulative_adjustment_factor,
            oldest_cumulative_adjustment_factor_5y,
            dividend_cagr_5y_adjusted_percent,
            is_progressive_dividend_5y_adjusted,
            progressive_dividend_status_5y_adjusted,
            adjusted_fiscal_periods_5y,
            adjusted_annual_dividends_yen_5y,
            financial_source_url
        FROM screener.company_screener_with_dividends
        WHERE annual_financial_id IS NOT NULL
          AND close_price IS NOT NULL
          AND is_adjustment_coverage_complete IS TRUE
          AND is_progressive_dividend_5y_adjusted IS TRUE
          AND dividend_yield_percent >= %s
          AND payout_ratio_percent BETWEEN 0 AND %s
          AND per_ratio > 0
          AND per_ratio <= %s
          AND pbr_ratio > 0
          AND pbr_ratio <= %s
          AND roe_percent >= %s
          AND (%s = FALSE OR free_cash_flow_jpy > 0)
        ORDER BY
            dividend_yield_percent DESC NULLS LAST,
            CASE
                WHEN is_adjustment_coverage_complete IS TRUE
                THEN dividend_cagr_5y_adjusted_percent
                ELSE dividend_cagr_5y_percent
            END DESC NULLS LAST,
            roe_percent DESC NULLS LAST,
            security_code
        LIMIT %s;
    """

    parameters = (
        criteria.min_dividend_yield_percent,
        criteria.max_payout_ratio_percent,
        criteria.max_per_ratio,
        criteria.max_pbr_ratio,
        criteria.min_roe_percent,
        criteria.require_positive_free_cash_flow,
        criteria.max_candidates,
    )

    with create_database_connection(
        "export_progressive_dividend_candidates"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, parameters)
            records = [dict(row) for row in cursor.fetchall()]

    security_codes = [
        str(record["security_code"])
        for record in records
    ]

    if len(security_codes) != len(set(security_codes)):
        raise RuntimeError(
            "累進配当候補に証券コードの重複があります。"
        )

    print(
        "PostgreSQLから累進配当候補を取得しました。"
        f"件数: {len(records):,}"
    )

    return records


# ============================================================
# Google Sheets出力行作成
# ============================================================

def build_candidate_rows(
    records: list[dict[str, Any]],
) -> list[list[Any]]:
    """候補レコードをGoogle Sheetsの列順へ変換する。"""

    updated_at = to_sheet_serial_value(
        datetime.now(JST)
    )
    rows: list[list[Any]] = []

    for rank, record in enumerate(
        records,
        start=1,
    ):
        row = [
            updated_at,
            rank,
            to_sheet_serial_value(
                record.get("trading_date")
            ),
            str(
                record.get("security_code", "")
            ),
            str(
                record.get("company_name", "")
                or ""
            ),
            str(
                record.get("market", "")
                or ""
            ),
            str(
                record.get("industry_33_name", "")
                or ""
            ),
            to_sheet_number(
                record.get("close_price")
            ),
            to_sheet_number(
                record.get(
                    "dividend_yield_percent"
                )
            ),
            to_sheet_number(
                record.get(
                    "payout_ratio_percent"
                )
            ),
            to_sheet_number(
                record.get("per_ratio")
            ),
            to_sheet_number(
                record.get("pbr_ratio")
            ),
            to_sheet_number(
                record.get("roe_percent")
            ),
            to_sheet_number(
                record.get(
                    "equity_ratio_percent"
                )
            ),
            yen_to_sheet_million(
                record.get(
                    "free_cash_flow_jpy"
                )
            ),
            to_sheet_number(
                record.get(
                    "dividend_cagr_5y_percent"
                )
            ),
            to_sheet_integer(
                record.get(
                    "dividend_increase_count_5y"
                )
            ),
            to_sheet_integer(
                record.get(
                    "dividend_unchanged_count_5y"
                )
            ),
            to_sheet_integer(
                record.get(
                    "consecutive_non_decrease_periods"
                )
            ),
            to_sheet_integer(
                record.get(
                    "consecutive_increase_periods"
                )
            ),
            to_sheet_number(
                record.get(
                    "dividend_latest_annual_dividend_yen"
                )
            ),
            to_sheet_number(
                record.get(
                    "oldest_annual_dividend_yen_5y"
                )
            ),
            format_dividend_history(
                record.get("fiscal_periods_5y"),
                record.get(
                    "annual_dividends_yen_5y"
                ),
            ),
            str(
                record.get(
                    "financial_source_url",
                    "",
                )
                or ""
            ),
            to_sheet_boolean(
                record.get(
                    "tdnet_policy_candidate"
                )
            ),
            str(
                record.get(
                    "tdnet_policy_date",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_policy_title",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_policy_url",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_policy_analysis_status",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_policy_classification",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_policy_matched_phrase",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_policy_evidence_text",
                    "",
                )
                or ""
            ),
            to_sheet_integer(
                record.get(
                    "tdnet_policy_evidence_page_number"
                )
            ),
            str(
                record.get(
                    "tdnet_policy_analyzer_version",
                    "",
                )
                or ""
            ),
            to_sheet_boolean(
                record.get(
                    "tdnet_dividend_warning"
                )
            ),
            str(
                record.get(
                    "tdnet_dividend_date",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_dividend_category",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_dividend_title",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "tdnet_dividend_url",
                    "",
                )
                or ""
            ),
            (
                ADJUSTED_DIVIDEND_NOTE
                if record.get(
                    "is_adjustment_coverage_complete"
                ) is True
                else RAW_DIVIDEND_CAUTION
            ),
            (
                "adjusted"
                if record.get(
                    "is_adjustment_coverage_complete"
                ) is True
                else "raw"
            ),
            to_sheet_boolean(
                record.get(
                    "is_progressive_dividend_5y_raw"
                )
            ),
            str(
                record.get(
                    "progressive_dividend_status_5y",
                    "",
                )
                or ""
            ),
            str(
                record.get(
                    "dividend_adjustment_status",
                    "",
                )
                or ""
            ),
            to_sheet_boolean(
                record.get(
                    "is_adjustment_coverage_complete"
                )
            ),
            to_sheet_number(
                record.get(
                    "latest_cumulative_adjustment_factor"
                ),
                digits=10,
            ),
            to_sheet_number(
                record.get(
                    "oldest_cumulative_adjustment_factor_5y"
                ),
                digits=10,
            ),
            to_sheet_number(
                record.get(
                    "dividend_cagr_5y_adjusted_percent"
                )
            ),
            to_sheet_boolean(
                record.get(
                    "is_progressive_dividend_5y_adjusted"
                )
            ),
            str(
                record.get(
                    "progressive_dividend_status_5y_adjusted",
                    "",
                )
                or ""
            ),
            format_dividend_history(
                record.get(
                    "adjusted_fiscal_periods_5y"
                ),
                record.get(
                    "adjusted_annual_dividends_yen_5y"
                ),
            ),
        ]

        if len(row) != len(CANDIDATE_HEADERS):
            raise RuntimeError(
                "累進配当候補の列数が一致しません。"
                f"順位: {rank}, "
                f"期待列数: {len(CANDIDATE_HEADERS)}, "
                f"実際の列数: {len(row)}"
            )

        rows.append(row)

    print(
        "Google Sheets出力用の"
        "累進配当候補を作成しました。"
        f"件数: {len(rows):,}"
    )

    return rows

# ============================================================
# TDnet PDF本文解析結果
# ============================================================

def enrich_candidate_records_with_latest_tdnet_policy_results(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """候補銘柄へPostgreSQL上の最新TDnet本文解析結果を付与する。"""

    security_codes = [
        record.get("security_code")
        for record in records
    ]

    policy_results = load_latest_tdnet_policy_results(
        security_codes
    )

    enriched_records = (
        enrich_candidates_with_tdnet_policy_results(
            records,
            policy_results,
        )
    )

    completed_count = sum(
        1
        for record in enriched_records
        if record.get(
            "tdnet_policy_analysis_status"
        ) == "completed"
    )
    confirmed_count = sum(
        1
        for record in enriched_records
        if record.get(
            "tdnet_policy_classification"
        ) == "confirmed"
    )

    print(
        "TDnet PDF本文解析結果を候補へ反映しました。"
        f"取得件数: {len(policy_results)}, "
        f"completed: {completed_count}, "
        f"confirmed: {confirmed_count}"
    )

    return enriched_records

# ============================================================
# 前回候補との差分
# ============================================================

def load_previous_candidate_snapshot(
    sheets_service,
    spreadsheet_id: str,
) -> tuple[dict[str, str], bool, str]:
    """更新前の候補シートから証券コードと銘柄名を取得する。"""

    metadata = get_spreadsheet_metadata(
        sheets_service,
        spreadsheet_id,
    )
    sheet_exists = any(
        sheet.get("properties", {}).get("title")
        == CANDIDATE_SHEET_NAME
        for sheet in metadata.get("sheets", [])
    )

    if not sheet_exists:
        print(
            "累進配当候補シートが未作成のため、"
            "前回候補との差分比較を省略します。"
        )
        return {}, False, ""

    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_SHEET_NAME,
    )

    if not values:
        return {}, True, ""

    headers = [
        str(value).strip()
        for value in values[0]
    ]

    required_headers = [
        "更新日時",
        "証券コード",
        "銘柄名",
    ]
    missing_headers = [
        header
        for header in required_headers
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            "更新前の累進配当候補シートに"
            "必要な列がありません。"
            f"不足列: {missing_headers}"
        )

    updated_at_index = headers.index("更新日時")
    code_index = headers.index("証券コード")
    name_index = headers.index("銘柄名")
    snapshot: dict[str, str] = {}
    snapshot_version = ""

    for row in values[1:]:
        if len(row) <= code_index:
            continue

        security_code = str(
            row[code_index]
        ).strip().upper()

        if not security_code:
            continue

        if security_code in snapshot:
            raise RuntimeError(
                "更新前の累進配当候補シートに"
                "証券コードの重複があります。"
                f"証券コード: {security_code}"
            )

        company_name = (
            str(row[name_index]).strip()
            if len(row) > name_index
            else ""
        )
        snapshot[security_code] = company_name

        if (
            not snapshot_version
            and len(row) > updated_at_index
        ):
            snapshot_version = str(
                row[updated_at_index]
            ).strip()

    print(
        "更新前の累進配当候補を読み込みました。"
        f"件数: {len(snapshot):,}"
    )

    return snapshot, True, snapshot_version


def calculate_candidate_changes(
    previous_snapshot: dict[str, str],
    records: list[dict[str, Any]],
    *,
    previous_sheet_exists: bool,
    previous_snapshot_version: str = "",
) -> CandidateChanges:
    """前回候補と今回候補の追加・除外銘柄を算出する。"""

    current_snapshot: dict[str, str] = {}

    for record in records:
        security_code = str(
            record.get("security_code", "")
        ).strip().upper()

        if not security_code:
            raise RuntimeError(
                "累進配当候補に証券コードが空の行があります。"
            )

        if security_code in current_snapshot:
            raise RuntimeError(
                "累進配当候補に証券コードの重複があります。"
                f"証券コード: {security_code}"
            )

        current_snapshot[security_code] = str(
            record.get("company_name", "") or ""
        )

    comparison_source = "\n".join(
        [
            "previous_version=" + previous_snapshot_version,
            "previous=" + ",".join(
                sorted(previous_snapshot)
            ),
            "current=" + ",".join(
                sorted(current_snapshot)
            ),
        ]
    )
    comparison_id = hashlib.sha256(
        comparison_source.encode("utf-8")
    ).hexdigest()[:20]

    if not previous_sheet_exists:
        return CandidateChanges(
            comparison_id=comparison_id,
            is_first_export=True,
            added_candidates=(),
            removed_candidates=(),
        )

    added_candidates = tuple(
        (security_code, company_name)
        for security_code, company_name
        in current_snapshot.items()
        if security_code not in previous_snapshot
    )
    removed_candidates = tuple(
        (security_code, company_name)
        for security_code, company_name
        in previous_snapshot.items()
        if security_code not in current_snapshot
    )

    return CandidateChanges(
        comparison_id=comparison_id,
        is_first_export=False,
        added_candidates=added_candidates,
        removed_candidates=removed_candidates,
    )


# ============================================================
# 候補除外理由
# ============================================================

def to_finite_decimal(value: Any) -> Decimal | None:
    """比較用の有限なDecimalへ変換する。"""

    if value is None:
        return None

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

    if not number.is_finite():
        return None

    return number


def diagnose_candidate_exclusion(
    record: dict[str, Any],
    criteria: CandidateCriteria,
) -> tuple[str, ...]:
    """現在の指標から候補を外れた理由を列挙する。"""

    reasons: list[str] = []

    if (
        record.get("annual_financial_id") is None
        or record.get("close_price") is None
    ):
        reasons.append("財務・株価データ不足")

    if record.get("is_adjustment_coverage_complete") is not True:
        reasons.append(
            "株式分割等補正データ不足"
        )

    uses_adjusted = (
        record.get("is_adjustment_coverage_complete") is True
    )
    progressive_field = (
        "is_progressive_dividend_5y_adjusted"
        if uses_adjusted
        else "is_progressive_dividend_5y_raw"
    )
    status_field = (
        "progressive_dividend_status_5y_adjusted"
        if uses_adjusted
        else "progressive_dividend_status_5y"
    )

    if record.get(progressive_field) is not True:
        status = str(record.get(status_field, "") or "")
        status_labels = {
            "dividend_cut": "5期内に減配",
            "non_positive_dividend": "無配・非正配当",
            "missing_dividend": "配当データ欠損",
            "irregular_fiscal_periods": "決算期間不整合",
            "insufficient_history": "配当履歴不足",
            "progressive": "累進配当判定不可",
            "": "累進配当判定不可",
        }
        reasons.append(
            status_labels.get(
                status,
                f"累進配当判定: {status}",
            )
        )

    dividend_yield = to_finite_decimal(
        record.get("dividend_yield_percent")
    )
    if dividend_yield is None:
        reasons.append("配当利回り算出不可")
    elif dividend_yield < criteria.min_dividend_yield_percent:
        reasons.append(
            "配当利回りが下限未満"
            f"（{dividend_yield:.2f}%）"
        )

    payout_ratio = to_finite_decimal(
        record.get("payout_ratio_percent")
    )
    if payout_ratio is None:
        reasons.append("配当性向算出不可")
    elif (
        payout_ratio < 0
        or payout_ratio > criteria.max_payout_ratio_percent
    ):
        reasons.append(
            "配当性向が範囲外"
            f"（{payout_ratio:.2f}%）"
        )

    per_ratio = to_finite_decimal(
        record.get("per_ratio")
    )
    if per_ratio is None or per_ratio <= 0:
        reasons.append("PER算出不可")
    elif per_ratio > criteria.max_per_ratio:
        reasons.append(
            f"PERが上限超過（{per_ratio:.2f}倍）"
        )

    pbr_ratio = to_finite_decimal(
        record.get("pbr_ratio")
    )
    if pbr_ratio is None or pbr_ratio <= 0:
        reasons.append("PBR算出不可")
    elif pbr_ratio > criteria.max_pbr_ratio:
        reasons.append(
            f"PBRが上限超過（{pbr_ratio:.2f}倍）"
        )

    roe_percent = to_finite_decimal(
        record.get("roe_percent")
    )
    if roe_percent is None:
        reasons.append("ROE算出不可")
    elif roe_percent < criteria.min_roe_percent:
        reasons.append(
            f"ROEが下限未満（{roe_percent:.2f}%）"
        )

    if criteria.require_positive_free_cash_flow:
        free_cash_flow = to_finite_decimal(
            record.get("free_cash_flow_jpy")
        )
        if free_cash_flow is None:
            reasons.append("フリーCF算出不可")
        elif free_cash_flow <= 0:
            reasons.append("フリーCFが0以下")

    if not reasons:
        reasons.append(
            f"ランキング上限{criteria.max_candidates}件の対象外"
        )

    return tuple(reasons)


def load_removed_candidate_reasons(
    changes: CandidateChanges,
    criteria: CandidateCriteria,
) -> dict[str, tuple[str, ...]]:
    """候補から外れた銘柄の現在指標を取得し理由を判定する。"""

    removed_codes = [
        security_code
        for security_code, _
        in changes.removed_candidates
    ]

    if not removed_codes:
        return {}

    query = """
        SELECT
            security_code,
            annual_financial_id,
            close_price,
            is_progressive_dividend_5y_raw,
            progressive_dividend_status_5y,
            is_adjustment_coverage_complete,
            is_progressive_dividend_5y_adjusted,
            progressive_dividend_status_5y_adjusted,
            dividend_yield_percent,
            payout_ratio_percent,
            per_ratio,
            pbr_ratio,
            roe_percent,
            free_cash_flow_jpy
        FROM screener.company_screener_with_dividends
        WHERE security_code = ANY(%s)
        ORDER BY security_code;
    """

    with create_database_connection(
        "diagnose_candidate_exclusions"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (removed_codes,),
            )
            records_by_code = {
                str(row["security_code"]): dict(row)
                for row in cursor.fetchall()
            }

    reasons_by_code: dict[str, tuple[str, ...]] = {}

    for security_code in removed_codes:
        record = records_by_code.get(security_code)

        if record is None:
            reasons_by_code[security_code] = (
                "現在の銘柄データなし",
            )
            continue

        reasons_by_code[security_code] = (
            diagnose_candidate_exclusion(
                record,
                criteria,
            )
        )

    print(
        "候補から外れた理由を判定しました。"
        f"件数: {len(reasons_by_code):,}"
    )

    return reasons_by_code


# ============================================================
# 候補変動履歴
# ============================================================

def build_candidate_change_history_rows(
    changes: CandidateChanges,
    removed_reasons: dict[str, tuple[str, ...]],
    criteria: CandidateCriteria,
    *,
    detected_at: datetime | None = None,
) -> list[list[str]]:
    """追加・除外された候補を履歴シートの行へ変換する。"""

    if changes.is_first_export or not changes.has_changes:
        return []

    if detected_at is None:
        detected_at = datetime.now(JST)

    detected_at_text = detected_at.strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    criteria_text = criteria.describe()
    rows: list[list[str]] = []

    for security_code, company_name in (
        changes.added_candidates
    ):
        rows.append(
            [
                f"{changes.comparison_id}:added:{security_code}",
                detected_at_text,
                "新規",
                security_code,
                company_name,
                "抽出条件を満たした",
                criteria_text,
            ]
        )

    for security_code, company_name in (
        changes.removed_candidates
    ):
        reasons = removed_reasons.get(
            security_code,
            ("理由取得不可",),
        )
        rows.append(
            [
                f"{changes.comparison_id}:removed:{security_code}",
                detected_at_text,
                "除外",
                security_code,
                company_name,
                " / ".join(reasons),
                criteria_text,
            ]
        )

    for row in rows:
        if len(row) != len(CANDIDATE_HISTORY_HEADERS):
            raise RuntimeError(
                "候補変動履歴の列数が一致しません。"
            )

    return rows


def append_candidate_change_history(
    sheets_service,
    spreadsheet_id: str,
    rows: list[list[str]],
) -> int:
    """未保存の候補変動だけを履歴シートへ追記する。"""

    if not rows:
        print(
            "候補の追加・除外がないため、"
            "変動履歴の追記を省略します。"
        )
        return 0

    get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_HISTORY_SHEET_NAME,
    )
    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_HISTORY_SHEET_NAME,
    )

    if not values:
        write_sheet(
            sheets_service,
            spreadsheet_id,
            CANDIDATE_HISTORY_SHEET_NAME,
            CANDIDATE_HISTORY_HEADERS,
            [],
        )
        values = [CANDIDATE_HISTORY_HEADERS]

    headers = [
        str(value).strip()
        for value in values[0]
    ]

    if headers != CANDIDATE_HISTORY_HEADERS:
        raise RuntimeError(
            "候補変動履歴シートの列が一致しません。"
            f"期待列: {CANDIDATE_HISTORY_HEADERS}, "
            f"実際の列: {headers}"
        )

    existing_event_ids = {
        str(row[0]).strip()
        for row in values[1:]
        if row and str(row[0]).strip()
    }
    new_rows = [
        row
        for row in rows
        if row[0] not in existing_event_ids
    ]

    if not new_rows:
        print(
            "候補変動履歴は保存済みのため、"
            "重複追記を省略します。"
        )
        return 0

    (
        sheets_service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=(
                f"'{CANDIDATE_HISTORY_SHEET_NAME}'!A:G"
            ),
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={
                "values": new_rows,
            },
        )
        .execute()
    )

    print(
        "累進配当候補の変動履歴を追記しました。"
        f"件数: {len(new_rows):,}"
    )

    return len(new_rows)


# ============================================================
# Discord通知
# ============================================================

def format_notification_metric(
    value: Any,
    *,
    suffix: str = "",
) -> str:
    """Discord通知用に数値を小数第2位まで整形する。"""

    if value is None:
        return "-"

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "-"

    if not number.is_finite():
        return "-"

    normalized = f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{normalized}{suffix}"

# ============================================================
# Discord向けTDnet PDF本文解析表示
# ============================================================

def build_tdnet_policy_discord_lines(
    record: dict[str, Any],
) -> list[str]:
    """
    Discord通知へ表示するTDnet PDF本文解析行を作成する。

    confirmed、manual_review、解析失敗など、
   確認が必要な結果だけを通知へ追加する。
    not_confirmedと解析結果なしは表示しない。
    """

    analysis_status = str(
        record.get(
            "tdnet_policy_analysis_status",
            "",
        )
        or ""
    ).strip()
    classification = str(
        record.get(
            "tdnet_policy_classification",
            "",
        )
        or ""
    ).strip()
    matched_phrase = " ".join(
        str(
            record.get(
                "tdnet_policy_matched_phrase",
                "",
            )
            or ""
        ).split()
    )

    should_display = (
        classification
        in {
            "confirmed",
            "manual_review",
        }
        or (
            bool(analysis_status)
            and analysis_status != "completed"
        )
    )

    if not should_display:
        return []

    lines: list[str] = []

    if analysis_status:
        lines.append(
            "   - TDnet本文確認状態: "
            f"`{analysis_status}`"
        )

    if classification:
        lines.append(
            "   - TDnet本文判定: "
            f"`{classification}`"
        )

    if matched_phrase:
        maximum_phrase_length = 120

        if len(matched_phrase) > maximum_phrase_length:
            matched_phrase = (
                matched_phrase[
                    :maximum_phrase_length - 1
                ]
                + "…"
            )

        lines.append(
            "   - 一致フレーズ: "
            f"{matched_phrase}"
        )

    return lines

def normalize_exclusion_reason(
    reason: str,
) -> str:
    """除外理由から数値付きの補足を取り除いて分類する。"""

    text = reason.strip()

    if (
        text.endswith("）")
        and "（" in text
    ):
        text = text[: text.index("（")].strip()

    return text


def summarize_exclusion_reasons(
    removed_reasons: dict[str, tuple[str, ...]],
    *,
    max_categories: int = 5,
) -> list[str]:
    """除外理由を銘柄単位で集計し、内訳行を返す。"""

    if max_categories < 1:
        raise ValueError(
            "内訳の表示種別数は1以上で指定してください。"
        )

    counts: dict[str, int] = {}

    for reasons in removed_reasons.values():
        seen_categories: set[str] = set()

        for reason in reasons:
            category = normalize_exclusion_reason(
                str(reason)
            )

            if not category:
                continue

            if category in seen_categories:
                continue

            seen_categories.add(category)
            counts[category] = (
                counts.get(category, 0) + 1
            )

    if not counts:
        return []

    ordered_categories = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    shown_categories = ordered_categories[
        :max_categories
    ]

    summary = " / ".join(
        f"{name} {count}件"
        for name, count in shown_categories
    )

    remaining_count = (
        len(ordered_categories) - len(shown_categories)
    )

    if remaining_count > 0:
        summary += f" / ほか{remaining_count}種"

    return [f"除外理由内訳: {summary}"]


def build_discord_notification_description(
    records: list[dict[str, Any]],
    criteria: CandidateCriteria,
    changes: CandidateChanges,
    removed_reasons: dict[str, tuple[str, ...]],
    *,
    display_limit: int = 10,
) -> str:
    """候補件数・差分・抽出条件・上位銘柄を通知文にする。"""

    if display_limit < 1:
        raise ValueError(
            "Discord通知の表示件数は1以上で指定してください。"
        )

    lines = [
        f"抽出件数: **{len(records):,}件**",
    ]

    if changes.is_first_export:
        lines.append(
            "前回比: 初回出力のため比較なし"
        )
    elif changes.has_changes:
        lines.append(
            "前回比: "
            f"新規 **{len(changes.added_candidates):,}件** / "
            f"除外 **{len(changes.removed_candidates):,}件**"
        )

        if changes.added_candidates:
            added_text = ", ".join(
                f"`{code}` {name}"
                for code, name
                in changes.added_candidates[:display_limit]
            )
            lines.append(f"新規: {added_text}")

        if changes.removed_candidates:
            removed_items: list[str] = []

            for code, name in (
                changes.removed_candidates[:display_limit]
            ):
                reasons = removed_reasons.get(
                    code,
                    ("理由取得不可",),
                )
                reason_text = " / ".join(reasons)
                removed_items.append(
                    f"`{code}` {name}（{reason_text}）"
                )

            lines.append(
                "除外: " + ", ".join(removed_items)
            )
            lines.extend(
                summarize_exclusion_reasons(
                    removed_reasons
                )
            )
    else:
        lines.append("前回比: **変更なし**")

    lines.extend(
        [
            f"抽出条件: {criteria.describe()}",
            "",
        ]
    )

    if not records:
        lines.extend(
            [
                "**上位候補**",
                "該当する銘柄はありませんでした。",
            ]
        )
    else:
        lines.append(
            f"**上位{min(display_limit, len(records))}銘柄**"
        )

        for rank, record in enumerate(
            records[:display_limit],
            start=1,
        ):
            security_code = str(
                record.get("security_code", "")
            )
            company_name = str(
                record.get("company_name", "") or ""
            )
            dividend_yield = format_notification_metric(
                record.get("dividend_yield_percent"),
                suffix="%",
            )
            dividend_cagr = format_notification_metric(
                record.get(
                    "dividend_cagr_5y_adjusted_percent"
                    if record.get("is_adjustment_coverage_complete") is True
                    else "dividend_cagr_5y_percent"
                ),
                suffix="%",
            )
            roe = format_notification_metric(
                record.get("roe_percent"),
                suffix="%",
            )
            markers: list[str] = []

            if record.get("tdnet_policy_candidate"):
                markers.append("TDnet方針候補")

            if record.get("tdnet_dividend_warning"):
                markers.append("TDnet減配警戒")

            analysis_status = str(
                record.get(
                    "tdnet_policy_analysis_status",
                    "",
                )
                or ""
            ).strip()
            classification = str(
                record.get(
                    "tdnet_policy_classification",
                    "",
                )
                or ""
            ).strip()

            if classification == "confirmed":
                markers.append(
                    "TDnet本文 confirmed"
                )
            elif classification == "manual_review":
                markers.append(
                    "TDnet本文 manual_review"
                )
            elif (
                analysis_status
                and analysis_status != "completed"
            ):
                markers.append(
                    f"TDnet本文 {analysis_status}"
                )

            marker_text = (
                " [" + " / ".join(markers) + "]"
                if markers
                else ""
            )

            decision_label = (
                "adjusted"
                if record.get(
                    "is_adjustment_coverage_complete"
                ) is True
                else "raw"
            )

            lines.append(
                f"{rank}. `{security_code}` {company_name}"
                f"{marker_text} [{decision_label}] — "
                f"利回り {dividend_yield} / "
                f"5期CAGR {dividend_cagr} / "
                f"ROE {roe}"
            )
            lines.extend(
                build_tdnet_policy_discord_lines(
                    record
                )
            )

    lines.extend(
        [
            "",
            "注意: 補正範囲が完全な銘柄だけadjusted判定を採用し、"
            "未充足銘柄はraw判定と注記を維持します。",
            "詳細はGoogleスプレッドシートの"
            f"「{CANDIDATE_SHEET_NAME}」を確認してください。",
        ]
    )

    return "\n".join(lines)


def notify_discord_candidates(
    records: list[dict[str, Any]],
    criteria: CandidateCriteria,
    changes: CandidateChanges,
    removed_reasons: dict[str, tuple[str, ...]],
) -> None:
    """Webhook設定時だけ累進配当候補の更新結果を通知する。"""

    webhook_url = os.getenv(
        "DISCORD_WEBHOOK_URL",
        "",
    ).strip()

    if not webhook_url:
        print(
            "DISCORD_WEBHOOK_URLが未設定のため、"
            "累進配当候補のDiscord通知を省略します。"
        )
        return

    description = build_discord_notification_description(
        records,
        criteria,
        changes,
        removed_reasons,
    )

    send_discord_notification(
        webhook_url,
        "累進配当候補を更新しました",
        description,
        success=True,
    )

    print(
        "累進配当候補のDiscord通知処理を実行しました。"
    )


# ============================================================
# TDnet方針候補との連携
# ============================================================

def refresh_tdnet_disclosures_non_fatal(
    sheets_service,
    spreadsheet_id: str,
) -> None:
    """TDnet更新を実行し、失敗時は候補更新を継続する。"""

    try:
        from update_tdnet_dividend_disclosures import (
            update_tdnet_dividend_disclosures,
        )

        update_tdnet_dividend_disclosures(
            sheets_service,
            spreadsheet_id,
        )
    except Exception as error:
        print(
            "TDnet配当関連開示の更新に失敗しました。"
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        traceback.print_exc()

        webhook_url = os.getenv(
            "DISCORD_WEBHOOK_URL",
            "",
        ).strip()

        if webhook_url:
            send_discord_notification(
                webhook_url,
                "TDnet配当関連開示の更新に失敗しました",
                "財務・株式指標・累進配当候補の更新は"
                "継続します。TDnet公開一覧の取得だけが"
                "失敗したため、GitHub Actionsのログを"
                "確認してください。",
                success=False,
            )


def load_tdnet_policy_candidates(
    sheets_service,
    spreadsheet_id: str,
) -> dict[str, dict[str, str]]:
    """累進配当方針候補シートを証券コード別に読み込む。"""

    policy_sheet_name = "累進配当方針候補"
    metadata = get_spreadsheet_metadata(
        sheets_service,
        spreadsheet_id,
    )
    sheet_exists = any(
        sheet.get("properties", {}).get("title")
        == policy_sheet_name
        for sheet in metadata.get("sheets", [])
    )

    if not sheet_exists:
        return {}

    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        policy_sheet_name,
    )

    if len(values) < 2:
        return {}

    headers = [
        str(value).strip()
        for value in values[0]
    ]
    required_headers = [
        "証券コード",
        "最新開示日",
        "最新表題",
        "PDF URL",
    ]
    missing_headers = [
        header
        for header in required_headers
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            "累進配当方針候補シートに必要な列がありません。"
            f"不足列: {missing_headers}"
        )

    code_index = headers.index("証券コード")
    date_index = headers.index("最新開示日")
    title_index = headers.index("最新表題")
    url_index = headers.index("PDF URL")
    policies: dict[str, dict[str, str]] = {}

    for row in values[1:]:
        if len(row) <= code_index:
            continue

        security_code = str(
            row[code_index]
        ).strip().upper()

        if not security_code:
            continue

        policies[security_code] = {
            "date": (
                str(row[date_index]).strip()
                if len(row) > date_index
                else ""
            ),
            "title": (
                str(row[title_index]).strip()
                if len(row) > title_index
                else ""
            ),
            "url": (
                str(row[url_index]).strip()
                if len(row) > url_index
                else ""
            ),
        }

    return policies


def load_tdnet_dividend_alerts(
    sheets_service,
    spreadsheet_id: str,
) -> dict[str, dict[str, str]]:
    """TDnet配当開示から銘柄ごとの最新開示を読み込む。"""

    disclosure_sheet_name = "TDnet配当開示"
    metadata = get_spreadsheet_metadata(
        sheets_service,
        spreadsheet_id,
    )
    sheet_exists = any(
        sheet.get("properties", {}).get("title")
        == disclosure_sheet_name
        for sheet in metadata.get("sheets", [])
    )

    if not sheet_exists:
        return {}

    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        disclosure_sheet_name,
    )

    if len(values) < 2:
        return {}

    headers = [
        str(value).strip()
        for value in values[0]
    ]
    required_headers = [
        "公開日",
        "公開時刻",
        "証券コード",
        "表題",
        "分類",
        "PDF URL",
    ]
    missing_headers = [
        header
        for header in required_headers
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            "TDnet配当開示シートに必要な列がありません。"
            f"不足列: {missing_headers}"
        )

    indexes = {
        header: headers.index(header)
        for header in required_headers
    }
    alerts: dict[str, dict[str, str]] = {}

    for row in values[1:]:
        code_index = indexes["証券コード"]

        if len(row) <= code_index:
            continue

        security_code = str(
            row[code_index]
        ).strip().upper()

        if not security_code:
            continue

        def cell(header: str) -> str:
            index = indexes[header]
            return (
                str(row[index]).strip()
                if len(row) > index
                else ""
            )

        candidate = {
            "date": cell("公開日"),
            "time": cell("公開時刻"),
            "title": cell("表題"),
            "category": cell("分類"),
            "url": cell("PDF URL"),
        }
        current = alerts.get(security_code)

        if current is None or (
            candidate["date"],
            candidate["time"],
        ) > (
            current["date"],
            current["time"],
        ):
            alerts[security_code] = candidate

    return alerts


def enrich_candidate_records_with_tdnet_policy(
    records: list[dict[str, Any]],
    policies: dict[str, dict[str, str]],
    alerts: dict[str, dict[str, str]],
) -> None:
    """候補レコードへTDnet方針候補・配当警戒情報を付加する。"""

    for record in records:
        security_code = str(
            record.get("security_code", "")
        ).strip().upper()
        policy = policies.get(security_code)
        record["tdnet_policy_candidate"] = bool(policy)
        record["tdnet_policy_date"] = (
            policy.get("date", "")
            if policy
            else ""
        )
        record["tdnet_policy_title"] = (
            policy.get("title", "")
            if policy
            else ""
        )
        record["tdnet_policy_url"] = (
            policy.get("url", "")
            if policy
            else ""
        )

        alert = alerts.get(security_code)
        alert_category = (
            alert.get("category", "")
            if alert
            else ""
        )
        record["tdnet_dividend_warning"] = (
            alert_category == "減配・無配"
        )
        record["tdnet_dividend_date"] = (
            alert.get("date", "")
            if alert
            else ""
        )
        record["tdnet_dividend_category"] = (
            alert_category
        )
        record["tdnet_dividend_title"] = (
            alert.get("title", "")
            if alert
            else ""
        )
        record["tdnet_dividend_url"] = (
            alert.get("url", "")
            if alert
            else ""
        )


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """累進配当候補を抽出し、専用シートへ出力する。"""

    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )
    service_account_json = get_required_environment_variable(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )
    sheets_service = create_google_sheets_service(
        service_account_json
    )
    criteria = load_candidate_criteria_from_sheet(
        sheets_service,
        spreadsheet_id,
    )
    refresh_tdnet_disclosures_non_fatal(
        sheets_service,
        spreadsheet_id,
    )
    tdnet_policies = load_tdnet_policy_candidates(
        sheets_service,
        spreadsheet_id,
    )
    tdnet_alerts = load_tdnet_dividend_alerts(
        sheets_service,
        spreadsheet_id,
    )

    print(f"累進配当候補の抽出条件: {criteria.describe()}")
    print(
        "判定上の注意: 補正範囲充足時だけadjusted判定を採用し、"
        "未充足時はraw判定を使用します。"
    )
    (
        previous_snapshot,
        previous_sheet_exists,
        previous_snapshot_version,
    ) = load_previous_candidate_snapshot(
        sheets_service,
        spreadsheet_id,
    )
    records = load_progressive_dividend_candidates(criteria)
    enrich_candidate_records_with_tdnet_policy(
        records,
        tdnet_policies,
        tdnet_alerts,
    )
    records = (
        enrich_candidate_records_with_latest_tdnet_policy_results(
            records
        )
    )
    changes = calculate_candidate_changes(
        previous_snapshot,
        records,
        previous_sheet_exists=previous_sheet_exists,
        previous_snapshot_version=previous_snapshot_version,
    )
    removed_reasons = load_removed_candidate_reasons(
        changes,
        criteria,
    )
    history_rows = build_candidate_change_history_rows(
        changes,
        removed_reasons,
        criteria,
    )
    candidate_rows = build_candidate_rows(records)

    # 候補シート更新が失敗して再実行された場合でも、
    # イベントIDで重複を防げるため履歴を先に保存する。
    append_candidate_change_history(
        sheets_service,
        spreadsheet_id,
        history_rows,
    )

    write_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_SHEET_NAME,
        CANDIDATE_HEADERS,
        candidate_rows,
    )

    candidate_sheet_id = get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_SHEET_NAME,
    )

    apply_column_formats(
        sheets_service,
        spreadsheet_id,
        candidate_sheet_id,
        CANDIDATE_COLUMN_FORMATS,
    )

    print(
        "累進配当候補の出力が完了しました。"
        f"シート: {CANDIDATE_SHEET_NAME}, "
        f"件数: {len(candidate_rows):,}"
    )

    notify_discord_candidates(
        records,
        criteria,
        changes,
        removed_reasons,
    )

    # 配当履歴の株式分割等未調整リスクを確認するため、
    # 発行済株式数が大幅に変化した期間も同時に出力する。
    from export_corporate_action_candidates import (
        export_corporate_action_candidates,
    )

    export_corporate_action_candidates(
        sheets_service,
        spreadsheet_id,
    )

    # ライツイシュー等の自動補正対象外アクションも同時に出力し、
    # 補正データ不足による候補除外の理由を確認できるようにする。
    from export_unsupported_corporate_actions import (
        export_unsupported_corporate_actions,
    )

    export_unsupported_corporate_actions(
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
            "累進配当候補の出力中にエラーが発生しました。",
            file=sys.stderr,
        )
        traceback.print_exc()
        print(
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
