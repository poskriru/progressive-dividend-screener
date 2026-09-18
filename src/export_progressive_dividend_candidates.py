"""
PostgreSQLの累進配当指標から投資条件に合う銘柄を抽出し、
Google Sheetsの「累進配当候補」シートへランキング出力する。

抽出条件は環境変数で変更できる。判定元の年間配当は
株式分割・株式併合による過年度調整前の値である。
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
    to_sheet_date,
    to_sheet_integer,
    to_sheet_number,
    yen_to_sheet_million,
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


# ============================================================
# 定数
# ============================================================

CANDIDATE_SHEET_NAME = "累進配当候補"
CANDIDATE_HISTORY_SHEET_NAME = "累進配当候補_変動履歴"

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
    "判定注記",
]

DEFAULT_MIN_DIVIDEND_YIELD_PERCENT = Decimal("3.0")
DEFAULT_MAX_PAYOUT_RATIO_PERCENT = Decimal("70.0")
DEFAULT_MAX_PER_RATIO = Decimal("25.0")
DEFAULT_MAX_PBR_RATIO = Decimal("3.0")
DEFAULT_MIN_ROE_PERCENT = Decimal("8.0")
DEFAULT_REQUIRE_POSITIVE_FREE_CASH_FLOW = True
DEFAULT_MAX_CANDIDATES = 300

RAW_DIVIDEND_CAUTION = "株式分割・併合の過年度配当は未調整"


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
            financial_source_url
        FROM screener.company_screener_with_dividends
        WHERE annual_financial_id IS NOT NULL
          AND close_price IS NOT NULL
          AND is_progressive_dividend_5y_raw IS TRUE
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
            dividend_cagr_5y_percent DESC NULLS LAST,
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

    updated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    rows: list[list[Any]] = []

    for rank, record in enumerate(records, start=1):
        row = [
            updated_at,
            rank,
            to_sheet_date(record.get("trading_date")),
            str(record.get("security_code", "")),
            str(record.get("company_name", "") or ""),
            str(record.get("market", "") or ""),
            str(record.get("industry_33_name", "") or ""),
            to_sheet_number(record.get("close_price")),
            to_sheet_number(record.get("dividend_yield_percent")),
            to_sheet_number(record.get("payout_ratio_percent")),
            to_sheet_number(record.get("per_ratio")),
            to_sheet_number(record.get("pbr_ratio")),
            to_sheet_number(record.get("roe_percent")),
            to_sheet_number(record.get("equity_ratio_percent")),
            yen_to_sheet_million(record.get("free_cash_flow_jpy")),
            to_sheet_number(record.get("dividend_cagr_5y_percent")),
            to_sheet_integer(record.get("dividend_increase_count_5y")),
            to_sheet_integer(record.get("dividend_unchanged_count_5y")),
            to_sheet_integer(record.get("consecutive_non_decrease_periods")),
            to_sheet_integer(record.get("consecutive_increase_periods")),
            to_sheet_number(
                record.get("dividend_latest_annual_dividend_yen")
            ),
            to_sheet_number(record.get("oldest_annual_dividend_yen_5y")),
            format_dividend_history(
                record.get("fiscal_periods_5y"),
                record.get("annual_dividends_yen_5y"),
            ),
            str(record.get("financial_source_url", "") or ""),
            RAW_DIVIDEND_CAUTION,
        ]

        if len(row) != len(CANDIDATE_HEADERS):
            raise RuntimeError(
                "累進配当候補の列数が一致しません。"
                f"順位: {rank}, 期待列数: {len(CANDIDATE_HEADERS)}, "
                f"実際の列数: {len(row)}"
            )

        rows.append(row)

    print(
        "Google Sheets出力用の累進配当候補を作成しました。"
        f"件数: {len(rows):,}"
    )

    return rows


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

    if record.get("is_progressive_dividend_5y_raw") is not True:
        status = str(
            record.get("progressive_dividend_status_5y", "")
            or ""
        )
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
                record.get("dividend_cagr_5y_percent"),
                suffix="%",
            )
            roe = format_notification_metric(
                record.get("roe_percent"),
                suffix="%",
            )

            lines.append(
                f"{rank}. `{security_code}` {company_name} — "
                f"利回り {dividend_yield} / "
                f"5期CAGR {dividend_cagr} / "
                f"ROE {roe}"
            )

    lines.extend(
        [
            "",
            f"注意: {RAW_DIVIDEND_CAUTION}",
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
    criteria = CandidateCriteria.from_environment()

    print(f"累進配当候補の抽出条件: {criteria.describe()}")
    print(f"判定上の注意: {RAW_DIVIDEND_CAUTION}")

    sheets_service = create_google_sheets_service(
        service_account_json
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
