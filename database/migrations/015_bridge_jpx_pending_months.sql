-- 月次PDF公開までの未確定月を、月ごとの正常取得済みwatchlistで保証する。
-- 1か月でも未取得・件数不一致・取得日不足なら完全性を認めない。
-- watchlist掲載銘柄は正式PDFが公開されるまで引き続き除外する。
-- 既存VIEWのcoverage CTEのみ置換し、配当計算・公開列は保持する。
DO $migration$
DECLARE
    definition text;
    coverage_start integer;
    coverage_end integer;
BEGIN
    definition := pg_get_viewdef(
        'screener.company_dividend_metrics_adjusted'::regclass, true
    );
    coverage_start := strpos(definition, 'coverage AS (');
    coverage_end := strpos(definition, 'adjusted_periods AS (');
    IF coverage_start = 0 OR coverage_end <= coverage_start THEN
        RAISE EXCEPTION '補正VIEWのcoverage CTEを特定できません';
    END IF;

    definition := substr(definition, 1, coverage_start - 1) || $coverage$
    coverage AS (
        SELECT
            raw_metrics.security_code,
            jpx_status.covered_from,
            jpx_status.covered_to,
            (
                jpx_status.months_are_continuous IS TRUE
                AND jpx_status.covered_from
                    <= raw_metrics.oldest_fiscal_period_end_5y
                AND latest_market_data.latest_trading_date IS NOT NULL
                AND (
                    jpx_status.covered_to >= latest_market_data.latest_trading_date
                    OR (
                        jpx_status.covered_to IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1
                            FROM GENERATE_SERIES(
                                DATE_TRUNC('month', jpx_status.covered_to)
                                    + INTERVAL '1 month',
                                DATE_TRUNC('month', latest_market_data.latest_trading_date),
                                INTERVAL '1 month'
                            ) AS pending(month_start)
                            LEFT JOIN screener.jpx_current_rights_page_snapshots AS snapshot
                                ON snapshot.coverage_month = pending.month_start::date
                            WHERE snapshot.coverage_month IS NULL
                               OR snapshot.source_count <= 0
                               OR snapshot.record_count <> (
                                   SELECT COUNT(*)
                                   FROM screener.jpx_current_rights_watchlist AS watchlist
                                   WHERE watchlist.coverage_month = pending.month_start::date
                               )
                               OR (snapshot.fetched_at AT TIME ZONE 'Asia/Tokyo')::date
                                   < LEAST(
                                       (pending.month_start + INTERVAL '1 month'
                                           - INTERVAL '1 day')::date,
                                       latest_market_data.latest_trading_date
                                   )
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM screener.jpx_current_rights_watchlist AS watchlist
                            WHERE watchlist.security_code = raw_metrics.security_code
                              AND watchlist.coverage_month > jpx_status.covered_to
                              AND watchlist.coverage_month <= DATE_TRUNC(
                                  'month', latest_market_data.latest_trading_date
                              )::date
                        )
                    )
                )
            ) AS range_is_complete,
            COUNT(actions.corporate_action_id) FILTER (
                WHERE actions.ex_right_type IS DISTINCT FROM '1'
                  AND actions.ex_right_type IS DISTINCT FROM '2'
                  AND actions.adjustment_factor <> 1
            ) AS unsupported_action_count
        FROM screener.company_dividend_metrics AS raw_metrics
        CROSS JOIN jpx_coverage_status AS jpx_status
        CROSS JOIN latest_market_data
        LEFT JOIN screener.corporate_actions AS actions
            ON actions.security_code = raw_metrics.security_code
           AND actions.effective_date > raw_metrics.oldest_fiscal_period_end_5y
           AND actions.effective_date <= jpx_status.covered_to
           AND actions.source = 'JPX'
        GROUP BY raw_metrics.security_code,
                 raw_metrics.oldest_fiscal_period_end_5y,
                 jpx_status.covered_from, jpx_status.covered_to,
                 jpx_status.months_are_continuous,
                 latest_market_data.latest_trading_date
    ),
    $coverage$ || substr(definition, coverage_end);
    EXECUTE 'CREATE OR REPLACE VIEW screener.company_dividend_metrics_adjusted AS '
        || definition;
END
$migration$;
