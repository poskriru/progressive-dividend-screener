"""
TDnet PDF本文解析結果のPostgreSQL読込テスト。
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

from load_tdnet_policy_pdf_analyses import (  # noqa: E402
    load_analysis_results_with_connection,
    load_tdnet_policy_pdf_analysis_results,
    normalize_disclosure_ids,
    row_to_analysis_result,
)


# ============================================================
# テストデータ
# ============================================================

def create_completed_row() -> dict:
    """正常なcompleted行を作成する。"""

    return {
        "disclosure_id": "140120260101000001",
        "analysis_status": "completed",
        "policy_classification": "confirmed",
        "matched_phrase": "累進配当方針を導入",
        "evidence_text": (
            "当社は累進配当方針を導入します。"
        ),
        "evidence_page_number": 2,
        "analyzer_version": "v1",
        "fetch_attempt_count": 1,
        "analyzed_at": datetime(
            2026,
            1,
            1,
            6,
            30,
            tzinfo=timezone.utc,
        ),
        "last_error": None,
    }


def create_connection(
    rows: list[dict],
):
    """指定行を返すDB接続モックを作成する。"""

    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    connection.cursor.return_value.__enter__.return_value = (
        cursor
    )

    return connection, cursor


# ============================================================
# 開示ID正規化
# ============================================================

class TdnetPolicyPdfDisclosureIdTest(
    unittest.TestCase
):
    """開示IDの正規化を確認する。"""

    def test_ids_are_trimmed_deduplicated_and_sorted(
        self,
    ) -> None:
        result = normalize_disclosure_ids(
            [
                " second ",
                "first",
                "",
                "first",
            ]
        )

        self.assertEqual(
            result,
            [
                "first",
                "second",
            ],
        )

    def test_single_string_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            normalize_disclosure_ids(
                "140120260101000001"
            )


# ============================================================
# DB行変換
# ============================================================

class TdnetPolicyPdfAnalysisRowTest(
    unittest.TestCase
):
    """DB行から解析結果への変換を確認する。"""

    def test_completed_row_is_converted(
        self,
    ) -> None:
        result = row_to_analysis_result(
            create_completed_row()
        )

        self.assertEqual(
            result.disclosure_id,
            "140120260101000001",
        )
        self.assertEqual(
            result.analysis_status,
            "completed",
        )
        self.assertEqual(
            result.policy_classification,
            "confirmed",
        )
        self.assertEqual(
            result.matched_phrase,
            "累進配当方針を導入",
        )
        self.assertEqual(
            result.evidence_page_number,
            2,
        )
        self.assertEqual(
            result.fetch_attempt_count,
            1,
        )

    def test_fetch_failure_is_converted(
        self,
    ) -> None:
        row = create_completed_row()
        row.update(
            {
                "analysis_status": "fetch_failed",
                "policy_classification": None,
                "matched_phrase": None,
                "evidence_text": None,
                "evidence_page_number": None,
                "fetch_attempt_count": 3,
                "last_error": (
                    "TdnetPdfFetchError: HTTP status 404"
                ),
            }
        )

        result = row_to_analysis_result(row)

        self.assertEqual(
            result.analysis_status,
            "fetch_failed",
        )
        self.assertIsNone(
            result.policy_classification
        )
        self.assertEqual(
            result.fetch_attempt_count,
            3,
        )
        self.assertIn(
            "TdnetPdfFetchError",
            result.last_error,
        )

    def test_completed_without_classification_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["policy_classification"] = None

        with self.assertRaises(RuntimeError):
            row_to_analysis_result(row)

    def test_incomplete_with_classification_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["analysis_status"] = "fetch_failed"

        with self.assertRaises(RuntimeError):
            row_to_analysis_result(row)

    def test_invalid_status_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["analysis_status"] = "unknown"

        with self.assertRaises(RuntimeError):
            row_to_analysis_result(row)

    def test_invalid_evidence_page_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["evidence_page_number"] = 0

        with self.assertRaises(RuntimeError):
            row_to_analysis_result(row)


# ============================================================
# PostgreSQL読込
# ============================================================

class TdnetPolicyPdfAnalysisLoadingTest(
    unittest.TestCase
):
    """PostgreSQLからの解析結果読込を確認する。"""

    def test_requested_results_are_loaded(
        self,
    ) -> None:
        connection, cursor = create_connection(
            [create_completed_row()]
        )

        results = (
            load_analysis_results_with_connection(
                connection,
                [
                    "140120260101000001",
                    "140120260101000001",
                ],
            )
        )

        cursor.execute.assert_called_once()
        query, parameters = (
            cursor.execute.call_args.args
        )

        self.assertIn(
            "FROM screener.tdnet_policy_pdf_analyses",
            query,
        )
        self.assertIn(
            "WHERE disclosure_id = ANY(%s)",
            query,
        )
        self.assertEqual(
            parameters,
            (
                ["140120260101000001"],
            ),
        )
        self.assertEqual(
            list(results),
            ["140120260101000001"],
        )
        self.assertEqual(
            results[
                "140120260101000001"
            ].policy_classification,
            "confirmed",
        )

    def test_empty_ids_do_not_execute_query(
        self,
    ) -> None:
        connection, cursor = create_connection([])

        results = (
            load_analysis_results_with_connection(
                connection,
                [],
            )
        )

        self.assertEqual(results, {})
        cursor.execute.assert_not_called()

    def test_duplicate_database_rows_are_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        connection, _ = create_connection(
            [
                row,
                dict(row),
            ]
        )

        with self.assertRaises(RuntimeError):
            load_analysis_results_with_connection(
                connection,
                ["140120260101000001"],
            )

    @patch(
        "load_tdnet_policy_pdf_analyses."
        "create_database_connection"
    )
    def test_public_loader_uses_database_connection(
        self,
        mocked_create_connection,
    ) -> None:
        connection, cursor = create_connection(
            [create_completed_row()]
        )
        mocked_create_connection.return_value.__enter__.return_value = (
            connection
        )

        results = (
            load_tdnet_policy_pdf_analysis_results(
                ["140120260101000001"]
            )
        )

        mocked_create_connection.assert_called_once_with(
            "tdnet_policy_pdf_sheet_results"
        )
        cursor.execute.assert_called_once()
        self.assertEqual(
            len(results),
            1,
        )

    @patch(
        "load_tdnet_policy_pdf_analyses."
        "create_database_connection"
    )
    def test_empty_public_request_does_not_connect(
        self,
        mocked_create_connection,
    ) -> None:
        results = (
            load_tdnet_policy_pdf_analysis_results(
                []
            )
        )

        self.assertEqual(results, {})
        mocked_create_connection.assert_not_called()


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
