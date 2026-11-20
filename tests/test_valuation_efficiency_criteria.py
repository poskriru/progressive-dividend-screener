"""PER割安度（年）と投資効率（10年）の抽出条件を確認する。"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from candidate_search import (  # noqa: E402
    DEFAULT_SEARCH_MAX_PER_DISCOUNT_YEARS,
    DEFAULT_SEARCH_MIN_INVESTMENT_EFFICIENCY,
    build_candidate_search_condition_line,
    build_candidate_search_result_line,
    parse_candidate_search_request,
)
from export_progressive_dividend_candidates import (  # noqa: E402
    CANDIDATE_CRITERIA_SHEET_NAME,
    CandidateCriteria,
    diagnose_candidate_exclusion,
    load_candidate_criteria_from_sheet,
    load_progressive_dividend_candidates,
)

from discord_candidate_repository import (  # noqa: E402
    CandidateCriteria as SearchCriteria,
)


def make_criteria(**overrides) -> CandidateCriteria:
    defaults = dict(
        min_dividend_yield_percent=Decimal("3"),
        max_payout_ratio_percent=Decimal("70"),
        max_per_ratio=Decimal("25"),
        max_pbr_ratio=Decimal("3"),
        min_roe_percent=Decimal("8"),
        max_per_discount_years=Decimal("5.5"),
        min_investment_efficiency=Decimal("1.7"),
        require_positive_free_cash_flow=True,
        max_candidates=300,
    )
    defaults.update(overrides)
    return CandidateCriteria(**defaults)


class ValuationEfficiencyCriteriaTests(unittest.TestCase):
    """条件シート設定の読み込みを確認する。"""

    def test_settings_accept_new_criteria(self) -> None:
        criteria = CandidateCriteria.from_settings(
            {
                "CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT": "3",
                "CANDIDATE_MAX_PAYOUT_RATIO_PERCENT": "70",
                "CANDIDATE_MAX_PER_RATIO": "25",
                "CANDIDATE_MAX_PBR_RATIO": "3",
                "CANDIDATE_MIN_ROE_PERCENT": "8",
                "CANDIDATE_MAX_PER_DISCOUNT_YEARS": "5.5",
                "CANDIDATE_MIN_INVESTMENT_EFFICIENCY": "1.7",
                "CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW": "true",
                "CANDIDATE_MAX_ROWS": "300",
            }
        )

        self.assertEqual(
            criteria.max_per_discount_years,
            Decimal("5.5"),
        )
        self.assertEqual(
            criteria.min_investment_efficiency,
            Decimal("1.7"),
        )

    def test_missing_settings_are_appended_with_defaults(
        self,
    ) -> None:
        """既存シートに新設定が無い場合、既定値の行を自動追加する。"""

        sheets_service = Mock()
        spreadsheet_id = "test-id"

        values = [
            ["設定項目", "設定値", "説明"],
            ["CANDIDATE_MIN_DIVIDEND_YIELD_PERCENT", "3", ""],
            ["CANDIDATE_MAX_PAYOUT_RATIO_PERCENT", "70", ""],
            ["CANDIDATE_MAX_PER_RATIO", "25", ""],
            ["CANDIDATE_MAX_PBR_RATIO", "3", ""],
            ["CANDIDATE_MIN_ROE_PERCENT", "8", ""],
            ["CANDIDATE_REQUIRE_POSITIVE_FREE_CASH_FLOW", "true", ""],
            ["CANDIDATE_MAX_ROWS", "300", ""],
        ]

        with patch(
            "export_progressive_dividend_candidates"
            ".get_spreadsheet_metadata",
            return_value={
                "sheets": [
                    {
                        "properties": {
                            "title": (
                                CANDIDATE_CRITERIA_SHEET_NAME
                            )
                        }
                    }
                ]
            },
        ), patch(
            "export_progressive_dividend_candidates"
            ".read_sheet",
            return_value=values,
        ), patch(
            "export_progressive_dividend_candidates"
            ".write_sheet"
        ) as write_sheet:
            criteria = (
                load_candidate_criteria_from_sheet(
                    sheets_service,
                    spreadsheet_id,
                )
            )

        self.assertEqual(
            criteria.max_per_discount_years,
            Decimal("5.5"),
        )
        self.assertEqual(
            criteria.min_investment_efficiency,
            Decimal("1.7"),
        )

        written_args = write_sheet.call_args.args

        written_headers = written_args[3]
        written_rows = written_args[4]

        written_names = [
            row[0] for row in written_rows
        ]

        self.assertIn(
            "CANDIDATE_MAX_PER_DISCOUNT_YEARS",
            written_names,
        )
        self.assertIn(
            "CANDIDATE_MIN_INVESTMENT_EFFICIENCY",
            written_names,
        )

    def test_export_sql_filters_new_criteria(self) -> None:
        """候補取得SQLに新条件フィルタを適用する。"""

        criteria = make_criteria(
            max_per_discount_years=Decimal("4.5"),
            min_investment_efficiency=Decimal("2"),
        )

        with patch(
            "export_progressive_dividend_candidates"
            ".create_database_connection"
        ) as connect:
            cursor = (
                connect.return_value.__enter__.
                return_value.cursor.return_value.
                __enter__.return_value
            )
            cursor.fetchall.return_value = []

            load_progressive_dividend_candidates(
                criteria
            )

            query, parameters = (
                cursor.execute.call_args.args
            )

        normalized_query = " ".join(query.split())

        self.assertIn(
            "per_undervaluation_years IS NOT NULL",
            normalized_query,
        )
        self.assertIn(
            "per_undervaluation_years <= %s",
            normalized_query,
        )
        self.assertIn(
            "investment_efficiency_10y IS NOT NULL",
            normalized_query,
        )
        self.assertIn(
            "investment_efficiency_10y >= %s",
            normalized_query,
        )

        self.assertIn(
            Decimal("4.5"),
            parameters,
        )
        self.assertIn(
            Decimal("2"),
            parameters,
        )


class ValuationEfficiencyDiagnosisTests(unittest.TestCase):
    """候補除外理由の判定を確認する。"""

    def test_missing_per_undervaluation_is_reported(
        self,
    ) -> None:
        reasons = diagnose_candidate_exclusion(
            {
                "dividend_yield_percent": Decimal("4"),
                "payout_ratio_percent": Decimal("40"),
                "per_ratio": Decimal("10"),
                "pbr_ratio": Decimal("1"),
                "roe_percent": Decimal("10"),
                "per_undervaluation_years": None,
                "investment_efficiency_10y": Decimal("2"),
            },
            make_criteria(),
        )

        self.assertIn(
            "PER割安度算出不可",
            reasons,
        )

    def test_excessive_per_undervaluation_is_reported(
        self,
    ) -> None:
        reasons = diagnose_candidate_exclusion(
            {
                "dividend_yield_percent": Decimal("4"),
                "payout_ratio_percent": Decimal("40"),
                "per_ratio": Decimal("10"),
                "pbr_ratio": Decimal("1"),
                "roe_percent": Decimal("10"),
                "per_undervaluation_years": Decimal("7"),
                "investment_efficiency_10y": Decimal("2"),
            },
            make_criteria(),
        )

        joined_reasons = "\n".join(reasons)

        self.assertIn(
            "PER割安度が上限超過",
            joined_reasons,
        )
        self.assertIn("7.00年", joined_reasons)

    def test_insufficient_investment_efficiency_is_reported(
        self,
    ) -> None:
        reasons = diagnose_candidate_exclusion(
            {
                "dividend_yield_percent": Decimal("4"),
                "payout_ratio_percent": Decimal("40"),
                "per_ratio": Decimal("10"),
                "pbr_ratio": Decimal("1"),
                "roe_percent": Decimal("10"),
                "per_undervaluation_years": Decimal("4"),
                "investment_efficiency_10y": Decimal("1.2"),
            },
            make_criteria(),
        )

        joined_reasons = "\n".join(reasons)

        self.assertIn(
            "投資効率が下限未満",
            joined_reasons,
        )
        self.assertIn("1.20", joined_reasons)


class SearchCriteriaIntegrationTests(unittest.TestCase):
    """Discord検索側の既定条件と表示を確認する。"""

    def test_search_defaults_match_sheet_defaults(self) -> None:
        self.assertEqual(
            DEFAULT_SEARCH_MAX_PER_DISCOUNT_YEARS,
            Decimal("5.5"),
        )
        self.assertEqual(
            DEFAULT_SEARCH_MIN_INVESTMENT_EFFICIENCY,
            Decimal("1.7"),
        )

    def test_request_forwards_new_criteria(self) -> None:
        criteria = parse_candidate_search_request(
            {}
        ).to_candidate_criteria()

        self.assertIsInstance(
            criteria,
            SearchCriteria,
        )
        self.assertEqual(
            criteria.max_per_discount_years,
            Decimal("5.5"),
        )
        self.assertEqual(
            criteria.min_investment_efficiency,
            Decimal("1.7"),
        )

    def test_condition_line_shows_new_criteria(self) -> None:
        request = parse_candidate_search_request({})

        condition_line = (
            build_candidate_search_condition_line(
                request
            )
        )

        self.assertIn("PER割安度5.5年以下", condition_line)
        self.assertIn("投資効率1.7以上", condition_line)

    def test_result_line_shows_new_metrics(self) -> None:
        record = {
            "security_code": "1234",
            "company_name": "テスト",
            "per_undervaluation_years": Decimal("4.25"),
            "investment_efficiency_10y": Decimal("1.9"),
        }

        line = build_candidate_search_result_line(
            1,
            record,
        )

        self.assertIn("PER割安度 4.25年", line)
        self.assertIn("投資効率 1.90", line)


class ValuationEfficiencyMigrationTests(unittest.TestCase):
    """マイグレーション016の新指標定義を確認する。"""

    @staticmethod
    def read_migration() -> str:
        return (
            PROJECT_ROOT
            / "database"
            / "migrations"
            / "016_screener_valuation_efficiency_metrics.sql"
        ).read_text(encoding="utf-8")

    def test_financial_columns_are_added(self) -> None:
        sql = self.read_migration()

        for column in (
            "current_assets_jpy",
            "investment_securities_jpy",
            "liabilities_jpy",
            "treasury_shares",
        ):
            with self.subTest(column=column):
                self.assertIn(
                    f"ADD COLUMN IF NOT EXISTS {column}",
                    sql,
                )

    def test_view_defines_new_metrics(self) -> None:
        sql = self.read_migration()

        self.assertIn(
            "AS per_undervaluation_years",
            sql,
        )
        self.assertIn(
            "AS investment_efficiency_10y",
            sql,
        )
        self.assertIn(
            "AS net_cash_jpy",
            sql,
        )
        self.assertIn(
            "AS treasury_shares_ratio_percent",
            sql,
        )
        self.assertIn(
            "investment_securities_jpy * 0.7::NUMERIC",
            sql,
        )
        self.assertIn(
            "net_income_jpy > 0::NUMERIC",
            sql,
        )

    def test_search_cache_is_recreated_with_new_columns(
        self,
    ) -> None:
        sql = self.read_migration()

        self.assertIn(
            "DROP MATERIALIZED VIEW",
            sql,
        )
        self.assertIn(
            "CREATE MATERIALIZED VIEW",
            sql,
        )
        self.assertIn(
            "per_undervaluation_years,",
            sql,
        )
        self.assertIn(
            "investment_efficiency_10y,",
            sql,
        )


if __name__ == "__main__":
    unittest.main()
