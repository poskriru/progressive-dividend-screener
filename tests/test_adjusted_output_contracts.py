"""raw/adjusted出力契約と安全な候補切替の回帰テスト。"""

import hashlib
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from export_database_indicators import (  # noqa: E402
    DATABASE_INDICATOR_HEADERS,
    build_indicator_rows,
)
from export_progressive_dividend_candidates import (  # noqa: E402
    ADJUSTED_DIVIDEND_NOTE,
    CANDIDATE_HEADERS,
    RAW_DIVIDEND_CAUTION,
    CandidateCriteria,
    build_candidate_rows,
    diagnose_candidate_exclusion,
)


LEGACY_INDICATOR_HEADERS = [
    "更新日時", "株価基準日", "証券コード", "銘柄名", "市場", "終値",
    "決算期末日", "会計基準", "売上高（百万円）", "営業利益（百万円）",
    "純利益（百万円）", "総資産（百万円）", "純資産（百万円）",
    "自己資本（百万円）", "EPS（円）", "BPS（円）", "1株配当（円）",
    "発行済株式数", "時価総額（百万円）", "PER（倍）", "PBR（倍）",
    "ROE（%）", "ROA（%）", "自己資本比率（%）", "営業利益率（%）",
    "純利益率（%）", "配当利回り（%）", "配当性向（%）",
    "営業CF（百万円）", "投資CF（百万円）", "フリーCF（百万円）",
    "財務CF（百万円）", "書類管理番号", "EDINET閲覧URL", "配当履歴期数",
    "判定対象配当期数", "最新年間配当（円）", "前期年間配当（円）",
    "5期最古年間配当（円）", "5期増配回数", "5期据え置き回数",
    "5期減配回数", "連続非減配期数", "連続増配期数", "5期配当CAGR（%）",
    "5期累進配当判定", "累進配当判定状態", "5期配当履歴",
    "株式分割等未調整",
]

LEGACY_CANDIDATE_HEADERS = [
    "更新日時", "順位", "株価基準日", "証券コード", "銘柄名", "市場",
    "業種", "終値", "配当利回り（%）", "配当性向（%）", "PER（倍）",
    "PBR（倍）", "ROE（%）", "自己資本比率（%）", "フリーCF（百万円）",
    "5期配当CAGR（%）", "5期増配回数", "5期据え置き回数",
    "連続非減配期数", "連続増配期数", "最新年間配当（円）",
    "5期最古年間配当（円）", "5期配当履歴", "EDINET閲覧URL",
    "TDnet方針候補", "TDnet最新方針開示日", "TDnet最新方針表題",
    "TDnet方針PDF URL", "TDnet減配警戒", "TDnet最新配当開示日",
    "TDnet最新配当分類", "TDnet最新配当表題", "TDnet最新配当PDF URL",
    "判定注記",
]


class OutputContractTest(unittest.TestCase):
    def test_existing_indicator_columns_are_unchanged_and_new_columns_append(self) -> None:
        self.assertEqual(DATABASE_INDICATOR_HEADERS[:49], LEGACY_INDICATOR_HEADERS)
        self.assertEqual(len(DATABASE_INDICATOR_HEADERS), 61)

    def test_existing_candidate_columns_are_unchanged_and_new_columns_append(self) -> None:
        self.assertEqual(CANDIDATE_HEADERS[:34], LEGACY_CANDIDATE_HEADERS)
        self.assertEqual(len(CANDIDATE_HEADERS), 45)

    def test_indicator_row_keeps_raw_and_adjusted_history(self) -> None:
        record = {
            "security_code": "1234",
            "company_name": "テスト",
            "fiscal_periods_5y": [date(2021, 3, 31)],
            "annual_dividends_yen_5y": [Decimal("100")],
            "adjusted_fiscal_periods_5y": [date(2021, 3, 31)],
            "adjusted_annual_dividends_yen_5y": [Decimal("50")],
            "is_adjustment_coverage_complete": True,
        }
        row = build_indicator_rows([record])[0]
        self.assertEqual(len(row), 61)
        self.assertEqual(row[47], "2021-03-31:100.0")
        self.assertEqual(row[60], "2021-03-31:50.0")

    def test_candidate_note_identifies_selected_decision(self) -> None:
        base = {
            "security_code": "1234",
            "company_name": "テスト",
            "tdnet_policy_candidate": False,
            "tdnet_dividend_warning": False,
        }
        raw_row = build_candidate_rows([dict(base)])[0]
        adjusted_record = dict(base)
        adjusted_record["is_adjustment_coverage_complete"] = True
        adjusted_row = build_candidate_rows([adjusted_record])[0]

        self.assertEqual(raw_row[33], RAW_DIVIDEND_CAUTION)
        self.assertEqual(raw_row[34], "raw")
        self.assertEqual(adjusted_row[33], ADJUSTED_DIVIDEND_NOTE)
        self.assertEqual(adjusted_row[34], "adjusted")


class CandidateDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.criteria = CandidateCriteria(
            min_dividend_yield_percent=Decimal("3"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            require_positive_free_cash_flow=True,
            max_candidates=300,
        )
        self.other_metrics = {
            "annual_financial_id": 1,
            "close_price": Decimal("1000"),
            "dividend_yield_percent": Decimal("4"),
            "payout_ratio_percent": Decimal("40"),
            "per_ratio": Decimal("10"),
            "pbr_ratio": Decimal("1"),
            "roe_percent": Decimal("10"),
            "free_cash_flow_jpy": Decimal("1"),
        }

    def test_complete_coverage_uses_adjusted_failure(self) -> None:
        record = {
            **self.other_metrics,
            "is_adjustment_coverage_complete": True,
            "is_progressive_dividend_5y_raw": True,
            "progressive_dividend_status_5y": "progressive",
            "is_progressive_dividend_5y_adjusted": False,
            "progressive_dividend_status_5y_adjusted": "dividend_cut",
        }
        self.assertIn("5期内に減配", diagnose_candidate_exclusion(record, self.criteria))

    def test_incomplete_coverage_does_not_treat_raw_as_adjusted(self) -> None:
        record = {
            **self.other_metrics,
            "is_adjustment_coverage_complete": False,
            "is_progressive_dividend_5y_raw": False,
            "progressive_dividend_status_5y": "dividend_cut",
            "is_progressive_dividend_5y_adjusted": True,
            "progressive_dividend_status_5y_adjusted": "progressive",
        }
        self.assertIn("5期内に減配", diagnose_candidate_exclusion(record, self.criteria))


class MigrationIntegrityTest(unittest.TestCase):
    def test_applied_migration_hashes_are_unchanged(self) -> None:
        expected = {
            "003_progressive_dividend_metrics.sql": (
                "f839f85cf4b5d6de1670a8b054ad4fd66c7b665c74ba15b68098e81a668912bf"
            ),
            "004_company_screener_dividend_metrics.sql": (
                "dfc2291343c78c5c25c20031c133fdcf60645fd5aee4b53bcfe709485036d988"
            ),
        }
        for filename, expected_hash in expected.items():
            content = (PROJECT_ROOT / "database" / "migrations" / filename).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected_hash)

    def test_adjusted_sql_uses_strict_ex_date_boundary(self) -> None:
        sql = (
            PROJECT_ROOT
            / "database"
            / "migrations"
            / "006_adjusted_dividend_metrics.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("actions.effective_date > periods.fiscal_period_end", sql)
        self.assertIn("actions.ex_right_type IN ('1', '2')", sql)
        self.assertIn("'adjustment_data_incomplete'", sql)


if __name__ == "__main__":
    unittest.main()
