"""JPX企業行動の表記変換と安全条件を検証する。"""

import hashlib
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch


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
    parse_monthly_pdf,
    parse_pdf_table_row,
    validate_pdf_content,
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
# JPX月次PDF全体の解析
# ============================================================

class JpxMonthlyPdfParsingTest(unittest.TestCase):
    """JPX月次PDF全体の安全な解析を検証する。"""

    def create_page(
        self,
        *,
        text: str,
        tables: list[list[list[object]]],
    ) -> MagicMock:
        """pdfplumberのページを模したモックを作成する。"""

        page = MagicMock()
        page.extract_text.return_value = text
        page.extract_tables.return_value = tables

        return page

    def parse_with_pages(
        self,
        content: bytes,
        pages: list[MagicMock],
    ):
        """指定ページを持つモックPDFを解析する。"""

        pdf = MagicMock()
        pdf.pages = pages
        pdf.__enter__.return_value = pdf
        pdf.__exit__.return_value = False

        with patch(
            "update_jpx_corporate_actions.pdfplumber.open",
            return_value=pdf,
        ):
            return parse_monthly_pdf(content)

    def test_complete_pdf_is_parsed(
        self,
    ) -> None:
        content = b"%PDF-test-content"

        page = self.create_page(
            text=(
                "17 新株落・権利落等一覧 "
                "(2024年10月)"
            ),
            tables=[
                [
                    [
                        "プライム",
                        "9301",
                        "三菱倉庫",
                        "2024.10.30",
                        "2024.10.31",
                        "1:2 株式分割",
                    ],
                    [
                        "スタンダード",
                        "2754",
                        "東葛ホールディングス",
                        "2024.10.17",
                        "2024.10.20",
                        "10:1 株式併合",
                    ],
                ]
            ],
        )

        result = self.parse_with_pages(
            content,
            [page],
        )

        self.assertEqual(
            result.content_sha256,
            hashlib.sha256(content).hexdigest(),
        )
        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.table_count, 1)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(len(result.actions), 2)

        # 権利落ち日、銘柄コードの順で並ぶ。
        self.assertEqual(
            result.actions[0].security_code,
            "2754",
        )
        self.assertEqual(
            result.actions[1].security_code,
            "9301",
        )

    def test_multiple_pages_are_all_parsed(
        self,
    ) -> None:
        content = b"%PDF-multiple-pages"

        first_page = self.create_page(
            text="17 新株落・権利落等一覧",
            tables=[
                [
                    [
                        "9104",
                        "商船三井",
                        "2022.03.30",
                        "2022.03.31",
                        "1:3 分割",
                    ]
                ]
            ],
        )

        second_page = self.create_page(
            text="2/2",
            tables=[
                [
                    [
                        "9301",
                        "三菱倉庫",
                        "2024.10.30",
                        "2024.10.31",
                        "1:2 株式分割",
                    ]
                ]
            ],
        )

        result = self.parse_with_pages(
            content,
            [
                first_page,
                second_page,
            ],
        )

        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.table_count, 2)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(len(result.actions), 2)

    def test_duplicate_identical_action_is_deduplicated(
        self,
    ) -> None:
        content = b"%PDF-duplicate"

        duplicate_row = [
            "9301",
            "三菱倉庫",
            "2024.10.30",
            "2024.10.31",
            "1:2 株式分割",
        ]

        page = self.create_page(
            text="17 新株落・権利落等一覧",
            tables=[
                [
                    duplicate_row,
                    duplicate_row,
                ]
            ],
        )

        result = self.parse_with_pages(
            content,
            [page],
        )

        self.assertEqual(result.row_count, 2)
        self.assertEqual(len(result.actions), 1)

    def test_conflicting_duplicate_action_is_rejected(
        self,
    ) -> None:
        content = b"%PDF-conflict"

        page = self.create_page(
            text="17 新株落・権利落等一覧",
            tables=[
                [
                    [
                        "9301",
                        "三菱倉庫",
                        "2024.10.30",
                        "2024.10.31",
                        "1:2 株式分割",
                    ],
                    [
                        "9301",
                        "三菱倉庫",
                        "2024.10.30",
                        "2024.10.31",
                        "1:3 株式分割",
                    ],
                ]
            ],
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "矛盾する企業行動",
        ):
            self.parse_with_pages(
                content,
                [page],
            )

    def test_non_bytes_content_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "bytesではありません",
        ):
            validate_pdf_content(
                "%PDF-text"
            )

    def test_empty_content_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "内容が空",
        ):
            validate_pdf_content(b"")

    def test_non_pdf_content_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "PDFではありません",
        ):
            validate_pdf_content(
                b"<html>error</html>"
            )

    def test_pdf_without_pages_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "ページがありません",
        ):
            self.parse_with_pages(
                b"%PDF-no-pages",
                [],
            )

    def test_pdf_without_expected_title_is_rejected(
        self,
    ) -> None:
        page = self.create_page(
            text="別のPDF資料",
            tables=[
                [
                    [
                        "9301",
                        "2024.10.30",
                        "1:2 株式分割",
                    ]
                ]
            ],
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "タイトル",
        ):
            self.parse_with_pages(
                b"%PDF-wrong-title",
                [page],
            )

    def test_pdf_without_tables_is_rejected(
        self,
    ) -> None:
        page = self.create_page(
            text="17 新株落・権利落等一覧",
            tables=[],
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "表を1件も",
        ):
            self.parse_with_pages(
                b"%PDF-no-tables",
                [page],
            )

    def test_unexpected_pdfplumber_error_is_wrapped(
        self,
    ) -> None:
        with patch(
            "update_jpx_corporate_actions.pdfplumber.open",
            side_effect=ValueError("broken pdf"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "PDFの解析に失敗",
            ):
                parse_monthly_pdf(
                    b"%PDF-broken"
                )

# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
