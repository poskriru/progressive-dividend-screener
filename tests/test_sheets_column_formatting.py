"""Sheets列書式ユーティリティと比較用日付正規化のテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from compare_indicator_sheets import (  # noqa: E402
    normalize_date_text,
)
from sheets_column_formatting import (  # noqa: E402
    build_column_format_requests,
    to_sheet_serial_value,
)


# ============================================================
# シリアル値変換
# ============================================================

class SheetSerialValueTests(unittest.TestCase):
    """シリアル値変換を確認する。"""

    def test_epoch_date_is_zero(self) -> None:
        self.assertEqual(
            to_sheet_serial_value(
                date(1899, 12, 30)
            ),
            0.0,
        )

    def test_known_date_is_converted(self) -> None:
        self.assertEqual(
            to_sheet_serial_value(
                date(2026, 9, 24)
            ),
            float(
                (date(2026, 9, 24) - date(1899, 12, 30)).days
            ),
        )

    def test_datetime_includes_time_fraction(self) -> None:
        value = datetime(2026, 9, 24, 12, 0, 0)

        serial = to_sheet_serial_value(value)

        base_days = (
            date(2026, 9, 24) - date(1899, 12, 30)
        ).days

        self.assertEqual(
            serial,
            base_days + 0.5,
        )

    def test_timezone_aware_datetime_is_accepted(
        self,
    ) -> None:
        value = datetime(
            2026,
            9,
            24,
            0,
            0,
            0,
        )

        serial = to_sheet_serial_value(
            value.replace(tzinfo=None)
        )

        self.assertEqual(
            serial,
            float(
                (
                    date(2026, 9, 24)
                    - date(1899, 12, 30)
                ).days
            ),
        )

    def test_iso_text_is_converted(self) -> None:
        self.assertEqual(
            to_sheet_serial_value("2026-09-24"),
            float(
                (
                    date(2026, 9, 24)
                    - date(1899, 12, 30)
                ).days
            ),
        )

    def test_none_and_empty_are_blank(self) -> None:
        self.assertEqual(to_sheet_serial_value(None), "")
        self.assertEqual(
            to_sheet_serial_value(""),
            "",
        )

    def test_non_date_text_is_kept(self) -> None:
        self.assertEqual(
            to_sheet_serial_value("未調整"),
            "未調整",
        )


# ============================================================
# 列書式リクエスト
# ============================================================

class ColumnFormatRequestTests(unittest.TestCase):
    """repeatCellリクエストの作成を確認する。"""

    def test_date_pattern_uses_date_type(self) -> None:
        requests = build_column_format_requests(
            123,
            {1: "yyyy-mm-dd"},
        )

        self.assertEqual(len(requests), 1)

        repeat_cell = requests[0]["repeatCell"]

        self.assertEqual(
            repeat_cell["range"]["sheetId"],
            123,
        )
        self.assertEqual(
            repeat_cell["range"]["startColumnIndex"],
            1,
        )
        self.assertEqual(
            repeat_cell["range"]["endColumnIndex"],
            2,
        )
        self.assertEqual(
            repeat_cell["range"]["startRowIndex"],
            1,
        )
        self.assertEqual(
            repeat_cell["cell"]["userEnteredFormat"][
                "numberFormat"
            ],
            {
                "type": "DATE",
                "pattern": "yyyy-mm-dd",
            },
        )

    def test_datetime_pattern_uses_date_time_type(
        self,
    ) -> None:
        requests = build_column_format_requests(
            1,
            {0: "yyyy-mm-dd hh:mm:ss"},
        )

        self.assertEqual(
            requests[0]["repeatCell"]["cell"][
                "userEnteredFormat"
            ]["numberFormat"]["type"],
            "DATE_TIME",
        )

    def test_number_pattern_uses_number_type(
        self,
    ) -> None:
        requests = build_column_format_requests(
            1,
            {8: "#,##0.0"},
        )

        self.assertEqual(
            requests[0]["repeatCell"]["cell"][
                "userEnteredFormat"
            ]["numberFormat"]["type"],
            "NUMBER",
        )

    def test_empty_formats_create_no_requests(
        self,
    ) -> None:
        self.assertEqual(
            build_column_format_requests(1, {}),
            [],
        )

    def test_blank_patterns_are_skipped(self) -> None:
        self.assertEqual(
            build_column_format_requests(
                1,
                {3: ""},
            ),
            [],
        )


# ============================================================
# 比較用日付正規化
# ============================================================

class NormalizeDateTextTests(unittest.TestCase):
    """比較用の日付正規化を確認する。"""

    def test_iso_text_is_normalized(self) -> None:
        self.assertEqual(
            normalize_date_text("2026-09-24"),
            "2026-09-24",
        )

    def test_unpadded_slash_text_is_padded(
        self,
    ) -> None:
        self.assertEqual(
            normalize_date_text("2026/9/24"),
            "2026-09-24",
        )

    def test_serial_number_is_converted(self) -> None:
        expected = (
            date(1899, 12, 30)
            + timedelta(
                days=int(
                    to_sheet_serial_value(
                        date(2026, 9, 24)
                    )
                )
            )
        )

        self.assertEqual(
            normalize_date_text(
                to_sheet_serial_value(
                    date(2026, 9, 24)
                )
            ),
            expected.isoformat(),
        )

    def test_empty_is_empty(self) -> None:
        self.assertEqual(
            normalize_date_text(""),
            "",
        )


if __name__ == "__main__":
    unittest.main()