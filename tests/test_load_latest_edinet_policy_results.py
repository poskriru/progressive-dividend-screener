"""
証券コードごとの最新EDINET有報配当政策解析結果の
PostgreSQL読込テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock


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

from load_latest_edinet_policy_results import (  # noqa: E402
    load_latest_policy_results_with_connection,
    load_latest_edinet_policy_results,
    normalize_security_codes,
    row_to_latest_policy_result,
)


# ============================================================
# テストデータ
# ============================================================

def create_completed_row(
    *,
    security_code: str = "8057",
    doc_id: str = "S100YIC7",
) -> dict:
    """正常なcompleted行を作成する。"""

    return {
        "doc_id": doc_id,
        "security_code": security_code,
        "submitted_date": date(2026, 6, 24),
        "fiscal_period_end": date(2026, 3, 31),
        "company_name": "内田洋行",
        "pdf_url": (
            "https://disclosure2dl.edinet-fsa.go.jp/"
            f"searchdocument/pdf/{doc_id}.pdf"
        ),
        "analysis_status": "completed",
        "policy_classification": "confirmed",
        "matched_phrase": "連続増配",
        "evidence_text": (
            "当社は連続増配を継続します。"
        ),
        "evidence_page_number": 12,
        "analyzer_version": "v3",
        "analyzed_at": datetime(
            2026,
            11,
            21,
            13,
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
# 証券コード正規化
# ============================================================

class EdinetSecurityCodeNormalizationTest(
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

class LatestEdinetPolicyResultRowTest(
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
            result.doc_id,
            "S100YIC7",
        )
        self.assertEqual(
            result.submitted_date,
            date(2026, 6, 24),
        )
        self.assertEqual(
            result.fiscal_period_end,
            date(2026, 3, 31),
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
            "連続増配",
        )
        self.assertEqual(
            result.evidence_page_number,
            12,
        )
        self.assertEqual(
            result.analyzer_version,
            "v3",
        )

    def test_blank_doc_id_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["doc_id"] = ""

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["security_code"] = "12345"

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_invalid_submitted_date_is_rejected(
        self,
    ) -> None:
        row = create_completed_row()
        row["submitted_date"] = "2026-06-24"

        with self.assertRaises(RuntimeError):
            row_to_latest_policy_result(row)

    def test_optional_fields_can_be_empty(
        self,
    ) -> None:
        row = create_completed_row()
        row["policy_classification"] = None
        row["matched_phrase"] = None
        row["evidence_text"] = None
        row["evidence_page_number"] = None
        row["analyzed_at"] = None
        row["last_error"] = None

        result = row_to_latest_policy_result(row)

        self.assertIsNone(
            result.policy_classification,
        )
        self.assertIsNone(
            result.evidence_page_number,
        )
        self.assertIsNone(
            result.analyzed_at,
        )


# ============================================================
# PostgreSQL読込
# ============================================================

class LatestEdinetPolicyResultLoadTest(
    unittest.TestCase
):
    """証券コードごとの最新解析結果読込を確認する。"""

    def test_latest_result_per_security_code_is_loaded(
        self,
    ) -> None:
        rows = [
            create_completed_row(),
            create_completed_row(
                security_code="1904",
                doc_id="S100YXYZ",
            ),
        ]
        connection, cursor = create_connection(rows)

        results = (
            load_latest_policy_results_with_connection(
                connection,
                ["8057", "1904"],
            )
        )

        self.assertEqual(
            len(results),
            2,
        )
        self.assertEqual(
            results["8057"].doc_id,
            "S100YIC7",
        )
        self.assertEqual(
            results["1904"].doc_id,
            "S100YXYZ",
        )

        parameters = cursor.execute.call_args[0][1]

        self.assertEqual(
            parameters,
            (["1904", "8057"],),
        )

    def test_empty_codes_returns_empty_dict(
        self,
    ) -> None:
        connection, _ = create_connection([])

        results = (
            load_latest_policy_results_with_connection(
                connection,
                [],
            )
        )

        self.assertEqual(
            results,
            {},
        )

    def test_unrequested_code_is_rejected(
        self,
    ) -> None:
        rows = [create_completed_row()]
        connection, _ = create_connection(rows)

        with self.assertRaises(RuntimeError):
            load_latest_policy_results_with_connection(
                connection,
                ["1904"],
            )

    def test_duplicate_code_is_rejected(
        self,
    ) -> None:
        rows = [
            create_completed_row(),
            create_completed_row(doc_id="S100YZZZ"),
        ]
        connection, _ = create_connection(rows)

        with self.assertRaises(RuntimeError):
            load_latest_policy_results_with_connection(
                connection,
                ["8057"],
            )


if __name__ == "__main__":
    unittest.main()
