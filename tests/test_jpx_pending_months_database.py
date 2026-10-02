"""実PostgreSQLで月次PDF公開待ちの複数月保証を検証する。"""

import os
import unittest
from pathlib import Path

import psycopg


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "専用テストDB未設定")
class PendingMonthsDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        with cls.connection.cursor() as cursor:
            for role in ("anon", "authenticated"):
                cursor.execute(f"CREATE ROLE {role}")
            migrations = Path(__file__).resolve().parent.parent / "database" / "migrations"
            for migration in sorted(migrations.glob("*.sql")):
                cursor.execute(migration.read_text(encoding="utf-8"))
            cursor.execute("""
                INSERT INTO screener.securities (security_code, company_name)
                VALUES ('1234', 'テスト');
                INSERT INTO screener.daily_prices
                    (security_code, trading_date, close_price)
                VALUES ('1234', '2026-10-01', 100);
                INSERT INTO screener.edinet_documents (doc_id, security_code)
                SELECT 'test' || y::text, '1234'
                FROM generate_series(2022, 2026) AS y;
                INSERT INTO screener.annual_financials
                    (security_code, doc_id, fiscal_period_end, annual_dividend_yen)
                SELECT '1234', 'test' || y::text, make_date(y, 3, 31), 10
                FROM generate_series(2022, 2026) AS y;
                INSERT INTO screener.jpx_corporate_action_source_files
                    (source_kind, coverage_start, coverage_end, source_url,
                     sync_status, content_sha256)
                SELECT 'monthly_pdf', m::date,
                       (m + interval '1 month' - interval '1 day')::date,
                       'https://www.jpx.co.jp/' || m::text, 'complete', repeat('a', 64)
                FROM generate_series('2021-01-01'::date, '2026-08-01'::date,
                                     interval '1 month') AS m;
            """)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        with self.connection.cursor() as cursor:
            cursor.execute("TRUNCATE screener.jpx_current_rights_page_snapshots CASCADE")
            cursor.execute("""
                INSERT INTO screener.jpx_current_rights_page_snapshots
                    (coverage_month, page_url, content_sha256, source_count,
                     record_count, fetched_at)
                VALUES ('2026-09-01', 'https://www.jpx.co.jp/archive', repeat('a', 64),
                        1, 0, '2026-10-02 00:00:00+09'),
                       ('2026-10-01', 'https://www.jpx.co.jp/current', repeat('a', 64),
                        1, 0, '2026-10-02 00:00:00+09')
            """)

    def is_complete(self):
        with self.connection.cursor() as cursor:
            cursor.execute("""
                SELECT is_adjustment_coverage_complete
                FROM screener.company_dividend_metrics_adjusted
                WHERE security_code = '1234'
            """)
            return cursor.fetchone()[0]

    def test_two_pending_months_allow_unaffected_security(self):
        self.assertTrue(self.is_complete())

    def test_missing_previous_month_is_incomplete(self):
        self.connection.execute("""
            DELETE FROM screener.jpx_current_rights_page_snapshots
            WHERE coverage_month = '2026-09-01'
        """)
        self.assertFalse(self.is_complete())

    def test_previous_month_snapshot_must_cover_month_end(self):
        self.connection.execute("""
            UPDATE screener.jpx_current_rights_page_snapshots
            SET fetched_at = '2026-09-24 00:00:00+09'
            WHERE coverage_month = '2026-09-01'
        """)
        self.assertFalse(self.is_complete())

    def test_count_mismatch_is_incomplete(self):
        self.connection.execute("""
            UPDATE screener.jpx_current_rights_page_snapshots
            SET record_count = 1 WHERE coverage_month = '2026-09-01'
        """)
        self.assertFalse(self.is_complete())

    def test_previous_month_watchlist_security_stays_excluded(self):
        self.connection.execute("""
            INSERT INTO screener.jpx_current_rights_watchlist
                (coverage_month, security_code, allocation_date, adjustment_factor, source_url)
            VALUES ('2026-09-01', '1234', '2026-09-30', 0.5, 'https://www.jpx.co.jp/test.csv');
            UPDATE screener.jpx_current_rights_page_snapshots
            SET record_count = 1 WHERE coverage_month = '2026-09-01'
        """)
        self.assertFalse(self.is_complete())
