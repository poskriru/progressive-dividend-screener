-- ============================================================
-- 004_company_screener_dividend_metrics.sql
--
-- 既存の株式指標VIEWと累進配当指標VIEWを結合し、
-- Google Sheets出力用の統合VIEWを作成する。
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
    dividend_metrics.annual_dividends_yen_5y

FROM screener.company_screener_base AS screener_base

LEFT JOIN screener.company_dividend_metrics AS dividend_metrics
    ON dividend_metrics.security_code
        = screener_base.security_code;


-- ============================================================
-- VIEWコメント
-- ============================================================

COMMENT ON VIEW
    screener.company_screener_with_dividends
IS
'最新株価・最新年次財務・投資指標・累進配当指標を結合したGoogle Sheets出力用VIEW。';


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL
ON screener.company_screener_with_dividends
FROM PUBLIC;

REVOKE ALL
ON screener.company_screener_with_dividends
FROM anon, authenticated;
