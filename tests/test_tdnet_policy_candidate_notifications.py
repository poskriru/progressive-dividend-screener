"""累進配当候補のTDnet本文解析Discord表示テスト。"""

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))


from export_progressive_dividend_candidates import (  # noqa: E402
    build_tdnet_policy_discord_lines,
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


if __name__ == "__main__":
    unittest.main()
