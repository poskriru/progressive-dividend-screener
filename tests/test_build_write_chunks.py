"""write_sheetのチャンク分割計画のテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from update_edinet_financials import (  # noqa: E402
    MAX_WRITE_CELLS_PER_REQUEST,
    MAX_WRITE_ROWS_PER_REQUEST,
    build_write_chunks,
)


# ============================================================
# テスト
# ============================================================

class BuildWriteChunksTests(unittest.TestCase):
    """書き込みチャンクの分割を確認する。"""

    def test_small_values_fit_in_one_chunk(
        self,
    ) -> None:
        values = [
            ["a", "b"],
            ["1", "2"],
        ]

        chunks = build_write_chunks(values)

        self.assertEqual(
            chunks,
            [(1, values)],
        )

    def test_empty_values_have_no_chunks(self) -> None:
        self.assertEqual(
            build_write_chunks([]),
            [],
        )

    def test_rows_are_split_by_row_limit(self) -> None:
        values = [
            [str(index)]
            for index in range(12)
        ]

        chunks = build_write_chunks(
            values,
            max_rows=5,
            max_cells=1000,
        )

        self.assertEqual(
            [chunk for _, chunk in chunks],
            [
                values[0:5],
                values[5:10],
                values[10:12],
            ],
        )
        self.assertEqual(
            [start for start, _ in chunks],
            [1, 6, 11],
        )

    def test_wide_rows_are_split_by_cell_limit(
        self,
    ) -> None:
        values = [
            [0] * 3000,
            [1] * 3000,
            [2] * 3000,
        ]

        chunks = build_write_chunks(
            values,
            max_rows=100,
            max_cells=5000,
        )

        self.assertEqual(
            [len(chunk) for _, chunk in chunks],
            [1, 1, 1],
        )
        self.assertEqual(
            [start for start, _ in chunks],
            [1, 2, 3],
        )

    def test_single_row_always_fits(self) -> None:
        values = [[0] * 2000000]

        chunks = build_write_chunks(
            values,
            max_rows=10,
            max_cells=1,
        )

        self.assertEqual(
            chunks,
            [(1, values)],
        )

    def test_default_limits_are_used(self) -> None:
        values = [
            [index]
            for index in range(
                MAX_WRITE_ROWS_PER_REQUEST + 1
            )
        ]

        chunks = build_write_chunks(values)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(
            len(chunks[0][1]),
            MAX_WRITE_ROWS_PER_REQUEST,
        )


if __name__ == "__main__":
    unittest.main()