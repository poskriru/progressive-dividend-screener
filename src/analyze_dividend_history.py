"""
PostgreSQLに保存された年間配当履歴を分析する。

累進配当指標を実装する前に、
配当データの件数、欠損、重複、5期分の取得状況を確認する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import json
import sys
import traceback
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import (
    create_database_connection,
    verify_required_tables,
)


# ============================================================
# 定数
# ============================================================

REQUIRED_TABLES = {
    "annual_financials",
    "edinet_documents",
    "securities",
}


# ============================================================
# JSON出力
# ============================================================

def print_json(
    label: str,
    value: Any,
) -> None:
    """
    診断結果をJSON形式で出力する。
    """

    print(
        f"{label}: "
        + json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )
    )


# ============================================================
# 基本集計
# ============================================================

def load_summary(
    connection: Any,
) -> dict[str, Any]:
    """
    年次財務と年間配当の基本件数を取得する。
    """

    query = """
        SELECT
            COUNT(*) AS annual_financial_count,
            COUNT(DISTINCT security_code)
                AS financial_security_count,
            COUNT(*) FILTER (
                WHERE extraction_status = '成功'
            ) AS successful_financial_count,
            COUNT(*) FILTER (
                WHERE annual_dividend_yen IS NOT NULL
            ) AS dividend_record_count,
            COUNT(DISTINCT security_code) FILTER (
                WHERE annual_dividend_yen IS NOT NULL
            ) AS dividend_security_count,
            COUNT(*) FILTER (
                WHERE annual_dividend_yen = 0
            ) AS zero_dividend_count,
            COUNT(*) FILTER (
                WHERE annual_dividend_yen < 0
            ) AS negative_dividend_count
        FROM screener.annual_financials;
    """

    with connection.cursor() as cursor:
        cursor.execute(query)
        result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "年間配当の基本集計を取得できませんでした。"
        )

    return dict(result)


# ============================================================
# 同一決算期の重複確認
# ============================================================

def load_duplicate_period_summary(
    connection: Any,
) -> dict[str, Any]:
    """
    同一銘柄・同一決算期の複数書類を確認する。
    """

    query = """
        WITH duplicate_periods AS (
            SELECT
                security_code,
                fiscal_period_end,
                COUNT(*) AS record_count
            FROM screener.annual_financials
            GROUP BY
                security_code,
                fiscal_period_end
            HAVING COUNT(*) > 1
        )
        SELECT
            COUNT(*) AS duplicate_period_count,
            COALESCE(
                SUM(record_count),
                0
            ) AS duplicate_record_count,
            COUNT(DISTINCT security_code)
                AS affected_security_count
        FROM duplicate_periods;
    """

    with connection.cursor() as cursor:
        cursor.execute(query)
        result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "決算期重複の集計を取得できませんでした。"
        )

    return dict(result)


# ============================================================
# 配当履歴年数の分布
# ============================================================

def load_history_coverage(
    connection: Any,
) -> list[dict[str, Any]]:
    """
    銘柄ごとの取得済み配当履歴年数を集計する。

    同一銘柄・同一決算期に複数書類がある場合は、
    提出日時などが最新の書類を採用する。
    """

    query = """
        WITH ranked_periods AS (
            SELECT
                financials.security_code,
                financials.fiscal_period_end,
                financials.annual_dividend_yen,
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
        ),

        selected_periods AS (
            SELECT
                security_code,
                fiscal_period_end,
                annual_dividend_yen
            FROM ranked_periods
            WHERE period_record_rank = 1
        ),

        security_coverage AS (
            SELECT
                security_code,
                COUNT(*) AS financial_period_count,
                COUNT(annual_dividend_yen)
                    AS dividend_period_count
            FROM selected_periods
            GROUP BY security_code
        )

        SELECT
            dividend_period_count,
            COUNT(*) AS security_count
        FROM security_coverage
        GROUP BY dividend_period_count
        ORDER BY dividend_period_count;
    """

    with connection.cursor() as cursor:
        cursor.execute(query)

        return [
            dict(row)
            for row in cursor.fetchall()
        ]


# ============================================================
# 5期累進配当候補
# ============================================================

def load_progressive_candidates(
    connection: Any,
) -> list[dict[str, Any]]:
    """
    直近5期の配当がすべて取得でき、
    5期を通じて非減配の銘柄候補を取得する。

    この結果は診断用であり、まだ本番判定には使用しない。
    """

    query = """
        WITH ranked_period_records AS (
            SELECT
                financials.security_code,
                financials.fiscal_period_end,
                financials.annual_dividend_yen,
                documents.submitted_at,
                financials.extracted_at,
                financials.annual_financial_id,
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
        ),

        selected_periods AS (
            SELECT
                security_code,
                fiscal_period_end,
                annual_dividend_yen
            FROM ranked_period_records
            WHERE period_record_rank = 1
        ),

        ranked_history AS (
            SELECT
                security_code,
                fiscal_period_end,
                annual_dividend_yen,
                ROW_NUMBER() OVER (
                    PARTITION BY security_code
                    ORDER BY fiscal_period_end DESC
                ) AS recent_period_rank,
                LEAD(annual_dividend_yen) OVER (
                    PARTITION BY security_code
                    ORDER BY fiscal_period_end DESC
                ) AS previous_dividend_yen
            FROM selected_periods
        ),

        candidate_summary AS (
            SELECT
                history.security_code,
                securities.company_name,
                COUNT(*) AS period_count,
                COUNT(history.annual_dividend_yen)
                    AS dividend_period_count,
                BOOL_AND(
                    CASE
                        WHEN history.recent_period_rank <= 4
                        THEN
                            history.annual_dividend_yen
                            >= history.previous_dividend_yen
                        ELSE TRUE
                    END
                ) AS is_non_decreasing,
                ARRAY_AGG(
                    history.fiscal_period_end
                    ORDER BY history.fiscal_period_end
                ) AS fiscal_periods,
                ARRAY_AGG(
                    history.annual_dividend_yen
                    ORDER BY history.fiscal_period_end
                ) AS annual_dividends
            FROM ranked_history AS history
            INNER JOIN screener.securities AS securities
                ON securities.security_code
                    = history.security_code
            WHERE history.recent_period_rank <= 5
            GROUP BY
                history.security_code,
                securities.company_name
        )

        SELECT
            security_code,
            company_name,
            fiscal_periods,
            annual_dividends
        FROM candidate_summary
        WHERE period_count = 5
          AND dividend_period_count = 5
          AND is_non_decreasing = TRUE
        ORDER BY security_code
        LIMIT 50;
    """

    with connection.cursor() as cursor:
        cursor.execute(query)

        return [
            dict(row)
            for row in cursor.fetchall()
        ]


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    配当履歴の診断を実行する。
    """

    print("年間配当履歴の診断を開始します。")

    with create_database_connection(
        "analyze-dividend-history"
    ) as connection:
        verify_required_tables(
            connection,
            REQUIRED_TABLES,
        )

        summary = load_summary(connection)

        duplicate_summary = (
            load_duplicate_period_summary(
                connection
            )
        )

        history_coverage = load_history_coverage(
            connection
        )

        progressive_candidates = (
            load_progressive_candidates(
                connection
            )
        )

    print_json(
        "配当基本集計",
        summary,
    )

    print_json(
        "同一決算期重複集計",
        duplicate_summary,
    )

    print_json(
        "配当履歴年数分布",
        history_coverage,
    )

    print_json(
        "5期累進配当候補件数",
        {
            "candidate_count_in_output": len(
                progressive_candidates
            ),
            "output_limit": 50,
        },
    )

    for candidate in progressive_candidates:
        print_json(
            "5期累進配当候補",
            candidate,
        )

    print("年間配当履歴の診断が完了しました。")


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            "年間配当履歴の診断中に"
            "エラーが発生しました。",
            file=sys.stderr,
        )

        traceback.print_exc()

        print(
            (
                f"{type(error).__name__}: "
                f"{error}"
            ),
            file=sys.stderr,
        )

        sys.exit(1)
