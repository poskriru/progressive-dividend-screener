-- ============================================================
-- 002_company_screener_base_view.sql
--
-- 有効銘柄について、最新株価と最新年次財務を結合し、
-- スクリーナー向け投資指標を算出するVIEWを作成する。
-- ============================================================

CREATE OR REPLACE VIEW screener.company_screener_base AS

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
    END AS free_cash_flow_jpy

FROM combined_data;


COMMENT ON VIEW screener.company_screener_base IS
    '有効銘柄ごとの最新株価・最新年次財務・スクリーナー向け投資指標';
