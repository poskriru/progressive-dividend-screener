"""
TDnet PDF解析結果のDB保存・処理連携テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import MagicMock, patch


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
    PolicyAnalysis,
    PolicyEvidence,
)

from fetch_tdnet_policy_pdfs import (  # noqa: E402
    ExtractedPdfText,
    FetchedPdf,
    TdnetPdfFetchError,
    TdnetPdfTextExtractionError,
)

from store_tdnet_policy_pdf_analyses import (  # noqa: E402
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FETCH_FAILED,
    ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED,
    TdnetPolicyPdfProcessingResult,
    TdnetPolicyPdfTarget,
    analyze_and_store_tdnet_policy_pdf,
    build_error_message,
    save_processing_result,
    select_targets_requiring_analysis,
    validate_target,
)

# ============================================================
# テストデータ
# ============================================================

def create_target() -> TdnetPolicyPdfTarget:
    """正常な解析対象を作成する。"""

    return TdnetPolicyPdfTarget(
        disclosure_id="140120260101000001",
        security_code="1234",
        published_date=date(2026, 1, 1),
        published_time=time(15, 30),
        company_name="テスト株式会社",
        title="株主還元方針の変更について",
        pdf_url=(
            "https://www.release.tdnet.info/"
            "inbs/140120260101000001.pdf"
        ),
    )


def create_fetched_pdf() -> FetchedPdf:
    """正常な取得済みPDFを作成する。"""

    return FetchedPdf(
        content=b"%PDF-1.7\ntest",
        content_sha256="a" * 64,
        content_type="application/pdf",
        content_length_bytes=15,
        http_status_code=200,
        final_url=(
            "https://www.release.tdnet.info/"
            "inbs/140120260101000001.pdf"
        ),
    )


# ============================================================
# 入力検証
# ============================================================

class TdnetPolicyPdfTargetValidationTest(
    unittest.TestCase
):
    """解析対象の入力検証を確認する。"""

    def test_valid_target_is_accepted(
        self,
    ) -> None:
        validate_target(
            create_target()
        )

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        target = TdnetPolicyPdfTarget(
            disclosure_id="test",
            security_code="123",
            published_date=date(2026, 1, 1),
            published_time=None,
            company_name="テスト株式会社",
            title="方針変更",
            pdf_url=(
                "https://www.release.tdnet.info/"
                "inbs/test.pdf"
            ),
        )

        with self.assertRaises(ValueError):
            validate_target(target)

    def test_empty_disclosure_id_is_rejected(
        self,
    ) -> None:
        target = TdnetPolicyPdfTarget(
            disclosure_id="",
            security_code="1234",
            published_date=date(2026, 1, 1),
            published_time=None,
            company_name="テスト株式会社",
            title="方針変更",
            pdf_url=(
                "https://www.release.tdnet.info/"
                "inbs/test.pdf"
            ),
        )

        with self.assertRaises(ValueError):
            validate_target(target)


# ============================================================
# エラー文字列
# ============================================================

class TdnetPolicyPdfErrorMessageTest(
    unittest.TestCase
):
    """DB保存用エラー文字列を確認する。"""

    def test_error_type_and_message_are_preserved(
        self,
    ) -> None:
        result = build_error_message(
            ValueError("invalid value")
        )

        self.assertEqual(
            result,
            "ValueError: invalid value",
        )

    def test_line_breaks_are_removed(
        self,
    ) -> None:
        result = build_error_message(
            RuntimeError("first\nsecond")
        )

        self.assertEqual(
            result,
            "RuntimeError: first second",
        )


# ============================================================
# DB保存
# ============================================================

class TdnetPolicyPdfDatabaseStorageTest(
    unittest.TestCase
):
    """UPSERTへ渡す値を確認する。"""

    def test_completed_result_is_saved(
        self,
    ) -> None:
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = (
            cursor
        )
        result = TdnetPolicyPdfProcessingResult(
            analysis_status=(
                ANALYSIS_STATUS_COMPLETED
            ),
            policy_classification="confirmed",
            content_sha256="a" * 64,
            content_type="application/pdf",
            content_length_bytes=100,
            http_status_code=200,
            page_count=2,
            extracted_text_length=300,
            matched_phrase="累進配当方針を導入",
            evidence_text=(
                "累進配当方針を導入します。"
            ),
            evidence_page_number=2,
        )

        save_processing_result(
            connection,
            create_target(),
            result,
        )

        cursor.execute.assert_called_once()
        query, parameters = (
            cursor.execute.call_args.args
        )

        self.assertIn(
            "ON CONFLICT (disclosure_id)",
            query,
        )
        self.assertEqual(
            parameters[0],
            "140120260101000001",
        )
        self.assertEqual(
            parameters[13],
            ANALYSIS_STATUS_COMPLETED,
        )
        self.assertEqual(
            parameters[14],
            "confirmed",
        )
        self.assertTrue(
            parameters[19]
        )
        self.assertIsNone(
            parameters[20]
        )


# ============================================================
# 取得・抽出・判定連携
# ============================================================

class TdnetPolicyPdfProcessingTest(
    unittest.TestCase
):
    """1件処理の成功・失敗経路を確認する。"""

    @patch(
        "store_tdnet_policy_pdf_analyses."
        "save_processing_result"
    )
    @patch(
        "store_tdnet_policy_pdf_analyses."
        "classify_policy_pages"
    )
    @patch(
        "store_tdnet_policy_pdf_analyses."
        "extract_pdf_text"
    )
    @patch(
        "store_tdnet_policy_pdf_analyses."
        "fetch_tdnet_pdf"
    )
    def test_successful_pdf_is_classified_and_saved(
        self,
        mocked_fetch,
        mocked_extract,
        mocked_classify,
        mocked_save,
    ) -> None:
        fetched_pdf = create_fetched_pdf()
        extracted_pdf = ExtractedPdfText(
            page_texts=(
                "累進配当方針を導入します。",
            ),
            page_count=1,
            extracted_text_length=14,
        )
        policy_analysis = PolicyAnalysis(
            classification="confirmed",
            evidence=PolicyEvidence(
                page_number=1,
                matched_phrase=(
                    "累進配当方針を導入"
                ),
                evidence_text=(
                    "累進配当方針を導入します。"
                ),
            ),
        )
        mocked_fetch.return_value = fetched_pdf
        mocked_extract.return_value = extracted_pdf
        mocked_classify.return_value = (
            policy_analysis
        )

        result = (
            analyze_and_store_tdnet_policy_pdf(
                MagicMock(),
                MagicMock(),
                create_target(),
            )
        )

        self.assertEqual(
            result.analysis_status,
            ANALYSIS_STATUS_COMPLETED,
        )
        self.assertEqual(
            result.policy_classification,
            "confirmed",
        )
        self.assertEqual(
            result.evidence_page_number,
            1,
        )
        mocked_save.assert_called_once()

    @patch(
        "store_tdnet_policy_pdf_analyses."
        "save_processing_result"
    )
    @patch(
        "store_tdnet_policy_pdf_analyses."
        "fetch_tdnet_pdf"
    )
    def test_fetch_failure_is_saved(
        self,
        mocked_fetch,
        mocked_save,
    ) -> None:
        mocked_fetch.side_effect = (
            TdnetPdfFetchError(
                "HTTP status 404"
            )
        )

        result = (
            analyze_and_store_tdnet_policy_pdf(
                MagicMock(),
                MagicMock(),
                create_target(),
            )
        )

        self.assertEqual(
            result.analysis_status,
            ANALYSIS_STATUS_FETCH_FAILED,
        )
        self.assertIsNone(
            result.policy_classification
        )
        self.assertIn(
            "TdnetPdfFetchError",
            result.last_error,
        )
        mocked_save.assert_called_once()

    @patch(
        "store_tdnet_policy_pdf_analyses."
        "save_processing_result"
    )
    @patch(
        "store_tdnet_policy_pdf_analyses."
        "extract_pdf_text"
    )
    @patch(
        "store_tdnet_policy_pdf_analyses."
        "fetch_tdnet_pdf"
    )
    def test_extraction_failure_keeps_pdf_metadata(
        self,
        mocked_fetch,
        mocked_extract,
        mocked_save,
    ) -> None:
        mocked_fetch.return_value = (
            create_fetched_pdf()
        )
        mocked_extract.side_effect = (
            TdnetPdfTextExtractionError(
                "image only"
            )
        )

        result = (
            analyze_and_store_tdnet_policy_pdf(
                MagicMock(),
                MagicMock(),
                create_target(),
            )
        )

        self.assertEqual(
            result.analysis_status,
            (
                ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED
            ),
        )
        self.assertEqual(
            result.content_sha256,
            "a" * 64,
        )
        self.assertEqual(
            result.http_status_code,
            200,
        )
        self.assertIn(
            "TdnetPdfTextExtractionError",
            result.last_error,
        )
        mocked_save.assert_called_once()


# ============================================================
# 再取得対象選択
# ============================================================

class TdnetPolicyPdfTargetSelectionTest(
    unittest.TestCase
):
    """解析済みPDFの再取得防止を確認する。"""

    def create_connection(
        self,
        stored_rows,
    ):
        """保存済み行を返すDBモックを作成する。"""

        connection = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = stored_rows
        connection.cursor.return_value.__enter__.return_value = (
            cursor
        )

        return connection

    def test_new_target_is_selected(
        self,
    ) -> None:
        connection = self.create_connection([])

        selected, summary = (
            select_targets_requiring_analysis(
                connection,
                [create_target()],
            )
        )

        self.assertEqual(
            selected,
            [create_target()],
        )
        self.assertEqual(
            summary["selected_count"],
            1,
        )

    def test_completed_current_version_is_skipped(
        self,
    ) -> None:
        connection = self.create_connection(
            [
                {
                    "disclosure_id": (
                        "140120260101000001"
                    ),
                    "analysis_status": "completed",
                    "analyzer_version": "v1",
                    "fetch_attempt_count": 1,
                }
            ]
        )

        selected, summary = (
            select_targets_requiring_analysis(
                connection,
                [create_target()],
            )
        )

        self.assertEqual(
            selected,
            [],
        )
        self.assertEqual(
            summary[
                "completed_skipped_count"
            ],
            1,
        )

    def test_failed_target_below_limit_is_selected(
        self,
    ) -> None:
        connection = self.create_connection(
            [
                {
                    "disclosure_id": (
                        "140120260101000001"
                    ),
                    "analysis_status": (
                        "fetch_failed"
                    ),
                    "analyzer_version": "v1",
                    "fetch_attempt_count": 2,
                }
            ]
        )

        selected, summary = (
            select_targets_requiring_analysis(
                connection,
                [create_target()],
                maximum_attempts=3,
            )
        )

        self.assertEqual(
            selected,
            [create_target()],
        )
        self.assertEqual(
            summary["selected_count"],
            1,
        )

    def test_failed_target_at_limit_is_skipped(
        self,
    ) -> None:
        connection = self.create_connection(
            [
                {
                    "disclosure_id": (
                        "140120260101000001"
                    ),
                    "analysis_status": (
                        "text_extraction_failed"
                    ),
                    "analyzer_version": "v1",
                    "fetch_attempt_count": 3,
                }
            ]
        )

        selected, summary = (
            select_targets_requiring_analysis(
                connection,
                [create_target()],
                maximum_attempts=3,
            )
        )

        self.assertEqual(
            selected,
            [],
        )
        self.assertEqual(
            summary[
                "retry_exhausted_count"
            ],
            1,
        )

    def test_old_analyzer_version_is_selected(
        self,
    ) -> None:
        connection = self.create_connection(
            [
                {
                    "disclosure_id": (
                        "140120260101000001"
                    ),
                    "analysis_status": "completed",
                    "analyzer_version": "v0",
                    "fetch_attempt_count": 10,
                }
            ]
        )

        selected, summary = (
            select_targets_requiring_analysis(
                connection,
                [create_target()],
                maximum_attempts=3,
            )
        )

        self.assertEqual(
            selected,
            [create_target()],
        )
        self.assertEqual(
            summary["selected_count"],
            1,
        )

    def test_duplicate_disclosure_id_is_rejected(
        self,
    ) -> None:
        connection = self.create_connection([])

        with self.assertRaises(ValueError):
            select_targets_requiring_analysis(
                connection,
                [
                    create_target(),
                    create_target(),
                ],
            )

    def test_invalid_maximum_attempts_is_rejected(
        self,
    ) -> None:
        connection = self.create_connection([])

        with self.assertRaises(ValueError):
            select_targets_requiring_analysis(
                connection,
                [create_target()],
                maximum_attempts=0,
            )

# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
