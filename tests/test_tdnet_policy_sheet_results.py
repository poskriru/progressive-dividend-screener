"""
TDnet PDF本文解析結果のGoogle Sheets向け変換テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import datetime, timezone
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

from format_tdnet_policy_sheet_results import (  # noqa: E402
    POLICY_ANALYSIS_NOT_ANALYZED,
    POLICY_ANALYSIS_NOT_APPLICABLE,
    TDNET_POLICY_ANALYSIS_HEADERS,
    build_policy_analysis_result_counts,
    build_policy_analysis_sheet_values,
)

from load_tdnet_policy_pdf_analyses import (  # noqa: E402
    TdnetPolicyPdfAnalysisResult,
)


# ============================================================
# テストデータ
# ============================================================

def create_analysis_result(
    *,
    disclosure_id: str = "140120260101000001",
    analysis_status: str = "completed",
    policy_classification: str | None = "confirmed",
    matched_phrase: str | None = "累進配当方針を導入",
    evidence_text: str | None = (
        "当社は累進配当方針を導入します。"
    ),
    evidence_page_number: int | None = 2,
) -> TdnetPolicyPdfAnalysisResult:
    """Google Sheets変換テスト用の解析結果を作成する。"""

    return TdnetPolicyPdfAnalysisResult(
        disclosure_id=disclosure_id,
        analysis_status=analysis_status,
        policy_classification=policy_classification,
        matched_phrase=matched_phrase,
        evidence_text=evidence_text,
        evidence_page_number=evidence_page_number,
        analyzer_version="v1",
        fetch_attempt_count=1,
        analyzed_at=datetime(
            2026,
            1,
            1,
            6,
            30,
            tzinfo=timezone.utc,
        ),
        last_error=None,
    )


# ============================================================
# 列定義
# ============================================================

class TdnetPolicySheetHeaderTest(
    unittest.TestCase
):
    """Google Sheetsの追加列を確認する。"""

    def test_analysis_headers_are_stable(
        self,
    ) -> None:
        self.assertEqual(
            TDNET_POLICY_ANALYSIS_HEADERS,
            [
                "本文確認状態",
                "本文判定",
                "本文一致フレーズ",
                "本文根拠",
                "本文根拠ページ",
            ],
        )


# ============================================================
# 行変換
# ============================================================

class TdnetPolicySheetValueTest(
    unittest.TestCase
):
    """解析結果からGoogle Sheets列への変換を確認する。"""

    def test_non_policy_disclosure_is_not_applicable(
        self,
    ) -> None:
        values = build_policy_analysis_sheet_values(
            is_policy_candidate=False,
            analysis_result=None,
        )

        self.assertEqual(
            values,
            [
                POLICY_ANALYSIS_NOT_APPLICABLE,
                "",
                "",
                "",
                "",
            ],
        )

    def test_policy_candidate_without_result_is_not_analyzed(
        self,
    ) -> None:
        values = build_policy_analysis_sheet_values(
            is_policy_candidate=True,
            analysis_result=None,
        )

        self.assertEqual(
            values,
            [
                POLICY_ANALYSIS_NOT_ANALYZED,
                "",
                "",
                "",
                "",
            ],
        )

    def test_confirmed_result_contains_evidence(
        self,
    ) -> None:
        values = build_policy_analysis_sheet_values(
            is_policy_candidate=True,
            analysis_result=create_analysis_result(),
        )

        self.assertEqual(
            values,
            [
                "completed",
                "confirmed",
                "累進配当方針を導入",
                "当社は累進配当方針を導入します。",
                2,
            ],
        )

    def test_manual_review_result_contains_evidence(
        self,
    ) -> None:
        result = create_analysis_result(
            policy_classification="manual_review",
            matched_phrase="累進配当",
            evidence_text=(
                "累進配当を基本とする方針を検討します。"
            ),
            evidence_page_number=4,
        )

        values = build_policy_analysis_sheet_values(
            is_policy_candidate=True,
            analysis_result=result,
        )

        self.assertEqual(
            values,
            [
                "completed",
                "manual_review",
                "累進配当",
                "累進配当を基本とする方針を検討します。",
                4,
            ],
        )

    def test_not_confirmed_result_can_have_no_evidence(
        self,
    ) -> None:
        result = create_analysis_result(
            policy_classification="not_confirmed",
            matched_phrase=None,
            evidence_text=None,
            evidence_page_number=None,
        )

        values = build_policy_analysis_sheet_values(
            is_policy_candidate=True,
            analysis_result=result,
        )

        self.assertEqual(
            values,
            [
                "completed",
                "not_confirmed",
                "",
                "",
                "",
            ],
        )

    def test_fetch_failure_does_not_expose_error_message(
        self,
    ) -> None:
        result = create_analysis_result(
            analysis_status="fetch_failed",
            policy_classification=None,
            matched_phrase=None,
            evidence_text=None,
            evidence_page_number=None,
        )

        values = build_policy_analysis_sheet_values(
            is_policy_candidate=True,
            analysis_result=result,
        )

        self.assertEqual(
            values,
            [
                "fetch_failed",
                "",
                "",
                "",
                "",
            ],
        )

    def test_text_extraction_failure_is_displayed(
        self,
    ) -> None:
        result = create_analysis_result(
            analysis_status=(
                "text_extraction_failed"
            ),
            policy_classification=None,
            matched_phrase=None,
            evidence_text=None,
            evidence_page_number=None,
        )

        values = build_policy_analysis_sheet_values(
            is_policy_candidate=True,
            analysis_result=result,
        )

        self.assertEqual(
            values,
            [
                "text_extraction_failed",
                "",
                "",
                "",
                "",
            ],
        )


# ============================================================
# 集計
# ============================================================

class TdnetPolicySheetCountTest(
    unittest.TestCase
):
    """Google Sheets反映対象の集計を確認する。"""

    def test_results_are_counted_by_status_and_classification(
        self,
    ) -> None:
        results = {
            "confirmed": create_analysis_result(
                disclosure_id="confirmed",
                policy_classification="confirmed",
            ),
            "manual_review": create_analysis_result(
                disclosure_id="manual_review",
                policy_classification="manual_review",
            ),
            "not_confirmed": create_analysis_result(
                disclosure_id="not_confirmed",
                policy_classification="not_confirmed",
                matched_phrase=None,
                evidence_text=None,
                evidence_page_number=None,
            ),
            "fetch_failed": create_analysis_result(
                disclosure_id="fetch_failed",
                analysis_status="fetch_failed",
                policy_classification=None,
                matched_phrase=None,
                evidence_text=None,
                evidence_page_number=None,
            ),
            "text_extraction_failed": (
                create_analysis_result(
                    disclosure_id=(
                        "text_extraction_failed"
                    ),
                    analysis_status=(
                        "text_extraction_failed"
                    ),
                    policy_classification=None,
                    matched_phrase=None,
                    evidence_text=None,
                    evidence_page_number=None,
                )
            ),
        }

        counts = build_policy_analysis_result_counts(
            results
        )

        self.assertEqual(
            counts,
            {
                "result_count": 5,
                "pending_count": 0,
                "completed_count": 3,
                "confirmed_count": 1,
                "not_confirmed_count": 1,
                "manual_review_count": 1,
                "fetch_failed_count": 1,
                "text_extraction_failed_count": 1,
            },
        )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
