-- ============================================================
-- 011_discord_candidate_search_cache.sql
--
-- Discordの都度検索で複雑なVIEW全体を再計算しないよう、
-- 検索対象となる累進配当候補をMaterialized Viewへ保存する。
-- ============================================================

CREATE MATERIALIZED VIEW
    screener.discord_candidate_search_cache
AS

SELECT
    annual_financial_id,
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
  AND is_progressive_dividend_5y_adjusted IS TRUE;


-- ============================================================
-- CONCURRENTLY更新に必要な一意INDEX
-- ============================================================

CREATE UNIQUE INDEX
    discord_candidate_search_cache_security_code_uq
ON screener.discord_candidate_search_cache (
    security_code
);


-- ============================================================
-- ランキング検索用INDEX
-- ============================================================

CREATE INDEX
    discord_candidate_search_cache_ranking_idx
ON screener.discord_candidate_search_cache (
    dividend_yield_percent DESC,
    dividend_cagr_5y_adjusted_percent DESC,
    roe_percent DESC,
    security_code
);


-- ============================================================
-- 統計情報
-- ============================================================

ANALYZE screener.discord_candidate_search_cache;


-- ============================================================
-- コメント
-- ============================================================

COMMENT ON MATERIALIZED VIEW
    screener.discord_candidate_search_cache
IS
'Discord累進配当候補検索用の定期更新キャッシュ。';


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL
ON screener.discord_candidate_search_cache
FROM PUBLIC;

REVOKE ALL
ON screener.discord_candidate_search_cache
FROM anon, authenticated;
