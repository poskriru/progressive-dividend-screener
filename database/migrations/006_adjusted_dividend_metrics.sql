-- ============================================================
-- 006_adjusted_dividend_metrics.sql
--
-- 既存raw指標を変更せず、J-Quants AdjFactorで現在株式ベースへ
-- 補正した直近5期配当と累進配当判定を追加する。
-- ============================================================

CREATE VIEW screener.company_dividend_metrics_adjusted AS
WITH ranked_period_records AS (
    SELECT
        financials.annual_financial_id,
        financials.security_code,
        financials.fiscal_period_end,
        financials.annual_dividend_yen,
        documents.submitted_at,
        financials.extracted_at,
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
        fiscal_period_end,
        annual_dividend_yen
    FROM ranked_period_records
    WHERE period_record_rank = 1
),
ranked_history AS (
    SELECT
        selected_periods.*,
        ROW_NUMBER() OVER (
            PARTITION BY security_code
            ORDER BY fiscal_period_end DESC, annual_financial_id DESC
        ) AS recent_period_rank,
        LEAD(fiscal_period_end) OVER (
            PARTITION BY security_code
            ORDER BY fiscal_period_end DESC, annual_financial_id DESC
        ) AS previous_fiscal_period_end
    FROM selected_periods
),
recent_five_periods AS (
    SELECT *
    FROM ranked_history
    WHERE recent_period_rank <= 5
),
coverage AS (
    SELECT
        raw_metrics.security_code,
        sync_status.covered_from,
        sync_status.covered_to,
        CASE
            WHEN sync_status.sync_status = 'complete'
             AND sync_status.covered_from
                    <= raw_metrics.oldest_fiscal_period_end_5y
             AND sync_status.covered_to >= CURRENT_DATE
            THEN TRUE
            ELSE FALSE
        END AS range_is_complete,
        COUNT(actions.corporate_action_id) FILTER (
            WHERE actions.ex_right_type IS DISTINCT FROM '1'
              AND actions.ex_right_type IS DISTINCT FROM '2'
              AND actions.adjustment_factor <> 1
        ) AS unsupported_action_count
    FROM screener.company_dividend_metrics AS raw_metrics
    LEFT JOIN screener.jquants_adjustment_sync_status AS sync_status
        ON sync_status.security_code = raw_metrics.security_code
    LEFT JOIN screener.corporate_actions AS actions
        ON actions.security_code = raw_metrics.security_code
       AND actions.effective_date
            > raw_metrics.oldest_fiscal_period_end_5y
       AND actions.effective_date <= sync_status.covered_to
    GROUP BY
        raw_metrics.security_code,
        raw_metrics.oldest_fiscal_period_end_5y,
        sync_status.sync_status,
        sync_status.covered_from,
        sync_status.covered_to
),
adjusted_periods AS (
    SELECT
        periods.annual_financial_id,
        periods.security_code,
        periods.fiscal_period_end,
        periods.annual_dividend_yen,
        periods.recent_period_rank,
        periods.previous_fiscal_period_end,
        coverage.covered_from,
        coverage.covered_to,
        coverage.range_is_complete,
        coverage.unsupported_action_count,
        CASE
            WHEN coverage.range_is_complete
             AND coverage.unsupported_action_count = 0
            THEN COALESCE(
                EXP(SUM(LN(actions.adjustment_factor))),
                1::numeric
            )
            ELSE NULL::numeric
        END AS cumulative_adjustment_factor,
        CASE
            WHEN coverage.range_is_complete
             AND coverage.unsupported_action_count = 0
             AND periods.annual_dividend_yen IS NOT NULL
            THEN periods.annual_dividend_yen * COALESCE(
                EXP(SUM(LN(actions.adjustment_factor))),
                1::numeric
            )
            ELSE NULL::numeric
        END AS adjusted_annual_dividend_yen
    FROM recent_five_periods AS periods
    LEFT JOIN coverage
        ON coverage.security_code = periods.security_code
    LEFT JOIN screener.corporate_actions AS actions
        ON actions.security_code = periods.security_code
       AND actions.effective_date > periods.fiscal_period_end
       AND actions.effective_date <= coverage.covered_to
       AND actions.ex_right_type IN ('1', '2')
    GROUP BY
        periods.annual_financial_id,
        periods.security_code,
        periods.fiscal_period_end,
        periods.annual_dividend_yen,
        periods.recent_period_rank,
        periods.previous_fiscal_period_end,
        coverage.covered_from,
        coverage.covered_to,
        coverage.range_is_complete,
        coverage.unsupported_action_count
),
period_comparisons AS (
    SELECT
        adjusted_periods.*,
        LEAD(adjusted_annual_dividend_yen) OVER (
            PARTITION BY security_code
            ORDER BY fiscal_period_end DESC, annual_financial_id DESC
        ) AS previous_adjusted_dividend_yen
    FROM adjusted_periods
),
aggregated AS (
    SELECT
        security_code,
        MAX(covered_from) AS adjustment_covered_from,
        MAX(covered_to) AS adjustment_covered_to,
        BOOL_AND(range_is_complete) AS range_is_complete,
        MAX(unsupported_action_count) AS unsupported_action_count,
        COUNT(*) AS recent_period_count,
        COUNT(annual_dividend_yen) AS raw_dividend_period_count,
        COUNT(adjusted_annual_dividend_yen) AS adjusted_dividend_period_count,
        COUNT(*) FILTER (
            WHERE adjusted_annual_dividend_yen <= 0
        ) AS adjusted_non_positive_count,
        COUNT(*) FILTER (
            WHERE recent_period_rank <= 4
              AND adjusted_annual_dividend_yen
                    > previous_adjusted_dividend_yen
        ) AS adjusted_increase_count_5y,
        COUNT(*) FILTER (
            WHERE recent_period_rank <= 4
              AND adjusted_annual_dividend_yen
                    = previous_adjusted_dividend_yen
        ) AS adjusted_unchanged_count_5y,
        COUNT(*) FILTER (
            WHERE recent_period_rank <= 4
              AND adjusted_annual_dividend_yen
                    < previous_adjusted_dividend_yen
        ) AS adjusted_cut_count_5y,
        BOOL_AND(
            (fiscal_period_end - previous_fiscal_period_end)
                BETWEEN 300 AND 430
        ) FILTER (
            WHERE recent_period_rank <= 4
        ) AS has_regular_fiscal_periods_5y,
        MAX(adjusted_annual_dividend_yen) FILTER (
            WHERE recent_period_rank = 1
        ) AS latest_adjusted_annual_dividend_yen,
        MAX(adjusted_annual_dividend_yen) FILTER (
            WHERE recent_period_rank = 5
        ) AS oldest_adjusted_annual_dividend_yen_5y,
        MAX(cumulative_adjustment_factor) FILTER (
            WHERE recent_period_rank = 1
        ) AS latest_cumulative_adjustment_factor,
        MAX(cumulative_adjustment_factor) FILTER (
            WHERE recent_period_rank = 5
        ) AS oldest_cumulative_adjustment_factor_5y,
        ARRAY_AGG(
            fiscal_period_end ORDER BY fiscal_period_end
        ) AS adjusted_fiscal_periods_5y,
        ARRAY_AGG(
            cumulative_adjustment_factor ORDER BY fiscal_period_end
        ) AS cumulative_adjustment_factors_5y,
        ARRAY_AGG(
            adjusted_annual_dividend_yen ORDER BY fiscal_period_end
        ) AS adjusted_annual_dividends_yen_5y
    FROM period_comparisons
    GROUP BY security_code
)
SELECT
    securities.security_code,
    CASE
        WHEN aggregated.range_is_complete IS NOT TRUE
        THEN 'adjustment_data_incomplete'
        WHEN aggregated.unsupported_action_count > 0
        THEN 'unsupported_corporate_action'
        ELSE 'complete'
    END AS dividend_adjustment_status,
    (
        aggregated.range_is_complete IS TRUE
        AND aggregated.unsupported_action_count = 0
    ) AS is_adjustment_coverage_complete,
    aggregated.adjustment_covered_from,
    aggregated.adjustment_covered_to,
    aggregated.latest_cumulative_adjustment_factor,
    aggregated.oldest_cumulative_adjustment_factor_5y,
    aggregated.latest_adjusted_annual_dividend_yen,
    aggregated.oldest_adjusted_annual_dividend_yen_5y,
    aggregated.adjusted_increase_count_5y::integer,
    aggregated.adjusted_unchanged_count_5y::integer,
    aggregated.adjusted_cut_count_5y::integer,
    CASE
        WHEN aggregated.range_is_complete IS NOT TRUE
          OR aggregated.unsupported_action_count > 0
        THEN NULL::boolean
        WHEN aggregated.recent_period_count < 5
          OR aggregated.raw_dividend_period_count < 5
          OR aggregated.adjusted_dividend_period_count < 5
          OR aggregated.has_regular_fiscal_periods_5y IS DISTINCT FROM TRUE
        THEN NULL::boolean
        WHEN aggregated.adjusted_non_positive_count > 0
          OR aggregated.adjusted_cut_count_5y > 0
        THEN FALSE
        ELSE TRUE
    END AS is_progressive_dividend_5y_adjusted,
    CASE
        WHEN aggregated.range_is_complete IS NOT TRUE
        THEN 'adjustment_data_incomplete'
        WHEN aggregated.unsupported_action_count > 0
        THEN 'unsupported_corporate_action'
        WHEN aggregated.recent_period_count < 5
        THEN 'insufficient_history'
        WHEN aggregated.raw_dividend_period_count < 5
          OR aggregated.adjusted_dividend_period_count < 5
        THEN 'missing_dividend'
        WHEN aggregated.has_regular_fiscal_periods_5y IS DISTINCT FROM TRUE
        THEN 'irregular_fiscal_periods'
        WHEN aggregated.adjusted_non_positive_count > 0
        THEN 'non_positive_dividend'
        WHEN aggregated.adjusted_cut_count_5y > 0
        THEN 'dividend_cut'
        ELSE 'progressive'
    END AS progressive_dividend_status_5y_adjusted,
    CASE
        WHEN aggregated.range_is_complete IS NOT TRUE
          OR aggregated.unsupported_action_count > 0
          OR aggregated.recent_period_count < 5
          OR aggregated.adjusted_dividend_period_count < 5
          OR aggregated.has_regular_fiscal_periods_5y IS DISTINCT FROM TRUE
          OR aggregated.latest_adjusted_annual_dividend_yen <= 0
          OR aggregated.oldest_adjusted_annual_dividend_yen_5y <= 0
        THEN NULL::numeric
        ELSE ROUND(
            (
                POWER(
                    (
                        aggregated.latest_adjusted_annual_dividend_yen
                        / aggregated.oldest_adjusted_annual_dividend_yen_5y
                    )::double precision,
                    0.25::double precision
                ) - 1::double precision
            )::numeric * 100::numeric,
            6
        )
    END AS dividend_cagr_5y_adjusted_percent,
    aggregated.adjusted_fiscal_periods_5y,
    aggregated.cumulative_adjustment_factors_5y,
    aggregated.adjusted_annual_dividends_yen_5y
FROM screener.securities AS securities
LEFT JOIN aggregated
    ON aggregated.security_code = securities.security_code;

COMMENT ON VIEW screener.company_dividend_metrics_adjusted IS
'J-Quants AdjFactorによる現在株式ベースの直近5期配当と累進配当指標。取得範囲不足時は判定NULL。';

COMMENT ON COLUMN
    screener.company_dividend_metrics_adjusted.is_progressive_dividend_5y_adjusted
IS
'補正範囲が完全で、未対応アクションがなく、直近5期が正の配当かつ非減配の場合TRUE。raw判定の代用は禁止。';

REVOKE ALL ON screener.company_dividend_metrics_adjusted
FROM PUBLIC, anon, authenticated;


-- ============================================================
-- Sheets出力用統合VIEWへadjusted列を末尾追加する。
-- 004で定義した既存列の順序・意味は変更しない。
-- ============================================================

CREATE OR REPLACE VIEW screener.company_screener_with_dividends AS
SELECT
    screener_base.*,
    dividend_metrics.available_history_period_count,
    dividend_metrics.recent_period_count,
    dividend_metrics.dividend_period_count,
    dividend_metrics.latest_fiscal_period_end
        AS dividend_latest_fiscal_period_end,
    dividend_metrics.oldest_fiscal_period_end_5y,
    dividend_metrics.latest_annual_dividend_yen
        AS dividend_latest_annual_dividend_yen,
    dividend_metrics.previous_annual_dividend_yen,
    dividend_metrics.oldest_annual_dividend_yen_5y,
    dividend_metrics.dividend_increase_count_5y,
    dividend_metrics.dividend_unchanged_count_5y,
    dividend_metrics.dividend_cut_count_5y,
    dividend_metrics.consecutive_non_decrease_periods,
    dividend_metrics.consecutive_increase_periods,
    dividend_metrics.has_regular_fiscal_periods_5y,
    dividend_metrics.is_progressive_dividend_5y_raw,
    dividend_metrics.progressive_dividend_status_5y,
    dividend_metrics.dividend_cagr_5y_percent,
    dividend_metrics.fiscal_periods_5y,
    dividend_metrics.annual_dividends_yen_5y,
    adjusted.dividend_adjustment_status,
    adjusted.is_adjustment_coverage_complete,
    adjusted.adjustment_covered_from,
    adjusted.adjustment_covered_to,
    adjusted.latest_cumulative_adjustment_factor,
    adjusted.oldest_cumulative_adjustment_factor_5y,
    adjusted.latest_adjusted_annual_dividend_yen,
    adjusted.oldest_adjusted_annual_dividend_yen_5y,
    adjusted.adjusted_increase_count_5y,
    adjusted.adjusted_unchanged_count_5y,
    adjusted.adjusted_cut_count_5y,
    adjusted.is_progressive_dividend_5y_adjusted,
    adjusted.progressive_dividend_status_5y_adjusted,
    adjusted.dividend_cagr_5y_adjusted_percent,
    adjusted.adjusted_fiscal_periods_5y,
    adjusted.cumulative_adjustment_factors_5y,
    adjusted.adjusted_annual_dividends_yen_5y
FROM screener.company_screener_base AS screener_base
LEFT JOIN screener.company_dividend_metrics AS dividend_metrics
    ON dividend_metrics.security_code = screener_base.security_code
LEFT JOIN screener.company_dividend_metrics_adjusted AS adjusted
    ON adjusted.security_code = screener_base.security_code;

COMMENT ON VIEW screener.company_screener_with_dividends IS
'最新株価・最新年次財務・raw/adjusted累進配当指標を結合したGoogle Sheets出力用VIEW。';

REVOKE ALL ON screener.company_screener_with_dividends
FROM PUBLIC, anon, authenticated;
