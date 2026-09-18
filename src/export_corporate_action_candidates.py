"""
EDINET年次財務の発行済株式数が大きく変化した期間を抽出し、
株式分割・株式併合等による配当履歴への影響確認用シートへ出力する。

発行済株式数の変動だけでは株式分割・併合を確定できないため、
本処理は自動補正を行わず、一次資料で確認すべき銘柄を提示する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import traceback
from datetime import datetime
from decimal import Decimal
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from export_database_indicators import (
    to_sheet_date,
    to_sheet_integer,
    to_sheet_number,
)

from update_edinet_financials import (
    JST,
    create_google_sheets_service,
    get_required_environment_variable,
    write_sheet,
)


# ============================================================
# 定数
# ============================================================

CORPORATE_ACTION_CANDIDATE_SHEET_NAME = (
    "株式分割等確認対象"
)

CORPORATE_ACTION_CANDIDATE_HEADERS = [
    "更新日時",
    "証券コード",
    "銘柄名",
    "市場",
    "変動種別",
    "旧決算期末日",
    "新決算期末日",
    "旧発行済株式数",
    "新発行済株式数",
    "株式数変動倍率",
    "旧年間配当（円）",
    "新年間配当（円）",
    "新旧配当倍率",
    "確認事項",
    "旧EDINET閲覧URL",
    "新EDINET閲覧URL",
]

MINIMUM_SHARE_INCREASE_RATIO = Decimal("1.5")
MAXIMUM_SHARE_DECREASE_RATIO = Decimal("0.67")
LOOKBACK_YEARS = 6

DIAGNOSTIC_CAUTION = (
    "発行済株式数の大幅変動から抽出した確認対象です。"
    "株式分割・併合を確定するものではありません。"
    "増資、自己株式消却、組織再編等の可能性もあるため、"
    "EDINET・適時開示・会社IRの一次資料を確認してください。"
)


# ============================================================
# 数値判定
# ============================================================

def to_decimal(value: Any) -> Decimal | None:
    """有限なDecimalへ変換する。"""

    if value is None:
        return None

    try:
        number = Decimal(str(value))
    except Exception:
        return None

    if not number.is_finite():
        return None

    return number


def classify_share_change(
    share_change_ratio: Any,
) -> str:
    """発行済株式数の変動倍率から確認種別を返す。"""

    ratio = to_decimal(share_change_ratio)

    if ratio is None or ratio <= 0:
        raise RuntimeError(
            "株式数変動倍率が不正です。"
            f"指定値: {share_change_ratio}"
        )

    if ratio >= MINIMUM_SHARE_INCREASE_RATIO:
        return "株式分割・増資等の疑い"

    if ratio <= MAXIMUM_SHARE_DECREASE_RATIO:
        return "株式併合・自己株式消却等の疑い"

    raise RuntimeError(
        "確認対象外の株式数変動倍率です。"
        f"指定値: {ratio}"
    )


# ============================================================
# PostgreSQLから確認対象を取得
# ============================================================

def load_corporate_action_candidates() -> list[dict[str, Any]]:
    """発行済株式数が大幅に変化した決算期間を取得する。"""

    query = """
        WITH ranked_period_records AS (
            SELECT
                financials.annual_financial_id,
                financials.security_code,
                financials.doc_id,
                financials.fiscal_period_end,
                financials.issued_shares,
                financials.annual_dividend_yen,
                financials.source_url,
                financials.extracted_at,
                documents.submitted_at,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        financials.security_code,
                        financials.fiscal_period_end
                    ORDER BY
                        documents.submitted_at DESC NULLS LAST,
                        financials.extracted_at DESC NULLS LAST,
                        financials.annual_financial_id DESC
                ) AS period_record_rank
            FROM screener.annual_financials AS financials
            LEFT JOIN screener.edinet_documents AS documents
                ON documents.doc_id = financials.doc_id
            WHERE financials.fiscal_period_end IS NOT NULL
        ),
        selected_periods AS (
            SELECT
                annual_financial_id,
                security_code,
                doc_id,
                fiscal_period_end,
                issued_shares,
                annual_dividend_yen,
                source_url
            FROM ranked_period_records
            WHERE period_record_rank = 1
        ),
        period_comparisons AS (
            SELECT
                selected_periods.*,
                LAG(fiscal_period_end) OVER (
                    PARTITION BY security_code
                    ORDER BY fiscal_period_end
                ) AS previous_fiscal_period_end,
                LAG(issued_shares) OVER (
                    PARTITION BY security_code
                    ORDER BY fiscal_period_end
                ) AS previous_issued_shares,
                LAG(annual_dividend_yen) OVER (
                    PARTITION BY security_code
                    ORDER BY fiscal_period_end
                ) AS previous_annual_dividend_yen,
                LAG(source_url) OVER (
                    PARTITION BY security_code
                    ORDER BY fiscal_period_end
                ) AS previous_source_url
            FROM selected_periods
        )
        SELECT
            comparisons.security_code,
            securities.company_name,
            securities.market,
            comparisons.previous_fiscal_period_end,
            comparisons.fiscal_period_end,
            comparisons.previous_issued_shares,
            comparisons.issued_shares,
            comparisons.issued_shares::numeric
                / NULLIF(
                    comparisons.previous_issued_shares,
                    0
                )::numeric AS share_change_ratio,
            comparisons.previous_annual_dividend_yen,
            comparisons.annual_dividend_yen,
            CASE
                WHEN comparisons.previous_annual_dividend_yen > 0
                 AND comparisons.annual_dividend_yen IS NOT NULL
                THEN comparisons.annual_dividend_yen
                    / comparisons.previous_annual_dividend_yen
                ELSE NULL::numeric
            END AS dividend_change_ratio,
            comparisons.previous_source_url,
            comparisons.source_url
        FROM period_comparisons AS comparisons
        INNER JOIN screener.securities AS securities
            ON securities.security_code
                = comparisons.security_code
        WHERE securities.is_active = TRUE
          AND comparisons.fiscal_period_end
                >= CURRENT_DATE
                    - (%s * INTERVAL '1 year')
          AND comparisons.previous_fiscal_period_end IS NOT NULL
          AND comparisons.previous_issued_shares > 0
          AND comparisons.issued_shares > 0
          AND (
              comparisons.issued_shares::numeric
                    / comparisons.previous_issued_shares::numeric
                    >= %s
              OR comparisons.issued_shares::numeric
                    / comparisons.previous_issued_shares::numeric
                    <= %s
          )
        ORDER BY
            comparisons.fiscal_period_end DESC,
            comparisons.security_code;
    """

    parameters = (
        LOOKBACK_YEARS,
        MINIMUM_SHARE_INCREASE_RATIO,
        MAXIMUM_SHARE_DECREASE_RATIO,
    )

    with create_database_connection(
        "export_corporate_action_candidates"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, parameters)
            records = [
                dict(row)
                for row in cursor.fetchall()
            ]

    print(
        "発行済株式数の大幅変動を取得しました。"
        f"件数: {len(records):,}"
    )

    return records


# ============================================================
# Google Sheets出力行作成
# ============================================================

def build_corporate_action_candidate_rows(
    records: list[dict[str, Any]],
) -> list[list[Any]]:
    """確認対象をGoogle Sheetsの列順へ変換する。"""

    updated_at = datetime.now(JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    rows: list[list[Any]] = []

    for record in records:
        row = [
            updated_at,
            str(record.get("security_code", "")),
            str(record.get("company_name", "") or ""),
            str(record.get("market", "") or ""),
            classify_share_change(
                record.get("share_change_ratio")
            ),
            to_sheet_date(
                record.get("previous_fiscal_period_end")
            ),
            to_sheet_date(
                record.get("fiscal_period_end")
            ),
            to_sheet_integer(
                record.get("previous_issued_shares")
            ),
            to_sheet_integer(
                record.get("issued_shares")
            ),
            to_sheet_number(
                record.get("share_change_ratio"),
                digits=4,
            ),
            to_sheet_number(
                record.get("previous_annual_dividend_yen")
            ),
            to_sheet_number(
                record.get("annual_dividend_yen")
            ),
            to_sheet_number(
                record.get("dividend_change_ratio"),
                digits=4,
            ),
            DIAGNOSTIC_CAUTION,
            str(
                record.get("previous_source_url", "")
                or ""
            ),
            str(record.get("source_url", "") or ""),
        ]

        if len(row) != len(
            CORPORATE_ACTION_CANDIDATE_HEADERS
        ):
            raise RuntimeError(
                "株式分割等確認対象の列数が一致しません。"
                f"証券コード: {row[1]}, "
                "期待列数: "
                f"{len(CORPORATE_ACTION_CANDIDATE_HEADERS)}, "
                f"実際の列数: {len(row)}"
            )

        rows.append(row)

    print(
        "Google Sheets出力用の株式分割等確認対象を"
        f"作成しました。件数: {len(rows):,}"
    )

    return rows


# ============================================================
# 出力処理
# ============================================================

def export_corporate_action_candidates(
    sheets_service,
    spreadsheet_id: str,
) -> int:
    """確認対象を専用シートへ出力して件数を返す。"""

    records = load_corporate_action_candidates()
    rows = build_corporate_action_candidate_rows(
        records
    )

    write_sheet(
        sheets_service,
        spreadsheet_id,
        CORPORATE_ACTION_CANDIDATE_SHEET_NAME,
        CORPORATE_ACTION_CANDIDATE_HEADERS,
        rows,
    )

    print(
        "株式分割等確認対象の出力が完了しました。"
        f"シート: {CORPORATE_ACTION_CANDIDATE_SHEET_NAME}, "
        f"件数: {len(rows):,}"
    )

    return len(rows)


def main() -> None:
    """認証情報を取得し、確認対象シートを更新する。"""

    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )
    service_account_json = get_required_environment_variable(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )
    sheets_service = create_google_sheets_service(
        service_account_json
    )

    export_corporate_action_candidates(
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
            "株式分割等確認対象の出力中に"
            "エラーが発生しました。",
            file=sys.stderr,
        )
        traceback.print_exc()
        print(
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
