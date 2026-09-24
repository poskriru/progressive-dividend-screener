"""ライツイシュー等確認対象出力のテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


# ============================================================
# テスト対象の読込
# ============================================================

# 純粋関数のテストではDB接続を使わない。psycopg未導入環境でも
# モジュールを読み込めるよう、database依存だけを差し替える。
database_stub = types.ModuleType("database")
database_stub.create_database_connection = None
sys.modules.setdefault("database", database_stub)

PROJECT_ROOT_PATH = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT_PATH / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from export_unsupported_corporate_actions import (  # noqa: E402
    UNSUPPORTED_CORPORATE_ACTION_HEADERS,
    UNSUPPORTED_CORPORATE_ACTION_SHEET_NAME,
    build_unsupported_corporate_action_rows,
    classify_ex_right_type,
    map_adjustment_status,
)


# ============================================================
# 種別の分類
# ============================================================

class ClassifyExRightTypeTests(unittest.TestCase):
    """ExRTの分類を確認する。"""

    def test_rights_issue_is_classified(self) -> None:
        self.assertEqual(
            classify_ex_right_type("3"),
            "ライツイシュー",
        )

    def test_unknown_type_is_classified(self) -> None:
        self.assertEqual(
            classify_ex_right_type(None),
            "種別不明",
        )

    def test_supported_types_are_rejected(self) -> None:
        for ex_right_type in ("1", "2"):
            with self.subTest(
                ex_right_type=ex_right_type
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "確認対象外のExRT",
                ):
                    classify_ex_right_type(
                        ex_right_type
                    )


# ============================================================
# 補正状態の表示
# ============================================================

class MapAdjustmentStatusTests(unittest.TestCase):
    """補正状態ラベルの変換を確認する。"""

    def test_known_statuses_are_mapped(self) -> None:
        expected = {
            "unsupported_corporate_action": (
                "自動補正対象外"
            ),
            "adjustment_data_incomplete": (
                "補正範囲不足"
            ),
            "complete": "補正完了",
        }

        for status, label in expected.items():
            with self.subTest(status=status):
                self.assertEqual(
                    map_adjustment_status(status),
                    label,
                )

    def test_missing_status_is_displayed(self) -> None:
        self.assertEqual(
            map_adjustment_status(None),
            "判定データなし",
        )

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "未知の補正状態",
        ):
            map_adjustment_status("unknown_status")


# ============================================================
# 出力行
# ============================================================

class BuildRowsTests(unittest.TestCase):
    """Google Sheets出力行の作成を確認する。"""

    def build_record(
        self,
        **overrides,
    ) -> dict[str, object]:
        record = {
            "security_code": "8057",
            "company_name": "内田洋行",
            "market": "プライム",
            "effective_date": date(2026, 2, 1),
            "ex_right_type": "3",
            "adjustment_factor": Decimal(
                "0.8333333333"
            ),
            "source": "J-Quants V2",
            "dividend_adjustment_status": (
                "unsupported_corporate_action"
            ),
        }
        record.update(overrides)
        return record

    def test_row_contains_all_columns(self) -> None:
        rows = build_unsupported_corporate_action_rows(
            [self.build_record()]
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            len(row),
            len(
                UNSUPPORTED_CORPORATE_ACTION_HEADERS
            ),
        )
        self.assertEqual(row[1], "8057")
        self.assertEqual(row[2], "内田洋行")
        self.assertEqual(row[3], "プライム")
        self.assertEqual(row[4], "2026-02-01")
        self.assertEqual(row[5], "ライツイシュー")
        self.assertEqual(row[6], 0.833333)
        self.assertEqual(row[7], "J-Quants V2")
        self.assertEqual(row[8], "自動補正対象外")
        self.assertIn("自動配当補正の対象外", row[9])

    def test_unknown_type_record_is_converted(self) -> None:
        rows = build_unsupported_corporate_action_rows(
            [
                self.build_record(
                    ex_right_type=None,
                    dividend_adjustment_status=None,
                ),
            ]
        )

        row = rows[0]
        self.assertEqual(row[5], "種別不明")
        self.assertEqual(row[8], "判定データなし")

    def test_rows_are_sorted_input_order_kept(
        self,
    ) -> None:
        rows = build_unsupported_corporate_action_rows(
            [
                self.build_record(
                    security_code="1111",
                ),
                self.build_record(
                    security_code="2222",
                ),
            ]
        )

        self.assertEqual(rows[0][1], "1111")
        self.assertEqual(rows[1][1], "2222")

    def test_sheet_name_is_defined(self) -> None:
        self.assertEqual(
            UNSUPPORTED_CORPORATE_ACTION_SHEET_NAME,
            "ライツイシュー等確認対象",
        )


if __name__ == "__main__":
    unittest.main()