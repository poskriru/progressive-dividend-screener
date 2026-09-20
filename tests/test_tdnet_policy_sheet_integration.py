"""
TDnet PDF本文解析とGoogle Sheets同期の実処理接続テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import datetime, timezone
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

import update_tdnet_dividend_disclosures as tdnet_update  # noqa: E402

from load_tdnet_policy_pdf_analyses import (  # noqa: E402
    TdnetPolicyPdfAnalysisResult,
)

from sync_tdnet_policy_analysis_sheets import (  # noqa: E402
    TDNET_DISCLOSURE_BASE_HEADERS,
    TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
)


# ============================================================
# テストデータ
# ============================================================

def create_disclosure(
) -> tdnet_update.TdnetDisclosure:
    """統合テスト用のTDnet開示を作成する。"""

    return tdnet_update.TdnetDisclosure(
        disclosure_id="140120260101000001",
        published_date="2026-01-01",
        published_time="15:30",
        security_code="1234",
        company_name="テスト株式会社",
        title="株主還元方針の変更について",
        category="配当・株主還元方針",
        is_policy_candidate=True,
        matched_keywords=(
            "株主還元方針",
        ),
        pdf_url=(
            "https://www.release.tdnet.info/"
            "inbs/140120260101000001.pdf"
        ),
        exchange="東",
    )


def create_analysis_result(
) -> TdnetPolicyPdfAnalysisResult:
    """統合テスト用のPDF本文解析結果を作成する。"""

    return TdnetPolicyPdfAnalysisResult(
        disclosure_id="140120260101000001",
        analysis_status="completed",
        policy_classification="confirmed",
        matched_phrase="累進配当方針を導入",
        evidence_text=(
            "当社は累進配当方針を導入します。"
        ),
        evidence_page_number=2,
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


def create_pdf_summary() -> dict[str, int]:
    """PDF解析処理の正常な集計を作成する。"""

    return {
        "target_count": 1,
        "processing_target_count": 1,
        "completed_skipped_count": 0,
        "retry_exhausted_count": 0,
        "completed_count": 1,
        "confirmed_count": 1,
        "not_confirmed_count": 0,
        "manual_review_count": 0,
        "fetch_failed_count": 0,
        "text_extraction_failed_count": 0,
    }


def create_sheet_summary() -> dict[str, int]:
    """Google Sheets同期処理の正常な集計を作成する。"""

    return {
        "disclosure_row_count": 1,
        "policy_row_count": 1,
        "result_count": 1,
        "pending_count": 0,
        "completed_count": 1,
        "confirmed_count": 1,
        "not_confirmed_count": 0,
        "manual_review_count": 0,
        "fetch_failed_count": 0,
        "text_extraction_failed_count": 0,
    }


# ============================================================
# シート列移行
# ============================================================

class TdnetDisclosureSheetMigrationTest(
    unittest.TestCase
):
    """従来12列と新17列の読込を確認する。"""

    @patch.object(
        tdnet_update,
        "read_sheet",
    )
    @patch.object(
        tdnet_update,
        "get_or_create_sheet",
    )
    def test_legacy_twelve_columns_are_accepted(
        self,
        mocked_get_or_create_sheet,
        mocked_read_sheet,
    ) -> None:
        values = [
            TDNET_DISCLOSURE_BASE_HEADERS,
            [
                "140120260101000001",
                "2026-01-01",
                "15:30",
                "1234",
                "テスト株式会社",
                "株主還元方針の変更について",
                "配当・株主還元方針",
                True,
                "株主還元方針",
                (
                    "https://www.release.tdnet.info/"
                    "inbs/test.pdf"
                ),
                "東",
                "TDnet適時開示情報閲覧サービス",
            ],
        ]
        mocked_read_sheet.return_value = values

        result = (
            tdnet_update.prepare_tdnet_disclosure_sheet(
                MagicMock(),
                "spreadsheet-id",
            )
        )

        self.assertEqual(result, values)
        mocked_get_or_create_sheet.assert_called_once()

    @patch.object(
        tdnet_update,
        "read_sheet",
    )
    @patch.object(
        tdnet_update,
        "get_or_create_sheet",
    )
    def test_expanded_seventeen_columns_are_accepted(
        self,
        mocked_get_or_create_sheet,
        mocked_read_sheet,
    ) -> None:
        values = [
            TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
        ]
        mocked_read_sheet.return_value = values

        result = (
            tdnet_update.prepare_tdnet_disclosure_sheet(
                MagicMock(),
                "spreadsheet-id",
            )
        )

        self.assertEqual(result, values)
        mocked_get_or_create_sheet.assert_called_once()

    @patch.object(
        tdnet_update,
        "read_sheet",
    )
    @patch.object(
        tdnet_update,
        "get_or_create_sheet",
    )
    def test_unknown_headers_are_rejected(
        self,
        mocked_get_or_create_sheet,
        mocked_read_sheet,
    ) -> None:
        mocked_read_sheet.return_value = [
            [
                "不明な列",
            ]
        ]

        with self.assertRaises(RuntimeError):
            tdnet_update.prepare_tdnet_disclosure_sheet(
                MagicMock(),
                "spreadsheet-id",
            )


# ============================================================
# 更新処理接続
# ============================================================

class TdnetPolicySheetUpdateIntegrationTest(
    unittest.TestCase
):
    """TDnet更新処理とPDF解析・シート同期の接続を確認する。"""

    def test_analysis_results_are_loaded_and_synced(
        self,
    ) -> None:
        disclosure = create_disclosure()
        analysis_result = create_analysis_result()
        existing_values = [
            TDNET_DISCLOSURE_BASE_HEADERS,
        ]

        with patch.object(
            tdnet_update,
            "get_lookback_days",
            return_value=7,
        ), patch.object(
            tdnet_update,
            "prepare_tdnet_disclosure_sheet",
            return_value=existing_values,
        ), patch.object(
            tdnet_update,
            "fetch_tdnet_dividend_disclosures",
            return_value=[disclosure],
        ), patch.object(
            tdnet_update,
            "append_new_disclosures",
            return_value=[disclosure],
        ), patch.object(
            tdnet_update,
            "load_all_stored_disclosures",
            return_value=[disclosure],
        ), patch.object(
            tdnet_update,
            "notify_new_tdnet_disclosures",
        ) as mocked_notify, patch.object(
            tdnet_update,
            "analyze_fetched_policy_disclosures",
            return_value=create_pdf_summary(),
        ) as mocked_analyze, patch.object(
            tdnet_update,
            "load_tdnet_policy_pdf_analysis_results",
            return_value={
                disclosure.disclosure_id: (
                    analysis_result
                ),
            },
        ) as mocked_load_results, patch.object(
            tdnet_update,
            "sync_tdnet_policy_analysis_sheets",
            return_value=create_sheet_summary(),
        ) as mocked_sync:
            result = (
                tdnet_update
                .update_tdnet_dividend_disclosures(
                    MagicMock(),
                    "spreadsheet-id",
                )
            )

        mocked_notify.assert_called_once_with(
            [disclosure]
        )
        mocked_analyze.assert_called_once_with(
            [disclosure]
        )
        mocked_load_results.assert_called_once_with(
            [
                "140120260101000001",
            ]
        )
        mocked_sync.assert_called_once()
        sync_arguments = mocked_sync.call_args.args

        self.assertEqual(
            sync_arguments[1],
            "spreadsheet-id",
        )
        self.assertEqual(
            sync_arguments[2],
            [disclosure],
        )
        self.assertEqual(
            sync_arguments[3],
            {
                disclosure.disclosure_id: (
                    analysis_result
                ),
            },
        )
        self.assertEqual(
            result["stored_disclosure_count"],
            1,
        )
        self.assertEqual(
            result["policy_candidate_count"],
            1,
        )
        self.assertEqual(
            result["sheet_confirmed_count"],
            1,
        )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
