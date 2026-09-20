"""
TDnet PDF本文の累進配当方針判定テスト。
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

from analyze_tdnet_policy_pdfs import (  # noqa: E402
    MAXIMUM_EVIDENCE_LENGTH,
    POLICY_CLASSIFICATION_CONFIRMED,
    POLICY_CLASSIFICATION_MANUAL_REVIEW,
    POLICY_CLASSIFICATION_NOT_CONFIRMED,
    classify_policy_pages,
    compact_text,
    normalize_text,
    split_page_sentences,
)


# ============================================================
# 文字列正規化
# ============================================================

class TdnetPolicyTextNormalizationTest(
    unittest.TestCase
):
    """PDF抽出文字列の正規化を確認する。"""

    def test_normalize_text_normalizes_width_and_spaces(
        self,
    ) -> None:
        result = normalize_text(
            "  ＤＯＥ   ３．０％  "
        )

        self.assertEqual(
            result,
            "DOE 3.0%",
        )

    def test_compact_text_removes_pdf_character_spaces(
        self,
    ) -> None:
        result = compact_text(
            "累 進 配 当 を 基 本 方 針 と す る"
        )

        self.assertEqual(
            result,
            "累進配当を基本方針とする",
        )

    def test_split_page_sentences_preserves_sentences(
        self,
    ) -> None:
        result = split_page_sentences(
            "配当方針を変更します。\n"
            "詳細は次のとおりです。"
        )

        self.assertEqual(
            result,
            (
                "配当方針を変更します。",
                "詳細は次のとおりです。",
            ),
        )


# ============================================================
# confirmed判定
# ============================================================

class TdnetPolicyConfirmedTest(unittest.TestCase):
    """明示的な累進配当方針を確認する。"""

    def test_progressive_policy_adoption_is_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "当社は新たな株主還元方針として、"
                    "累進配当方針を導入します。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIsNotNone(result.evidence)
        self.assertEqual(
            result.evidence.page_number,
            1,
        )
        self.assertIn(
            "累進配当",
            result.evidence.matched_phrase,
        )

    def test_progressive_dividend_adoption_noun_is_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "当社は累進配当の導入を決定しました。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIsNotNone(result.evidence)
        self.assertIn(
            "累進配当の導入",
            result.evidence.matched_phrase,
        )

    def test_actual_adoption_title_is_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "剰余金の配当および配当方針の変更"
                    "(累進配当の導入)に関するお知らせ"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIsNotNone(result.evidence)
        self.assertEqual(
            result.evidence.matched_phrase,
            "累進配当の導入",
        )

    def test_progressive_policy_as_basic_policy_is_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "安定的な利益還元を重視し、"
                    "累進配当を基本方針とします。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )

    def test_no_dividend_reduction_policy_is_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "株主還元については、"
                    "原則として減配を行わない方針です。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )

    def test_spaced_pdf_text_is_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "累 進 配 当 方 針 を "
                    "導 入 し ま す。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )

    def test_evidence_page_number_is_preserved(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "1ページ目には会社概要を記載します。",
                "2ページ目で累進配当政策を採用します。",
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIsNotNone(result.evidence)
        self.assertEqual(
            result.evidence.page_number,
            2,
        )

    def test_clear_policy_has_priority_over_ambiguous_reference(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "今後、累進配当の導入を検討します。",
                "取締役会で累進配当方針を採用しました。",
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIsNotNone(result.evidence)
        self.assertEqual(
            result.evidence.page_number,
            2,
        )

# ============================================================
# manual_review判定
# ============================================================

class TdnetPolicyManualReviewTest(
    unittest.TestCase
):
    """曖昧な方針表現を自動確定しないことを確認する。"""

    def test_future_consideration_requires_manual_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "今後、累進配当の導入を検討します。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )
        self.assertIsNotNone(result.evidence)

    def test_planned_adoption_requires_manual_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "来年度から累進配当の導入を予定しています。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )
        self.assertIsNotNone(result.evidence)

    def test_policy_change_consideration_requires_manual_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "累進配当方針への変更を"
                    "今後検討します。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )

    def test_policy_goal_requires_manual_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "将来的に累進配当を目指します。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )

    def test_policy_withdrawal_requires_manual_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "従来の累進配当方針を廃止します。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )

    def test_policy_review_requires_manual_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "累進配当方針の見直しを行います。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )

    def test_plain_progressive_dividend_reference_requires_review(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "資料では累進配当について説明します。"
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_MANUAL_REVIEW,
        )

# ============================================================
# not_confirmed判定
# ============================================================

class TdnetPolicyNotConfirmedTest(
    unittest.TestCase
):
    """累進配当方針を確認できない本文を確認する。"""

    def test_doe_only_is_not_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "株主資本配当率DOE3.0%以上を"
                    "目安として配当を実施します。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_NOT_CONFIRMED,
        )
        self.assertIsNone(result.evidence)

    def test_ordinary_dividend_policy_is_not_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "安定配当と業績連動配当を"
                    "組み合わせて株主還元を行います。"
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_NOT_CONFIRMED,
        )

    def test_empty_pages_are_not_confirmed(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                "",
                "   ",
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_NOT_CONFIRMED,
        )


# ============================================================
# 証跡文字数
# ============================================================

class TdnetPolicyEvidenceLengthTest(
    unittest.TestCase
):
    """判定根拠が保存上限を超えないことを確認する。"""

    def test_long_evidence_is_truncated(
        self,
    ) -> None:
        result = classify_policy_pages(
            [
                (
                    "前置き" * 200
                    + "累進配当方針を導入します。"
                    + "後書き" * 200
                )
            ]
        )

        self.assertEqual(
            result.classification,
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIsNotNone(result.evidence)
        self.assertLessEqual(
            len(result.evidence.evidence_text),
            MAXIMUM_EVIDENCE_LENGTH,
        )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
