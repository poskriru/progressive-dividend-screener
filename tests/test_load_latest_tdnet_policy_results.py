"""
証券コードごとの最新TDnet PDF本文解析結果の
PostgreSQL読込テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date, datetime, time, timezone
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

from load_latest_tdnet_policy_results import (  # noqa: E402
    load_latest_policy_results_with_connection,
    load_latest_tdnet_policy_results,
    normalize_security_codes,
    row_to_latest_policy_result,
)


# ============================================================
# テストデータ
# ============================================================

def create_completed_row(
    *,
    security_code: str = "8057",
    disclosure_id: str = "140120260901529442",
) -> dict:
    """正常なcompleted行を作成する。"""

    return {
        "disclosure_id": disclosure_id,
        "security_code": security_code,
        "published_date": date(
            2026,
            9,
            2,
        ),
        "published_time": time(
            15,
            30,
        ),
        "company_name": "内田洋行",
        "title": (
            "剰余金の配当および配当方針の変更"
            "(累進配当の導入)に関するお知らせ"
        ),
        "pdf_url": (
            "https://www.release.tdnet.info/inbs/"
            f"{disclosure_id}.pdf"
        ),
        "analysis_status": "completed",
        "policy_classification": "confirmed",
        "matched_phrase": "累進配当の導入",
        "evidence_text": (
            "剰余金の配当および配当方針の変更"
            "(累進配当の導入)に関するお知らせ"
        ),
        "evidence_page_number": 1,
        "analyzer_version": "v2",
        "fetch_attempt_count": 2,
        "analyzed_at": datetime(
            2026,
            9,
            20,
            3,
            26,
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
# 証券コード正規化
# ============================================================

class SecurityCodeNormalizationTest(
    unittest.TestCase
):
    """証券コードの正規化を確認する。"""

    def test_codes_are_normalized_deduplicated_and_sorted(
        self,
    ) -> None:
        result = normalize_security_codes(
            [
                " 8057 ",
                "245a",
                "",
                "8057",
            ]
        )

        self.assertEqual(
            result,
            [
                "245A",
                "8057",
            ],
        )

    def test_single_string_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            normalize_security_codes("8057")

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            normalize_security_codes(
                [
                    "8057",
                    "12345",
                ]
            )


# ============================================================
# DB行変換
# ============================================================

class LatestTdnetPolicyResultRowTest(
    unittest.TestCase
):
    """DB行から最新解析結果への変換を確認する。"""

    def test_completed_row_is_converted(
        self,
    ) -> None:
        result = row_to_latest_policy_result(
            create_completed_row()
        )

        self.assertEqual(
            result.security_code,
            "8057",
        )
        self.assertEqual(
            result.disclosure_id,
            "140120260901529442",
        )
        self.assertEqual(
            result.published_date,
            date(2026, 9, 2),
        )
        self.assertEqual(
            result.published_time,
            time(15, 30),
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
            "累進配当の導入",
        )
        self.assertEqual(
            result.evidence_page_number,
            1,
        )
        self.assertEqual(
            result.analyzer_version,
            "v2",
        )

    def test_alphanumeric_security_code_is_normalized(
        self,
    ) -> None:
        row = create_completed_row(
            security_code="245a",
        )

        result = row_to_latest_policy_result(row)

        self.assertEqual(
            result.security_code,
            "245A",
        )

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        row = create_completed_row(
            security_code="80570",
        )

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_missing_published_date_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["published_date"] = None

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_invalid_published_time_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["published_time"] = "15:30"

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_empty_company_name_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["company_name"] = ""

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_empty_title_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["title"] = ""

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_empty_pdf_url_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["pdf_url"] = ""

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)


# ============================================================
# PostgreSQL読込
# ============================================================

class LatestTdnetPolicyResultLoadingTest(
    unittest.TestCase
):
    """PostgreSQLからの最新解析結果読込を確認する。"""

    def test_latest_results_are_loaded(
        self,
    ) -> None:
        connection, cursor = create_connection(
            [
                create_completed_row(),
                create_completed_row(
                    security_code="245A",
                    disclosure_id=(
                        "140120260824524841"
                    ),
                ),
            ]
        )

        results = (
            load_latest_policy_results_with_connection(
                connection,
                [
                    "8057",
                    "245a",
                    "8057",
                ],
            )
        )

        cursor.execute.assert_called_once()
        query, parameters = (
            cursor.execute.call_args.args
        )

        self.assertIn(
            "SELECT DISTINCT ON (security_code)",
            query,
        )
        self.assertIn(
            "WHERE security_code = ANY(%s)",
            query,
        )
        self.assertIn(
            "published_date DESC",
            query,
        )
        self.assertIn(
            "published_time DESC NULLS LAST",
            query,
        )
        self.assertIn(
            "disclosure_id DESC",
            query,
        )
        self.assertEqual(
            parameters,
            (
                [
                    "245A",
                    "8057",
                ],
            ),
        )
        self.assertEqual(
            set(results),
            {
                "245A",
                "8057",
            },
        )
        self.assertEqual(
            results["8057"].policy_classification,
            "confirmed",
        )

    def test_empty_codes_do_not_execute_query(
        self,
    ) -> None:
        connection, cursor = create_connection([])

        results = (
            load_latest_policy_results_with_connection(
                connection,
                [],
            )
        )

        self.assertEqual(results, {})
        cursor.execute.assert_not_called()

    def test_duplicate_database_codes_are_rejected(
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
            load_latest_policy_results_with_connection(
                connection,
                ["8057"],
            )

    def test_unrequested_database_code_is_rejected(
        self,
    ) -> None:
        connection, _ = create_connection(
            [
                create_completed_row(
                    security_code="245A",
                )
            ]
        )

        with self.assertRaises(RuntimeError):
            load_latest_policy_results_with_connection(
                connection,
                ["8057"],
            )

    @patch(
        "load_latest_tdnet_policy_results."
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

        results = load_latest_tdnet_policy_results(
            ["8057"]
        )

        mocked_create_connection.assert_called_once_with(
            "latest_tdnet_policy_results"
        )
        cursor.execute.assert_called_once()
        self.assertEqual(
            list(results),
            ["8057"],
        )

    @patch(
        "load_latest_tdnet_policy_results."
        "create_database_connection"
    )
    def test_empty_public_request_does_not_connect(
        self,
        mocked_create_connection,
    ) -> None:
        results = load_latest_tdnet_policy_results([])

        self.assertEqual(results, {})
        mocked_create_connection.assert_not_called()


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
