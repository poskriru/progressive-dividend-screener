-- ============================================================
-- 016_screener_valuation_efficiency_metrics.sql
--
-- PER割安度（年）と投資効率（10年）を算出するため、
-- 年次財務へB/S詳細項目の列を追加し、
-- スクリーナーVIEWへ新指標を追加する。
-- ============================================================

ALTER TABLE screener.annual_financials
    ADD COLUMN IF NOT EXISTS current_assets_jpy
        numeric(28, 6),
    ADD COLUMN IF NOT EXISTS investment_securities_jpy
        numeric(28, 6),
    ADD COLUMN IF NOT EXISTS liabilities_jpy
        numeric(28, 6),
    ADD COLUMN IF NOT EXISTS treasury_shares
        bigint,
    ADD COLUMN IF NOT EXISTS current_assets_element_id
        text,
    ADD COLUMN IF NOT EXISTS investment_securities_element_id
        text,
    ADD COLUMN IF NOT EXISTS liabilities_element_id
        text,
    ADD COLUMN IF NOT EXISTS treasury_shares_element_id
        text;


-- ============================================================
-- 依存VIEWを一度削除する
--
-- スクリーナー基礎VIEWの列は途中に追加されるため、
-- CREATE OR REPLACEでの列順維持ができない。
-- 依存する統合VIEW・検索キャッシュ・基礎VIEW自体を
-- 一度削除してから作り直す。
-- ============================================================

DROP MATERIALIZED VIEW
    screener.discord_candidate_search_cache;

DROP VIEW
    screener.company_screener_with_dividends;

DROP VIEW
    screener.company_screener_base;


-- ============================================================
-- スクリーナー基礎VIEWの再作成
-- ============================================================

CREATE VIEW screener.company_screener_base AS

WITH ranked_prices AS (
    SELECT
        prices.daily_price_id,
        prices.security_code,
        prices.trading_date,
        prices.trading_unit,
        prices.open_price,
        prices.high_price,
        prices.low_price,
        prices.close_price,
        prices.previous_close,
        prices.price_change,
        prices.change_rate_percent,
        prices.final_quote,
        prices.vwap,
        prices.volume,
        prices.turnover_thousand_yen,
        prices.adjustment_factor,
        prices.adjusted_close_price,
        prices.price_status,
        prices.source AS price_source,
        prices.source_updated_at AS price_source_updated_at,
        prices.fetched_at AS price_fetched_at,
        ROW_NUMBER() OVER (
            PARTITION BY prices.security_code
            ORDER BY
                prices.trading_date DESC,
                prices.updated_at DESC,
                prices.daily_price_id DESC
        ) AS price_rank
    FROM screener.daily_prices AS prices
    WHERE prices.close_price IS NOT NULL
),

latest_prices AS (
    SELECT
        ranked_prices.daily_price_id,
        ranked_prices.security_code,
        ranked_prices.trading_date,
        ranked_prices.trading_unit,
        ranked_prices.open_price,
        ranked_prices.high_price,
        ranked_prices.low_price,
        ranked_prices.close_price,
        ranked_prices.previous_close,
        ranked_prices.price_change,
        ranked_prices.change_rate_percent,
        ranked_prices.final_quote,
        ranked_prices.vwap,
        ranked_prices.volume,
        ranked_prices.turnover_thousand_yen,
        ranked_prices.adjustment_factor,
        ranked_prices.adjusted_close_price,
        ranked_prices.price_status,
        ranked_prices.price_source,
        ranked_prices.price_source_updated_at,
        ranked_prices.price_fetched_at,
        ranked_prices.price_rank
    FROM ranked_prices
    WHERE ranked_prices.price_rank = 1
),

ranked_financials AS (
    SELECT
        financials.annual_financial_id,
        financials.security_code,
        financials.doc_id,
        financials.fiscal_period_start,
        financials.fiscal_period_end,
        financials.accounting_standard,
        financials.is_consolidated,
        financials.revenue_jpy,
        financials.operating_profit_jpy,
        financials.ordinary_profit_jpy,
        financials.net_income_jpy,
        financials.total_assets_jpy,
        financials.net_assets_jpy,
        financials.equity_jpy,
        financials.current_assets_jpy,
        financials.investment_securities_jpy,
        financials.liabilities_jpy,
        financials.treasury_shares,
        financials.operating_cash_flow_jpy,
        financials.investing_cash_flow_jpy,
        financials.financing_cash_flow_jpy,
        financials.cash_and_equivalents_jpy,
        financials.eps_yen,
        financials.bps_yen,
        financials.annual_dividend_yen,
        financials.issued_shares,
        financials.extracted_item_count,
        financials.extraction_status,
        financials.extraction_error,
        financials.source AS financial_source,
        financials.source_url AS financial_source_url,
        financials.extracted_at,
        documents.submitted_at,
        ROW_NUMBER() OVER (
            PARTITION BY financials.security_code
            ORDER BY
                financials.fiscal_period_end DESC,
                documents.submitted_at DESC NULLS LAST,
                financials.extracted_at DESC NULLS LAST,
                financials.annual_financial_id DESC
        ) AS financial_rank
    FROM screener.annual_financials AS financials
    LEFT JOIN screener.edinet_documents AS documents
        ON documents.doc_id = financials.doc_id
),

latest_financials AS (
    SELECT
        ranked_financials.annual_financial_id,
        ranked_financials.security_code,
        ranked_financials.doc_id,
        ranked_financials.fiscal_period_start,
        ranked_financials.fiscal_period_end,
        ranked_financials.accounting_standard,
        ranked_financials.is_consolidated,
        ranked_financials.revenue_jpy,
        ranked_financials.operating_profit_jpy,
        ranked_financials.ordinary_profit_jpy,
        ranked_financials.net_income_jpy,
        ranked_financials.total_assets_jpy,
        ranked_financials.net_assets_jpy,
        ranked_financials.equity_jpy,
        ranked_financials.current_assets_jpy,
        ranked_financials.investment_securities_jpy,
        ranked_financials.liabilities_jpy,
        ranked_financials.treasury_shares,
        ranked_financials.operating_cash_flow_jpy,
        ranked_financials.investing_cash_flow_jpy,
        ranked_financials.financing_cash_flow_jpy,
        ranked_financials.cash_and_equivalents_jpy,
        ranked_financials.eps_yen,
        ranked_financials.bps_yen,
        ranked_financials.annual_dividend_yen,
        ranked_financials.issued_shares,
        ranked_financials.extracted_item_count,
        ranked_financials.extraction_status,
        ranked_financials.extraction_error,
        ranked_financials.financial_source,
        ranked_financials.financial_source_url,
        ranked_financials.extracted_at,
        ranked_financials.submitted_at,
        ranked_financials.financial_rank
    FROM ranked_financials
    WHERE ranked_financials.financial_rank = 1
),

combined_data AS (
    SELECT
        securities.security_code,
        securities.company_name,
        securities.market,
        securities.market_category,
        securities.industry_33_code,
        securities.industry_33_name,
        securities.industry_17_code,
        securities.industry_17_name,
        securities.scale_code,
        securities.scale_name,
        securities.reference_date,
        securities.is_active,

        latest_prices.daily_price_id,
        latest_prices.trading_date,
        latest_prices.trading_unit,
        latest_prices.open_price,
        latest_prices.high_price,
        latest_prices.low_price,
        latest_prices.close_price,
        latest_prices.previous_close,
        latest_prices.price_change,
        latest_prices.change_rate_percent,
        latest_prices.final_quote,
        latest_prices.vwap,
        latest_prices.volume,
        latest_prices.turnover_thousand_yen,
        latest_prices.adjustment_factor,
        latest_prices.adjusted_close_price,
        latest_prices.price_status,
        latest_prices.price_source,
        latest_prices.price_source_updated_at,
        latest_prices.price_fetched_at,

        latest_financials.annual_financial_id,
        latest_financials.doc_id,
        latest_financials.submitted_at,
        latest_financials.fiscal_period_start,
        latest_financials.fiscal_period_end,
        latest_financials.accounting_standard,
        latest_financials.is_consolidated,
        latest_financials.revenue_jpy,
        latest_financials.operating_profit_jpy,
        latest_financials.ordinary_profit_jpy,
        latest_financials.net_income_jpy,
        latest_financials.total_assets_jpy,
        latest_financials.net_assets_jpy,
        latest_financials.equity_jpy,
        latest_financials.current_assets_jpy,
        latest_financials.investment_securities_jpy,
        latest_financials.liabilities_jpy,
        latest_financials.treasury_shares,
        latest_financials.operating_cash_flow_jpy,
        latest_financials.investing_cash_flow_jpy,
        latest_financials.financing_cash_flow_jpy,
        latest_financials.cash_and_equivalents_jpy,
        latest_financials.eps_yen,
        latest_financials.bps_yen,
        latest_financials.annual_dividend_yen,
        latest_financials.issued_shares,
        latest_financials.extracted_item_count,
        latest_financials.extraction_status,
        latest_financials.extraction_error,
        latest_financials.financial_source,
        latest_financials.financial_source_url,
        latest_financials.extracted_at
    FROM screener.securities AS securities
    LEFT JOIN latest_prices
        ON latest_prices.security_code = securities.security_code
    LEFT JOIN latest_financials
        ON latest_financials.security_code = securities.security_code
    WHERE securities.is_active = TRUE
)

SELECT
    security_code,
    company_name,
    market,
    market_category,
    industry_33_code,
    industry_33_name,
    industry_17_code,
    industry_17_name,
    scale_code,
    scale_name,
    reference_date,
    is_active,

    daily_price_id,
    trading_date,
    trading_unit,
    open_price,
    high_price,
    low_price,
    close_price,
    previous_close,
    price_change,
    change_rate_percent,
    final_quote,
    vwap,
    volume,
    turnover_thousand_yen,
    adjustment_factor,
    adjusted_close_price,
    price_status,
    price_source,
    price_source_updated_at,
    price_fetched_at,

    annual_financial_id,
    doc_id,
    submitted_at,
    fiscal_period_start,
    fiscal_period_end,
    accounting_standard,
    is_consolidated,
    revenue_jpy,
    operating_profit_jpy,
    ordinary_profit_jpy,
    net_income_jpy,
    total_assets_jpy,
    net_assets_jpy,
    equity_jpy,
    current_assets_jpy,
    investment_securities_jpy,
    liabilities_jpy,
    treasury_shares,
    operating_cash_flow_jpy,
    investing_cash_flow_jpy,
    financing_cash_flow_jpy,
    cash_and_equivalents_jpy,
    eps_yen,
    bps_yen,
    annual_dividend_yen,
    issued_shares,
    extracted_item_count,
    extraction_status,
    extraction_error,
    financial_source,
    financial_source_url,
    extracted_at,

    CASE
        WHEN close_price IS NOT NULL
         AND issued_shares IS NOT NULL
        THEN close_price * issued_shares::NUMERIC
        ELSE NULL::NUMERIC
    END AS market_capitalization_jpy,

    CASE
        WHEN close_price IS NOT NULL
         AND issued_shares IS NOT NULL
        THEN close_price * issued_shares::NUMERIC / 1000000::NUMERIC
        ELSE NULL::NUMERIC
    END AS market_capitalization_million_yen,

    CASE
        WHEN close_price IS NOT NULL
         AND eps_yen > 0::NUMERIC
        THEN close_price / NULLIF(eps_yen, 0::NUMERIC)
        ELSE NULL::NUMERIC
    END AS per_ratio,

    CASE
        WHEN close_price IS NOT NULL
         AND bps_yen > 0::NUMERIC
        THEN close_price / NULLIF(bps_yen, 0::NUMERIC)
        ELSE NULL::NUMERIC
    END AS pbr_ratio,

    CASE
        WHEN equity_jpy > 0::NUMERIC
         AND net_income_jpy IS NOT NULL
        THEN net_income_jpy
             / NULLIF(equity_jpy, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS roe_percent,

    CASE
        WHEN total_assets_jpy > 0::NUMERIC
         AND net_income_jpy IS NOT NULL
        THEN net_income_jpy
             / NULLIF(total_assets_jpy, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS roa_percent,

    CASE
        WHEN total_assets_jpy > 0::NUMERIC
         AND equity_jpy IS NOT NULL
        THEN equity_jpy
             / NULLIF(total_assets_jpy, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS equity_ratio_percent,

    CASE
        WHEN revenue_jpy <> 0::NUMERIC
         AND operating_profit_jpy IS NOT NULL
        THEN operating_profit_jpy
             / NULLIF(revenue_jpy, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS operating_profit_margin_percent,

    CASE
        WHEN revenue_jpy <> 0::NUMERIC
         AND net_income_jpy IS NOT NULL
        THEN net_income_jpy
             / NULLIF(revenue_jpy, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS net_income_margin_percent,

    CASE
        WHEN close_price > 0::NUMERIC
         AND annual_dividend_yen IS NOT NULL
        THEN annual_dividend_yen
             / NULLIF(close_price, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS dividend_yield_percent,

    CASE
        WHEN eps_yen > 0::NUMERIC
         AND annual_dividend_yen IS NOT NULL
        THEN annual_dividend_yen
             / NULLIF(eps_yen, 0::NUMERIC)
             * 100::NUMERIC
        ELSE NULL::NUMERIC
    END AS payout_ratio_percent,

    CASE
        WHEN operating_cash_flow_jpy IS NOT NULL
         AND investing_cash_flow_jpy IS NOT NULL
        THEN operating_cash_flow_jpy + investing_cash_flow_jpy
        ELSE NULL::NUMERIC
    END AS free_cash_flow_jpy,

    -- ========================================================
    -- 自社株保有比率（%）
    --
    -- 株数ベース: 自己株式数 ÷ 発行済株式総数 × 100。
    -- 自己株式数が抽出できない場合は0%扱いとする。
    -- ========================================================

    CASE
        WHEN issued_shares > 0
         AND treasury_shares IS NOT NULL
        THEN treasury_shares::NUMERIC
             / NULLIF(issued_shares::NUMERIC, 0::NUMERIC)
             * 100::NUMERIC
        ELSE 0::NUMERIC
    END AS treasury_shares_ratio_percent,

    -- ========================================================
    -- ネットキャッシュ（円）
    --
    -- (流動資産 + 投資有価証券 × 70%) - 負債合計。
    -- ========================================================

    CASE
        WHEN current_assets_jpy IS NOT NULL
         AND investment_securities_jpy IS NOT NULL
         AND liabilities_jpy IS NOT NULL
        THEN current_assets_jpy
             + investment_securities_jpy * 0.7::NUMERIC
             - liabilities_jpy
        ELSE NULL::NUMERIC
    END AS net_cash_jpy,

    -- ========================================================
    -- PER割安度（年）
    --
    -- (時価総額 × (100% - 自社株保有比率%) - ネットキャッシュ)
    -- ÷ 当期純利益。
    -- 当期純利益が正の銘柄だけを対象とする。
    -- ========================================================

    CASE
        WHEN close_price IS NOT NULL
         AND issued_shares > 0
         AND current_assets_jpy IS NOT NULL
         AND investment_securities_jpy IS NOT NULL
         AND liabilities_jpy IS NOT NULL
         AND net_income_jpy > 0::NUMERIC
        THEN (
            close_price
            * (issued_shares::NUMERIC
               - COALESCE(treasury_shares, 0)::NUMERIC)
            - (current_assets_jpy
               + investment_securities_jpy * 0.7::NUMERIC
               - liabilities_jpy)
        ) / net_income_jpy
        ELSE NULL::NUMERIC
    END AS per_undervaluation_years,

    -- ========================================================
    -- 投資効率（10年）
    --
    -- (EPS × 10 + BPS) ÷ 株価。
    -- ========================================================

    CASE
        WHEN close_price > 0::NUMERIC
         AND eps_yen IS NOT NULL
         AND bps_yen IS NOT NULL
        THEN (eps_yen * 10::NUMERIC + bps_yen)
             / close_price
        ELSE NULL::NUMERIC
    END AS investment_efficiency_10y

FROM combined_data;


COMMENT ON VIEW screener.company_screener_base IS
    '有効銘柄ごとの最新株価・最新年次財務・スクリーナー向け投資指標';


-- ============================================================
-- Sheets出力用統合VIEWの再作成
-- ============================================================

CREATE OR REPLACE VIEW
    screener.company_screener_with_dividends
AS
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
FROM
    screener.company_screener_base
        AS screener_base
LEFT JOIN
    screener.company_dividend_metrics
        AS dividend_metrics
    ON dividend_metrics.security_code
        = screener_base.security_code
LEFT JOIN
    screener.company_dividend_metrics_adjusted
        AS adjusted
    ON adjusted.security_code
        = screener_base.security_code;


COMMENT ON VIEW
    screener.company_screener_with_dividends
IS
    '最新株価・最新年次財務・raw/JPX adjusted累進配当指標を結合したGoogle Sheets出力用VIEW。';


REVOKE ALL
ON screener.company_screener_with_dividends
FROM PUBLIC, anon, authenticated;


-- ============================================================
-- Discord候補検索キャッシュの再作成
--
-- Materialized Viewは列追加できないため、
-- 新指標を含めて作り直す。
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
    per_undervaluation_years,
    investment_efficiency_10y,
    net_cash_jpy,
    treasury_shares_ratio_percent,
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
