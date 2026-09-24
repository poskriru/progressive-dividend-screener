"""累進配当候補のTDnet本文解析Discord表示テスト。"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))


from export_progressive_dividend_candidates import (  # noqa: E402
    CandidateChanges,
    CandidateCriteria,
    build_discord_notification_description,
    build_tdnet_policy_discord_lines,
    normalize_exclusion_reason,
    summarize_exclusion_reasons,
)

class TdnetPolicyCandidateNotificationTest(
    unittest.TestCase
):
    def test_confirmed_result_is_displayed(
        self,
    ) -> None:
        record = {
            "tdnet_policy_analysis_status": (
                "completed"
            ),
            "tdnet_policy_classification": (
                "confirmed"
            ),
            "tdnet_policy_matched_phrase": (
                "累進配当の導入"
            ),
        }

        lines = build_tdnet_policy_discord_lines(
            record
        )

        self.assertEqual(
            lines,
            [
                (
                    "   - TDnet本文確認状態: "
                    "`completed`"
                ),
                (
                    "   - TDnet本文判定: "
                    "`confirmed`"
                ),
                (
                    "   - 一致フレーズ: "
                    "累進配当の導入"
                ),
            ],
        )

    def test_manual_review_is_displayed(
        self,
    ) -> None:
        record = {
            "tdnet_policy_analysis_status": (
                "completed"
            ),
            "tdnet_policy_classification": (
                "manual_review"
            ),
            "tdnet_policy_matched_phrase": (
                "累進配当"
            ),
        }

        lines = build_tdnet_policy_discord_lines(
            record
        )

        self.assertIn(
            "   - TDnet本文判定: `manual_review`",
            lines,
        )
        self.assertIn(
            "   - 一致フレーズ: 累進配当",
            lines,
        )

    def test_failed_analysis_is_displayed(
        self,
    ) -> None:
        record = {
            "tdnet_policy_analysis_status": (
                "fetch_failed"
            ),
            "tdnet_policy_classification": None,
            "tdnet_policy_matched_phrase": None,
        }

        lines = build_tdnet_policy_discord_lines(
            record
        )

        self.assertEqual(
            lines,
            [
                (
                    "   - TDnet本文確認状態: "
                    "`fetch_failed`"
                ),
            ],
        )

    def test_not_confirmed_is_not_displayed(
        self,
    ) -> None:
        record = {
            "tdnet_policy_analysis_status": (
                "completed"
            ),
            "tdnet_policy_classification": (
                "not_confirmed"
            ),
            "tdnet_policy_matched_phrase": None,
        }

        self.assertEqual(
            build_tdnet_policy_discord_lines(
                record
            ),
            [],
        )

    def test_missing_analysis_is_not_displayed(
        self,
    ) -> None:
        self.assertEqual(
            build_tdnet_policy_discord_lines({}),
            [],
        )

    def test_matched_phrase_is_normalized_and_limited(
        self,
    ) -> None:
        record = {
            "tdnet_policy_analysis_status": (
                "completed"
            ),
            "tdnet_policy_classification": (
                "confirmed"
            ),
            "tdnet_policy_matched_phrase": (
                "累進配当\n"
                + ("方針" * 100)
            ),
        }

        lines = build_tdnet_policy_discord_lines(
            record
        )
        phrase_line = lines[-1]

        self.assertNotIn("\n", phrase_line)
        self.assertTrue(
            phrase_line.endswith("…")
        )
        self.assertLessEqual(
            len(
                phrase_line.removeprefix(
                    "   - 一致フレーズ: "
                )
            ),
            120,
        )

    def test_candidate_description_keeps_adjusted_label(
        self,
    ) -> None:
        criteria = CandidateCriteria(
            min_dividend_yield_percent=Decimal("3"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            require_positive_free_cash_flow=True,
            max_candidates=300,
        )
        record = {
            "security_code": "8057",
            "company_name": "内田洋行",
            "is_adjustment_coverage_complete": True,
            "tdnet_policy_candidate": True,
            "tdnet_dividend_warning": False,
            "tdnet_policy_analysis_status": (
                "completed"
            ),
            "tdnet_policy_classification": (
                "confirmed"
            ),
            "tdnet_policy_matched_phrase": (
                "累進配当の導入"
            ),
        }

        changes = CandidateChanges(
            comparison_id="adjusted-label-test",
            is_first_export=False,
            added_candidates=(),
            removed_candidates=(),
        )

        description = (
            build_discord_notification_description(
                [record],
                criteria,
                changes,
                {},
            )
        )

        self.assertIn(
            (
                "[TDnet方針候補 / "
                "TDnet本文 confirmed] [adjusted]"
            ),
            description,
        )
        self.assertIn(
            "TDnet本文判定: `confirmed`",
            description,
        )
        self.assertIn(
            "[adjusted] — 利回り",
            description,
        )

    def test_candidate_description_keeps_raw_label(
        self,
    ) -> None:
        criteria = CandidateCriteria(
            min_dividend_yield_percent=Decimal("3"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            require_positive_free_cash_flow=True,
            max_candidates=300,
        )
        record = {
            "security_code": "1234",
            "company_name": "テスト会社",
            "is_adjustment_coverage_complete": False,
            "tdnet_policy_candidate": False,
            "tdnet_dividend_warning": False,
        }

        changes = CandidateChanges(
            comparison_id="raw-label-test",
            is_first_export=False,
            added_candidates=(),
            removed_candidates=(),
        )

        description = (
            build_discord_notification_description(
                [record],
                criteria,
                changes,
                {},
            )
        )

        self.assertIn(
            "`1234` テスト会社 [raw]",
            description,
        )
        self.assertIn(
            "[raw] — 利回り",
            description,
        )

class ExclusionReasonSummaryTest(unittest.TestCase):
    def test_numeric_suffix_is_removed(
        self,
    ) -> None:
        self.assertEqual(
            normalize_exclusion_reason(
                "配当利回りが下限未満（4.20%）"
            ),
            "配当利回りが下限未満",
        )
        self.assertEqual(
            normalize_exclusion_reason(
                "PERが上限超過（30.00倍）"
            ),
            "PERが上限超過",
        )

    def test_plain_reason_is_kept(
        self,
    ) -> None:
        self.assertEqual(
            normalize_exclusion_reason(
                "株式分割等補正データ不足"
            ),
            "株式分割等補正データ不足",
        )

    def test_reasons_are_counted_per_stock(
        self,
    ) -> None:
        removed_reasons = {
            "1111": (
                "配当利回りが下限未満（2.10%）",
                "ROEが下限未満（5.00%）",
            ),
            "2222": (
                "配当利回りが下限未満（1.50%）",
            ),
        }

        lines = summarize_exclusion_reasons(
            removed_reasons
        )

        self.assertEqual(
            lines,
            [
                "除外理由内訳: "
                "配当利回りが下限未満 2件 / "
                "ROEが下限未満 1件"
            ],
        )

    def test_categories_are_limited(
        self,
    ) -> None:
        removed_reasons = {
            f"{index:04d}": (
                f"理由{index}",
            )
            for index in range(7)
        }

        lines = summarize_exclusion_reasons(
            removed_reasons,
            max_categories=3,
        )

        self.assertEqual(
            lines,
            [
                "除外理由内訳: "
                "理由0 1件 / 理由1 1件 / 理由2 1件 / "
                "ほか4種"
            ],
        )

    def test_empty_reasons_return_no_lines(
        self,
    ) -> None:
        self.assertEqual(
            summarize_exclusion_reasons({}),
            [],
        )

    def test_description_contains_summary(
        self,
    ) -> None:
        criteria = CandidateCriteria(
            min_dividend_yield_percent=Decimal("3"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            require_positive_free_cash_flow=True,
            max_candidates=300,
        )
        record = {
            "security_code": "1234",
            "company_name": "テスト会社",
            "is_adjustment_coverage_complete": False,
        }
        changes = CandidateChanges(
            comparison_id="summary-test",
            is_first_export=False,
            added_candidates=(),
            removed_candidates=(
                ("1234", "テスト会社"),
            ),
        )

        description = (
            build_discord_notification_description(
                [],
                criteria,
                changes,
                {
                    "1234": (
                        "配当利回りが下限未満（2.10%）",
                    ),
                },
            )
        )

        self.assertIn(
            "除外理由内訳: 配当利回りが下限未満 1件",
            description,
        )


if __name__ == "__main__":
    unittest.main()
