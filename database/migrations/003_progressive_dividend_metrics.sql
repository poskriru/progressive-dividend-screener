-- ============================================================
-- 003_progressive_dividend_metrics.sql
--
-- EDINET年次財務に保存された年間配当履歴から、
-- 銘柄ごとの累進配当指標を算出するVIEWを作成する。
--
-- annual_dividend_yenは株式分割・株式併合による
-- 過年度調整を行っていないため、判定結果にはrawを付ける。
-- ============================================================

CREATE OR REPLACE VIEW
    screener.company_dividend_metrics
AS

WITH ranked_period_records AS (
    SELECT
        financials.annual_financial_id,
        financials.security_code,
        financials.doc_id,
        financials.fiscal_period_end,
        financials.annual_dividend_yen,
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
        annual_dividend_yen
    FROM ranked_period_records
    WHERE period_record_rank = 1
),

ranked_history AS (
    SELECT
        selected_periods.annual_financial_id,
        selected_periods.security_code,
        selected_periods.doc_id,
        selected_periods.fiscal_period_end,
        selected_periods.annual_dividend_yen,

        COUNT(*) OVER (
            PARTITION BY selected_periods.security_code
        ) AS available_history_period_count,

        ROW_NUMBER() OVER (
            PARTITION BY selected_periods.security_code
            ORDER BY
                selected_periods.fiscal_period_end DESC,
                selected_periods.annual_financial_id DESC
        ) AS recent_period_rank,

        LEAD(
            selected_periods.fiscal_period_end
        ) OVER (
            PARTITION BY selected_periods.security_code
            ORDER BY
                selected_periods.fiscal_period_end DESC,
                selected_periods.annual_financial_id DESC
        ) AS previous_fiscal_period_end,

        LEAD(
            selected_periods.annual_dividend_yen
        ) OVER (
            PARTITION BY selected_periods.security_code
            ORDER BY
                selected_periods.fiscal_period_end DESC,
                selected_periods.annual_financial_id DESC
        ) AS previous_dividend_yen

    FROM selected_periods
),

recent_five_periods AS (
    SELECT
        annual_financial_id,
        security_code,
        doc_id,
        fiscal_period_end,
        annual_dividend_yen,
        available_history_period_count,
        recent_period_rank,
        previous_fiscal_period_end,
        previous_dividend_yen
    FROM ranked_history
    WHERE recent_period_rank <= 5
),

aggregated_metrics AS (
    SELECT
        security_code,

        MAX(
            available_history_period_count
        ) AS available_history_period_count,

        COUNT(*) AS recent_period_count,

        COUNT(
            annual_dividend_yen
        ) AS dividend_period_count,

        COUNT(*) FILTER (
            WHERE annual_dividend_yen <= 0
        ) AS non_positive_dividend_period_count,

        COUNT(*) FILTER (
            WHERE recent_period_rank <= 4
              AND annual_dividend_yen
                    > previous_dividend_yen
        ) AS dividend_increase_count_5y,

        COUNT(*) FILTER (
            WHERE recent_period_rank <= 4
              AND annual_dividend_yen
                    = previous_dividend_yen
        ) AS dividend_unchanged_count_5y,

        COUNT(*) FILTER (
            WHERE recent_period_rank <= 4
              AND annual_dividend_yen
                    < previous_dividend_yen
        ) AS dividend_cut_count_5y,

        BOOL_AND(
            (
                fiscal_period_end
                - previous_fiscal_period_end
            ) BETWEEN 300 AND 430
        ) FILTER (
            WHERE recent_period_rank <= 4
        ) AS has_regular_fiscal_periods_5y,

        MAX(
            annual_dividend_yen
        ) FILTER (
            WHERE recent_period_rank = 1
        ) AS latest_annual_dividend_yen,

        MAX(
            annual_dividend_yen
        ) FILTER (
            WHERE recent_period_rank = 2
        ) AS previous_annual_dividend_yen,

        MAX(
            annual_dividend_yen
        ) FILTER (
            WHERE recent_period_rank = 5
        ) AS oldest_annual_dividend_yen_5y,

        MAX(
            fiscal_period_end
        ) FILTER (
            WHERE recent_period_rank = 1
        ) AS latest_fiscal_period_end,

        MAX(
            fiscal_period_end
        ) FILTER (
            WHERE recent_period_rank = 5
        ) AS oldest_fiscal_period_end_5y,

        MIN(
            recent_period_rank
        ) FILTER (
            WHERE recent_period_rank <= 4
              AND (
                  annual_dividend_yen IS NULL
                  OR previous_dividend_yen IS NULL
                  OR annual_dividend_yen
                        < previous_dividend_yen
              )
        ) AS first_non_decrease_failure_rank,

        MIN(
            recent_period_rank
        ) FILTER (
            WHERE recent_period_rank <= 4
              AND (
                  annual_dividend_yen IS NULL
                  OR previous_dividend_yen IS NULL
                  OR annual_dividend_yen
                        <= previous_dividend_yen
              )
        ) AS first_increase_failure_rank,

        ARRAY_AGG(
            fiscal_period_end
            ORDER BY fiscal_period_end
        ) AS fiscal_periods_5y,

        ARRAY_AGG(
            annual_dividend_yen
            ORDER BY fiscal_period_end
        ) AS annual_dividends_yen_5y

    FROM recent_five_periods

    GROUP BY security_code
),

calculated_metrics AS (
    SELECT
        aggregated_metrics.security_code,
        aggregated_metrics.available_history_period_count,
        aggregated_metrics.recent_period_count,
        aggregated_metrics.dividend_period_count,
        aggregated_metrics.non_positive_dividend_period_count,
        aggregated_metrics.dividend_increase_count_5y,
        aggregated_metrics.dividend_unchanged_count_5y,
        aggregated_metrics.dividend_cut_count_5y,
        aggregated_metrics.has_regular_fiscal_periods_5y,
        aggregated_metrics.latest_annual_dividend_yen,
        aggregated_metrics.previous_annual_dividend_yen,
        aggregated_metrics.oldest_annual_dividend_yen_5y,
        aggregated_metrics.latest_fiscal_period_end,
        aggregated_metrics.oldest_fiscal_period_end_5y,
        aggregated_metrics.fiscal_periods_5y,
        aggregated_metrics.annual_dividends_yen_5y,

        CASE
            WHEN
                aggregated_metrics.dividend_period_count
                = aggregated_metrics.recent_period_count
            THEN COALESCE(
                aggregated_metrics
                    .first_non_decrease_failure_rank,
                LEAST(
                    aggregated_metrics.recent_period_count,
                    5
                )
            )::integer
            ELSE NULL::integer
        END AS consecutive_non_decrease_periods,

        CASE
            WHEN
                aggregated_metrics.dividend_period_count
                = aggregated_metrics.recent_period_count
            THEN COALESCE(
                aggregated_metrics.first_increase_failure_rank,
                LEAST(
                    aggregated_metrics.recent_period_count,
                    5
                )
            )::integer
            ELSE NULL::integer
        END AS consecutive_increase_periods,

        CASE
            WHEN aggregated_metrics.recent_period_count < 5
            THEN NULL::boolean

            WHEN aggregated_metrics.dividend_period_count < 5
            THEN NULL::boolean

            WHEN
                aggregated_metrics.has_regular_fiscal_periods_5y
                IS DISTINCT FROM TRUE
            THEN NULL::boolean

            WHEN
                aggregated_metrics
                    .non_positive_dividend_period_count > 0
            THEN FALSE

            WHEN aggregated_metrics.dividend_cut_count_5y > 0
            THEN FALSE

            ELSE TRUE
        END AS is_progressive_dividend_5y_raw,

        CASE
            WHEN aggregated_metrics.recent_period_count < 5
            THEN 'insufficient_history'

            WHEN aggregated_metrics.dividend_period_count < 5
            THEN 'missing_dividend'

            WHEN
                aggregated_metrics.has_regular_fiscal_periods_5y
                IS DISTINCT FROM TRUE
            THEN 'irregular_fiscal_periods'

            WHEN
                aggregated_metrics
                    .non_positive_dividend_period_count > 0
            THEN 'non_positive_dividend'

            WHEN aggregated_metrics.dividend_cut_count_5y > 0
            THEN 'dividend_cut'

            ELSE 'progressive'
        END AS progressive_dividend_status_5y,

        CASE
            WHEN aggregated_metrics.recent_period_count < 5
            THEN NULL::numeric

            WHEN aggregated_metrics.dividend_period_count < 5
            THEN NULL::numeric

            WHEN
                aggregated_metrics.has_regular_fiscal_periods_5y
                IS DISTINCT FROM TRUE
            THEN NULL::numeric

            WHEN
                aggregated_metrics.latest_annual_dividend_yen <= 0
            THEN NULL::numeric

            WHEN
                aggregated_metrics.oldest_annual_dividend_yen_5y <= 0
            THEN NULL::numeric

            ELSE ROUND(
                (
                    POWER(
                        (
                            aggregated_metrics
                                .latest_annual_dividend_yen
                            / aggregated_metrics
                                .oldest_annual_dividend_yen_5y
                        )::double precision,
                        0.25::double precision
                    )
                    - 1::double precision
                )::numeric
                * 100::numeric,
                6
            )
        END AS dividend_cagr_5y_percent

    FROM aggregated_metrics
)

SELECT
    securities.security_code,
    securities.company_name,

    COALESCE(
        calculated_metrics.available_history_period_count,
        0
    )::integer AS available_history_period_count,

    COALESCE(
        calculated_metrics.recent_period_count,
        0
    )::integer AS recent_period_count,

    COALESCE(
        calculated_metrics.dividend_period_count,
        0
    )::integer AS dividend_period_count,

    calculated_metrics.latest_fiscal_period_end,
    calculated_metrics.oldest_fiscal_period_end_5y,
    calculated_metrics.latest_annual_dividend_yen,
    calculated_metrics.previous_annual_dividend_yen,
    calculated_metrics.oldest_annual_dividend_yen_5y,

    calculated_metrics.dividend_increase_count_5y::integer
        AS dividend_increase_count_5y,

    calculated_metrics.dividend_unchanged_count_5y::integer
        AS dividend_unchanged_count_5y,

    calculated_metrics.dividend_cut_count_5y::integer
        AS dividend_cut_count_5y,

    calculated_metrics.consecutive_non_decrease_periods,
    calculated_metrics.consecutive_increase_periods,
    calculated_metrics.has_regular_fiscal_periods_5y,
    calculated_metrics.is_progressive_dividend_5y_raw,
    calculated_metrics.progressive_dividend_status_5y,
    calculated_metrics.dividend_cagr_5y_percent,
    calculated_metrics.fiscal_periods_5y,
    calculated_metrics.annual_dividends_yen_5y

FROM screener.securities AS securities

LEFT JOIN calculated_metrics
    ON calculated_metrics.security_code
        = securities.security_code;


-- ============================================================
-- VIEWコメント
-- ============================================================

COMMENT ON VIEW screener.company_dividend_metrics IS
'EDINET年間配当履歴から算出した銘柄別累進配当指標。株式分割・併合は未調整。';

COMMENT ON COLUMN
    screener.company_dividend_metrics
        .is_progressive_dividend_5y_raw
IS
'直近5期がすべて正の配当かつ非減配の場合TRUE。履歴不足・欠損・決算期間不整合はNULL。株式分割・併合は未調整。';

COMMENT ON COLUMN
    screener.company_dividend_metrics
        .progressive_dividend_status_5y
IS
'5期累進配当判定の状態。progressive、dividend_cut、non_positive_dividend、missing_dividend、irregular_fiscal_periods、insufficient_history。';

COMMENT ON COLUMN
    screener.company_dividend_metrics
        .dividend_cagr_5y_percent
IS
'直近5期の最古配当から最新配当までの4年間に対する年平均成長率。';


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL
ON screener.company_dividend_metrics
FROM PUBLIC;

REVOKE ALL
ON screener.company_dividend_metrics
FROM anon, authenticated;
