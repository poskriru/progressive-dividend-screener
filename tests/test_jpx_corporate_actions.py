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
    JpxCorporateAction,
    extract_ratio,
    find_effective_date_in_row,
    find_security_code_in_row,
    normalize_security_code,
    normalize_text,
    parse_action_description,
    parse_jpx_date,
    parse_pdf_table_row,
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
# JPX月次PDFの行解析
# ============================================================

class JpxPdfTableRowParsingTest(unittest.TestCase):
    """JPX月次PDFから抽出された表行を検証する。"""

    def test_split_row_is_converted_to_action(
        self,
    ) -> None:
        row = [
            "プライム",
            "9301",
            "三菱倉庫",
            "Mitsubishi Logistics Corporation",
            "2024.10.30",
            "2024.10.31",
            "1:2 株式分割",
        ]

        action = parse_pdf_table_row(row)

        self.assertEqual(
            action,
            JpxCorporateAction(
                security_code="9301",
                effective_date=date(2024, 10, 30),
                adjustment_factor=Decimal(
                    "0.5000000000"
                ),
                ex_right_type="1",
                description=(
                    "プライム 9301 三菱倉庫 "
                    "Mitsubishi Logistics Corporation "
                    "2024.10.30 2024.10.31 "
                    "1:2 株式分割"
                ),
            ),
        )

    def test_old_pdf_reverse_split_row_is_converted(
        self,
    ) -> None:
        row = [
            "ＪＡＳＤＡＱスタンダード 7901",
            "マツモト",
            "MATSUMOTO INC.",
            "2017.10.27",
            "2017.10.31",
            "10:1 株式併合",
        ]

        action = parse_pdf_table_row(row)

        self.assertIsNotNone(action)
        self.assertEqual(
            action.security_code,
            "7901",
        )
        self.assertEqual(
            action.effective_date,
            date(2017, 10, 27),
        )
        self.assertEqual(
            action.adjustment_factor,
            Decimal("10.0000000000"),
        )
        self.assertEqual(
            action.ex_right_type,
            "2",
        )

    def test_alphanumeric_code_is_found_in_merged_cell(
        self,
    ) -> None:
        row = [
            "TOKYO PRO Market 312A",
            "シンコーホールディングス",
            "2026.07.30",
            "2026.07.31",
            "1:2 株式分割",
        ]

        self.assertEqual(
            find_security_code_in_row(row),
            "312A",
        )

    def test_first_date_is_used_as_effective_date(
        self,
    ) -> None:
        row = [
            "9301",
            "三菱倉庫",
            "2024.10.30",
            "2024.10.31",
            "1:2 株式分割",
        ]

        self.assertEqual(
            find_effective_date_in_row(row),
            date(2024, 10, 30),
        )

    def test_general_meeting_row_is_ignored(
        self,
    ) -> None:
        row = [
            "プライム",
            "4933",
            "Ｉ－ｎｅ",
            "2024.10.02",
            "2024.10.03",
            "臨時株主総会の議決権の行使",
        ]

        self.assertIsNone(
            parse_pdf_table_row(row)
        )

    def test_etf_beneficiary_split_row_is_ignored(
        self,
    ) -> None:
        row = [
            "ETF",
            "2237",
            "iFreeETF",
            "2026.07.03",
            "2026.07.06",
            "1:50 受益権分割",
        ]

        self.assertIsNone(
            parse_pdf_table_row(row)
        )

    def test_reit_investment_unit_split_is_ignored(
        self,
    ) -> None:
        row = [
            "不動産投信",
            "3471",
            "三井不動産ロジスティクスパーク",
            "2024.10.30",
            "2024.10.31",
            "1:4 投資口分割",
        ]

        self.assertIsNone(
            parse_pdf_table_row(row)
        )

    def test_action_without_security_code_is_rejected(
        self,
    ) -> None:
        row = [
            "スタンダード",
            "銘柄名",
            "2024.10.30",
            "2024.10.31",
            "1:2 株式分割",
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "銘柄コード",
        ):
            parse_pdf_table_row(row)

    def test_action_without_effective_date_is_rejected(
        self,
    ) -> None:
        row = [
            "スタンダード",
            "7901",
            "マツモト",
            "10:1 株式併合",
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "権利落ち日",
        ):
            parse_pdf_table_row(row)

    def test_non_list_row_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "listではありません",
        ):
            parse_pdf_table_row(
                ("9301", "1:2 株式分割")
            )

# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
