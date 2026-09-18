"""JPX企業行動の表記変換と安全条件を検証する。"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


# ============================================================
# テスト対象の読み込み
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"

sys.path.insert(
    0,
    str(SRC_DIRECTORY),
)

from update_jpx_corporate_actions import (  # noqa: E402
    extract_ratio,
    normalize_security_code,
    normalize_text,
    parse_action_description,
    parse_jpx_date,
)


# ============================================================
# 共通変換
# ============================================================

class JpxCommonParsingTest(unittest.TestCase):
    """JPX共通表記の変換を検証する。"""

    def test_normalize_text_removes_line_breaks_and_extra_spaces(
        self,
    ) -> None:
        self.assertEqual(
            normalize_text(
                "  1:2\n株式分割\u3000 "
            ),
            "1:2 株式分割",
        )

    def test_numeric_security_code_is_preserved(
        self,
    ) -> None:
        self.assertEqual(
            normalize_security_code("9301"),
            "9301",
        )

    def test_alphanumeric_security_code_is_preserved(
        self,
    ) -> None:
        self.assertEqual(
            normalize_security_code("312a"),
            "312A",
        )

    def test_excel_numeric_security_code_is_normalized(
        self,
    ) -> None:
        self.assertEqual(
            normalize_security_code("9301.0"),
            "9301",
        )

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "銘柄コード",
        ):
            normalize_security_code("123")

    def test_jpx_dot_date_is_parsed(
        self,
    ) -> None:
        self.assertEqual(
            parse_jpx_date(
                "2024.10.17",
                field_name="権利落ち日",
            ),
            date(2024, 10, 17),
        )

    def test_iso_date_is_parsed(
        self,
    ) -> None:
        self.assertEqual(
            parse_jpx_date(
                "2026-07-30",
                field_name="権利落ち日",
            ),
            date(2026, 7, 30),
        )

    def test_invalid_date_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "日付として読み取れません",
        ):
            parse_jpx_date(
                "2026.02.30",
                field_name="権利落ち日",
            )

    def test_ratio_is_extracted(
        self,
    ) -> None:
        self.assertEqual(
            extract_ratio(
                "1:1.2 分割"
            ),
            (
                Decimal("1"),
                Decimal("1.2"),
            ),
        )


# ============================================================
# 株式分割
# ============================================================

class JpxStockSplitParsingTest(unittest.TestCase):
    """JPXの株式分割表記を検証する。"""

    def test_two_for_one_split_uses_half_factor(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "1:2 株式分割"
            ),
            (
                Decimal("0.5000000000"),
                "1",
            ),
        )

    def test_old_pdf_split_wording_is_supported(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "1:3 分割"
            ),
            (
                Decimal("0.3333333333"),
                "1",
            ),
        )

    def test_decimal_split_ratio_is_supported(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "1:1.2 分割"
            ),
            (
                Decimal("0.8333333333"),
                "1",
            ),
        )

    def test_split_without_ratio_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "株式分割の比率",
        ):
            parse_action_description(
                "株式分割"
            )


# ============================================================
# 株式併合
# ============================================================

class JpxReverseSplitParsingTest(unittest.TestCase):
    """JPXの株式併合表記を検証する。"""

    def test_ten_for_one_reverse_split_uses_ten_factor(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "10:1 株式併合"
            ),
            (
                Decimal("10.0000000000"),
                "2",
            ),
        )

    def test_five_for_one_reverse_split_uses_five_factor(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "5:1 株式併合"
            ),
            (
                Decimal("5.0000000000"),
                "2",
            ),
        )

    def test_reverse_split_without_ratio_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "株式併合の比率",
        ):
            parse_action_description(
                "株式併合"
            )


# ============================================================
# 無償割当て・未対応企業行動
# ============================================================

class JpxOtherActionParsingTest(unittest.TestCase):
    """無償割当てと自動補正対象外の表記を検証する。"""

    def test_free_allotment_adds_new_shares_to_existing_shares(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "1:0.1 株主無償割当て"
            ),
            (
                Decimal("0.9090909091"),
                "1",
            ),
        )

    def test_paid_shareholder_allotment_is_unsupported(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "1:0.1 株主割当て"
            ),
            (
                Decimal("1.0000000000"),
                "3",
            ),
        )

    def test_stock_dividend_is_unsupported(
        self,
    ) -> None:
        self.assertEqual(
            parse_action_description(
                "50:1 株式配当"
            ),
            (
                Decimal("1.0000000000"),
                "3",
            ),
        )

    def test_general_meeting_record_date_is_ignored(
        self,
    ) -> None:
        self.assertIsNone(
            parse_action_description(
                "臨時株主総会の議決権の行使"
            )
        )

    def test_beneficiary_right_split_is_ignored(
        self,
    ) -> None:
        self.assertIsNone(
            parse_action_description(
                "1:50 受益権分割"
            )
        )

    def test_investment_unit_split_is_ignored(
        self,
    ) -> None:
        self.assertIsNone(
            parse_action_description(
                "1:4 投資口分割"
            )
        )

    def test_blank_description_is_ignored(
        self,
    ) -> None:
        self.assertIsNone(
            parse_action_description("")
        )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
