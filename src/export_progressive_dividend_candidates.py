"""
PostgreSQLの累進配当指標から投資条件に合う銘柄を抽出し、
Google Sheetsの「累進配当候補」シートへランキング出力する。

抽出条件は環境変数で変更できる。判定元の年間配当は
株式分割・株式併合による過年度調整前の値である。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

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
) -> tuple[dict[str, str], bool]:
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
        return {}, False

    values = read_sheet(
        sheets_service,
        spreadsheet_id,
        CANDIDATE_SHEET_NAME,
    )

    if not values:
        return {}, True

    headers = [
        str(value).strip()
        for value in values[0]
    ]

    required_headers = [
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

    code_index = headers.index("証券コード")
    name_index = headers.index("銘柄名")
    snapshot: dict[str, str] = {}

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

    print(
        "更新前の累進配当候補を読み込みました。"
        f"件数: {len(snapshot):,}"
    )

    return snapshot, True


def calculate_candidate_changes(
    previous_snapshot: dict[str, str],
    records: list[dict[str, Any]],
    *,
    previous_sheet_exists: bool,
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

    if not previous_sheet_exists:
        return CandidateChanges(
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
        is_first_export=False,
        added_candidates=added_candidates,
        removed_candidates=removed_candidates,
    )


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
            removed_text = ", ".join(
                f"`{code}` {name}"
                for code, name
                in changes.removed_candidates[:display_limit]
            )
            lines.append(f"除外: {removed_text}")
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
    previous_snapshot, previous_sheet_exists = (
        load_previous_candidate_snapshot(
            sheets_service,
            spreadsheet_id,
        )
    )
    records = load_progressive_dividend_candidates(criteria)
    changes = calculate_candidate_changes(
        previous_snapshot,
        records,
        previous_sheet_exists=previous_sheet_exists,
    )
    candidate_rows = build_candidate_rows(records)

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
