"""Discord累進配当候補検索コアのテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


# ============================================================
# テスト対象の読込
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from candidate_search import (  # noqa: E402
    CandidateSearchRequest,
    build_candidate_search_message,
    parse_candidate_search_request,
    search_progressive_dividend_candidates,
)


# ============================================================
# テストデータ
# ============================================================

def build_candidate_record(
    *,
    security_code: str = "8057",
    company_name: str = "内田洋行",
    dividend_yield_percent: Decimal | None = Decimal("4.10"),
    per_ratio: Decimal | None = Decimal("12.30"),
    pbr_ratio: Decimal | None = Decimal("1.20"),
    roe_percent: Decimal | None = Decimal("10.50"),
    adjusted: bool = True,
    tdnet_classification: str | None = None,
) -> dict[str, object]:
    """検索結果表示用の候補レコードを作成する。"""

    return {
        "security_code": security_code,
        "company_name": company_name,
        "dividend_yield_percent": dividend_yield_percent,
        "per_ratio": per_ratio,
        "pbr_ratio": pbr_ratio,
        "roe_percent": roe_percent,
        "is_adjustment_coverage_complete": adjusted,
        "tdnet_policy_classification": (
            tdnet_classification
        ),
    }


# ============================================================
# 検索条件
# ============================================================

class CandidateSearchRequestTests(unittest.TestCase):
    """検索条件の検証を確認する。"""

    def test_default_options_are_used(self) -> None:
        request = parse_candidate_search_request({})

        self.assertEqual(
            request.min_dividend_yield_percent,
            Decimal("3.0"),
        )
        self.assertEqual(
            request.max_payout_ratio_percent,
            Decimal("70.0"),
        )
        self.assertEqual(
            request.max_per_ratio,
            Decimal("25.0"),
        )
        self.assertEqual(
            request.max_pbr_ratio,
            Decimal("3.0"),
        )
        self.assertEqual(
            request.min_roe_percent,
            Decimal("8.0"),
        )
        self.assertTrue(
            request.require_positive_free_cash_flow
        )
        self.assertEqual(
            request.max_results,
            10,
        )

    def test_all_options_are_parsed(self) -> None:
        request = parse_candidate_search_request(
            {
                "min_yield": "4.25",
                "max_payout": "60",
                "max_per": "18.5",
                "max_pbr": "1.75",
                "min_roe": "10",
                "positive_fcf": "false",
                "limit": "15",
            }
        )

        self.assertEqual(
            request.min_dividend_yield_percent,
            Decimal("4.25"),
        )
        self.assertEqual(
            request.max_payout_ratio_percent,
            Decimal("60"),
        )
        self.assertEqual(
            request.max_per_ratio,
            Decimal("18.5"),
        )
        self.assertEqual(
            request.max_pbr_ratio,
            Decimal("1.75"),
        )
        self.assertEqual(
            request.min_roe_percent,
            Decimal("10"),
        )
        self.assertFalse(
            request.require_positive_free_cash_flow
        )
        self.assertEqual(
            request.max_results,
            15,
        )

    def test_blank_options_use_defaults(self) -> None:
        request = parse_candidate_search_request(
            {
                "min_yield": "",
                "max_per": "   ",
                "positive_fcf": None,
                "limit": "",
            }
        )

        self.assertEqual(
            request.min_dividend_yield_percent,
            Decimal("3.0"),
        )
        self.assertEqual(
            request.max_per_ratio,
            Decimal("25.0"),
        )
        self.assertTrue(
            request.require_positive_free_cash_flow
        )
        self.assertEqual(
            request.max_results,
            10,
        )

    def test_non_finite_numbers_are_rejected(self) -> None:
        for option_name, value in (
            ("min_yield", "NaN"),
            ("max_payout", "Infinity"),
            ("max_per", "-Infinity"),
        ):
            with self.subTest(
                option_name=option_name,
                value=value,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "有限値",
                ):
                    parse_candidate_search_request(
                        {
                            option_name: value,
                        }
                    )

    def test_negative_minimum_yield_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "min_yield",
        ):
            parse_candidate_search_request(
                {
                    "min_yield": "-0.01",
                }
            )

    def test_non_positive_maximum_values_are_rejected(
        self,
    ) -> None:
        for option_name in (
            "max_payout",
            "max_per",
            "max_pbr",
        ):
            with self.subTest(
                option_name=option_name
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    option_name,
                ):
                    parse_candidate_search_request(
                        {
                            option_name: "0",
                        }
                    )

    def test_limit_boundaries_are_allowed(self) -> None:
        self.assertEqual(
            parse_candidate_search_request(
                {
                    "limit": "1",
                }
            ).max_results,
            1,
        )
        self.assertEqual(
            parse_candidate_search_request(
                {
                    "limit": "20",
                }
            ).max_results,
            20,
        )

    def test_out_of_range_limits_are_rejected(self) -> None:
        for value in ("0", "21"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "limit",
                ):
                    parse_candidate_search_request(
                        {
                            "limit": value,
                        }
                    )

    def test_boolean_and_string_fcf_options_are_allowed(
        self,
    ) -> None:
        self.assertTrue(
            parse_candidate_search_request(
                {
                    "positive_fcf": True,
                }
            ).require_positive_free_cash_flow
        )
        self.assertFalse(
            parse_candidate_search_request(
                {
                    "positive_fcf": False,
                }
            ).require_positive_free_cash_flow
        )
        self.assertTrue(
            parse_candidate_search_request(
                {
                    "positive_fcf": "true",
                }
            ).require_positive_free_cash_flow
        )
        self.assertFalse(
            parse_candidate_search_request(
                {
                    "positive_fcf": "false",
                }
            ).require_positive_free_cash_flow
        )

    def test_request_converts_to_candidate_criteria(
        self,
    ) -> None:
        request = CandidateSearchRequest(
            min_dividend_yield_percent=Decimal("4"),
            max_payout_ratio_percent=Decimal("65"),
            max_per_ratio=Decimal("20"),
            max_pbr_ratio=Decimal("2"),
            min_roe_percent=Decimal("9"),
            require_positive_free_cash_flow=False,
            max_results=12,
        )

        criteria = request.to_candidate_criteria()

        self.assertEqual(
            criteria.min_dividend_yield_percent,
            Decimal("4"),
        )
        self.assertEqual(
            criteria.max_payout_ratio_percent,
            Decimal("65"),
        )
        self.assertEqual(
            criteria.max_per_ratio,
            Decimal("20"),
        )
        self.assertEqual(
            criteria.max_pbr_ratio,
            Decimal("2"),
        )
        self.assertEqual(
            criteria.min_roe_percent,
            Decimal("9"),
        )
        self.assertFalse(
            criteria.require_positive_free_cash_flow
        )
        self.assertEqual(
            criteria.max_candidates,
            12,
        )


# ============================================================
# PostgreSQL検索
# ============================================================

class CandidateSearchExecutionTests(unittest.TestCase):
    """既存候補検索との接続を確認する。"""

    @patch(
        "candidate_search."
        "enrich_candidate_records_with_latest_tdnet_policy_results"
    )
    @patch(
        "candidate_search."
        "load_progressive_dividend_candidates"
    )
    def test_existing_candidate_query_is_reused(
        self,
        load_candidates_mock,
        enrich_candidates_mock,
    ) -> None:
        request = parse_candidate_search_request(
            {
                "min_yield": "4",
                "max_payout": "65",
                "max_per": "20",
                "max_pbr": "2",
                "min_roe": "9",
                "positive_fcf": "false",
                "limit": "5",
            }
        )
        loaded_records = [
            build_candidate_record(),
        ]
        enriched_records = [
            build_candidate_record(
                tdnet_classification="confirmed",
            ),
        ]

        load_candidates_mock.return_value = loaded_records
        enrich_candidates_mock.return_value = (
            enriched_records
        )

        result = search_progressive_dividend_candidates(
            request
        )

        load_candidates_mock.assert_called_once_with(
            request.to_candidate_criteria()
        )
        enrich_candidates_mock.assert_called_once_with(
            loaded_records
        )
        self.assertEqual(
            result,
            enriched_records,
        )


# ============================================================
# Discord表示
# ============================================================

class CandidateSearchMessageTests(unittest.TestCase):
    """Discord検索結果の整形を確認する。"""

    def setUp(self) -> None:
        self.request = parse_candidate_search_request(
            {
                "limit": "20",
            }
        )

    def test_results_keep_input_order_and_rank(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                security_code="1111",
                company_name="第一会社",
            ),
            build_candidate_record(
                security_code="2222",
                company_name="第二会社",
            ),
        ]

        message = build_candidate_search_message(
            records,
            self.request,
        )

        first_position = message.index(
            "1. `1111` 第一会社"
        )
        second_position = message.index(
            "2. `2222` 第二会社"
        )

        self.assertLess(
            first_position,
            second_position,
        )

    def test_confirmed_marker_is_shown_only_for_confirmed(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                security_code="1111",
                tdnet_classification="confirmed",
            ),
            build_candidate_record(
                security_code="2222",
                tdnet_classification="not_confirmed",
            ),
        ]

        message = build_candidate_search_message(
            records,
            self.request,
        )

        confirmed_line = next(
            line
            for line in message.splitlines()
            if "`1111`" in line
        )
        not_confirmed_line = next(
            line
            for line in message.splitlines()
            if "`2222`" in line
        )

        self.assertIn(
            "[TDnet confirmed]",
            confirmed_line,
        )
        self.assertNotIn(
            "[TDnet confirmed]",
            not_confirmed_line,
        )

    def test_adjusted_and_raw_labels_are_shown(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                security_code="1111",
                adjusted=True,
            ),
            build_candidate_record(
                security_code="2222",
                adjusted=False,
            ),
        ]

        message = build_candidate_search_message(
            records,
            self.request,
        )

        adjusted_line = next(
            line
            for line in message.splitlines()
            if "`1111`" in line
        )
        raw_line = next(
            line
            for line in message.splitlines()
            if "`2222`" in line
        )

        self.assertIn(
            "[adjusted]",
            adjusted_line,
        )
        self.assertIn(
            "[raw]",
            raw_line,
        )

    def test_empty_results_have_explicit_message(
        self,
    ) -> None:
        message = build_candidate_search_message(
            [],
            self.request,
        )

        self.assertIn(
            "条件に一致する銘柄はありません。",
            message,
        )

    def test_company_name_is_normalized_to_one_line(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                company_name="株式会社\nテスト   企業",
            ),
        ]

        message = build_candidate_search_message(
            records,
            self.request,
        )

        self.assertIn(
            "株式会社 テスト 企業",
            message,
        )
        self.assertNotIn(
            "株式会社\nテスト",
            message,
        )

    def test_invalid_numbers_are_displayed_as_hyphens(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                dividend_yield_percent=None,
                per_ratio=Decimal("NaN"),
                pbr_ratio=None,
                roe_percent=Decimal("Infinity"),
            ),
        ]

        message = build_candidate_search_message(
            records,
            self.request,
        )

        result_line = next(
            line
            for line in message.splitlines()
            if "`8057`" in line
        )

        self.assertIn(
            "利回り -",
            result_line,
        )
        self.assertIn(
            "PER -",
            result_line,
        )
        self.assertIn(
            "PBR -",
            result_line,
        )
        self.assertIn(
            "ROE -",
            result_line,
        )

    def test_message_does_not_exceed_default_limit(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                security_code=f"{index:04d}",
                company_name=(
                    "非常に長い会社名" * 20
                ),
            )
            for index in range(20)
        ]

        message = build_candidate_search_message(
            records,
            self.request,
        )

        self.assertLessEqual(
            len(message),
            1900,
        )
        self.assertIn(
            "省略しました",
            message,
        )

    def test_empty_results_respect_custom_limit(
        self,
    ) -> None:
        message = build_candidate_search_message(
            [],
            self.request,
            max_chars=100,
        )

        self.assertLessEqual(
            len(message),
            100,
        )
        self.assertTrue(
            message.endswith("…")
        )

    def test_long_condition_line_respects_limit(
        self,
    ) -> None:
        request = CandidateSearchRequest(
            min_dividend_yield_percent=Decimal(
                "1" * 500
            ),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            require_positive_free_cash_flow=True,
            max_results=10,
        )

        message = build_candidate_search_message(
            [],
            request,
            max_chars=300,
        )

        self.assertLessEqual(
            len(message),
            300,
        )
        self.assertTrue(
            message.endswith("…")
        )

    def test_formatting_does_not_mutate_records(
        self,
    ) -> None:
        records = [
            build_candidate_record(
                tdnet_classification="confirmed",
            ),
        ]
        original_records = deepcopy(records)

        build_candidate_search_message(
            records,
            self.request,
        )

        self.assertEqual(
            records,
            original_records,
        )


if __name__ == "__main__":
    unittest.main()
