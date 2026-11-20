"""
TDnet配当関連開示の表題分類テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from pathlib import Path


# ============================================================
# テスト対象を読み込むためのパス設定
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRECTORY = PROJECT_ROOT / "src"

if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(
        0,
        str(SOURCE_DIRECTORY),
    )


# ============================================================
# テスト対象
# ============================================================

from update_tdnet_dividend_disclosures import (  # noqa: E402
    classify_disclosure_title,
)


# ============================================================
# 表題分類テスト
# ============================================================

class ClassifyDisclosureTitleTest(
    unittest.TestCase
):
    """表題キーワードによる分類を確認する。"""

    def test_midterm_plan_title_is_policy_candidate(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            matched_keywords,
        ) = classify_disclosure_title(
            "中期経営計画策定に関するお知らせ"
        )

        self.assertEqual(
            category,
            "中期経営計画",
        )
        self.assertTrue(
            is_policy_candidate,
        )
        self.assertIn(
            "経営計画",
            matched_keywords,
        )

    def test_longterm_plan_title_is_policy_candidate(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            _,
        ) = classify_disclosure_title(
            "長期経営ビジョンおよび第2次中期経営計画の策定について"
        )

        self.assertEqual(
            category,
            "中期経営計画",
        )
        self.assertTrue(
            is_policy_candidate,
        )

    def test_plan_title_with_dividend_forecast_keeps_category(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            _,
        ) = classify_disclosure_title(
            "中期経営計画策定及び配当予想の修正に関するお知らせ"
        )

        self.assertEqual(
            category,
            "配当予想",
        )
        self.assertTrue(
            is_policy_candidate,
        )

    def test_policy_title_category_is_unchanged(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            _,
        ) = classify_disclosure_title(
            "配当方針の変更に関するお知らせ"
        )

        self.assertEqual(
            category,
            "配当・株主還元方針",
        )
        self.assertTrue(
            is_policy_candidate,
        )

    def test_ordinary_dividend_title_is_not_policy_candidate(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            _,
        ) = classify_disclosure_title(
            "剰余金の配当(中間配当)に関するお知らせ"
        )

        self.assertEqual(
            category,
            "配当・株主還元",
        )
        self.assertFalse(
            is_policy_candidate,
        )

    def test_explanation_meeting_title_is_policy_candidate(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            _,
        ) = classify_disclosure_title(
            "2027年3月期第2四半期 決算説明会資料"
        )

        self.assertEqual(
            category,
            "決算説明会",
        )
        self.assertTrue(
            is_policy_candidate,
        )

    def test_shareholder_return_title_is_policy_candidate(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            _,
        ) = classify_disclosure_title(
            "株主還元に関するお知らせ"
        )

        self.assertEqual(
            category,
            "配当・株主還元方針",
        )
        self.assertTrue(
            is_policy_candidate,
        )

    def test_unrelated_title_is_not_captured(
        self,
    ) -> None:
        (
            category,
            is_policy_candidate,
            matched_keywords,
        ) = classify_disclosure_title(
            "役員人事に関するお知らせ"
        )

        self.assertEqual(
            category,
            "",
        )
        self.assertFalse(
            is_policy_candidate,
        )
        self.assertEqual(
            matched_keywords,
            (),
        )


if __name__ == "__main__":
    unittest.main()
