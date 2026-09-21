"""
Discord累進配当候補検索用の軽量データアクセス処理。

Google Sheets出力やEDINET更新処理へ依存せず、
Discord Workerが必要とするPostgreSQL検索と
最新TDnet本文解析結果の付与だけを担当する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection
from enrich_tdnet_policy_candidates import (
    enrich_candidates_with_tdnet_policy_results,
)
from load_latest_tdnet_policy_results import (
    load_latest_tdnet_policy_results,
)


# ============================================================
# 検索条件
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


# ============================================================
# PostgreSQL候補検索
# ============================================================

def load_progressive_dividend_candidates(
    criteria: CandidateCriteria,
) -> list[dict[str, Any]]:
    """抽出条件に合う累進配当候補をランキング順で取得する。"""

    if not isinstance(criteria, CandidateCriteria):
        raise TypeError(
            "criteriaはCandidateCriteriaで"
            "指定してください。"
        )

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
        "discord_candidate_search"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                parameters,
            )

            records = [
                dict(row)
                for row in cursor.fetchall()
            ]

    security_codes = [
        str(record["security_code"])
        for record in records
    ]

    if len(security_codes) != len(
        set(security_codes)
    ):
        raise RuntimeError(
            "累進配当候補に証券コードの重複があります。"
        )

    print(
        "PostgreSQLからDiscord用の"
        "累進配当候補を取得しました。"
        f"件数: {len(records):,}"
    )

    return records


# ============================================================
# TDnet本文解析結果
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
        "TDnet PDF本文解析結果をDiscord候補へ"
        "反映しました。"
        f"取得件数: {len(policy_results)}, "
        f"completed: {completed_count}, "
        f"confirmed: {confirmed_count}"
    )

    return enriched_records
