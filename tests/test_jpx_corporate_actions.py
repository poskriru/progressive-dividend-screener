"""JPX企業行動の表記変換と安全条件を検証する。"""

import hashlib
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

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
    MAX_HTML_CONTENT_BYTES,
    JpxCorporateAction,
    JpxMonthlyPdfSource,
    ParsedJpxCoverage,
    ParsedJpxMonthlySource,
    ParsedJpxPdf,
    build_month_range,
    discover_monthly_pdf_sources,
    download_and_parse_monthly_sources,
    download_monthly_page,
    download_monthly_pdf,
    extract_month_from_pdf_url,
    extract_ratio,
    find_effective_date_in_row,
    find_security_code_in_row,
    merge_monthly_pdf_sources,
    next_month,
    normalize_month,
    normalize_security_code,
    normalize_text,
    parse_action_description,
    parse_jpx_date,
    parse_monthly_page_urls,
    parse_monthly_pdf,
    parse_monthly_pdf_sources,
    parse_pdf_table_row,
    select_monthly_pdf_sources,
    validate_html_content,
    validate_jpx_monthly_page_url,
    validate_jpx_monthly_pdf_url,
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
# JPX月次PDFのダウンロード
# ============================================================

class JpxMonthlyPdfDownloadTest(unittest.TestCase):
    """JPX月次PDFのURL制限と取得処理を検証する。"""

    VALID_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "example-att/17_kenri2607.pdf"
    )

    def create_response(
        self,
        *,
        status_code: int = 200,
        url: str | None = None,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> MagicMock:
        """requests.Responseを模したモックを作成する。"""

        response = MagicMock(
            spec=requests.Response
        )
        response.status_code = status_code
        response.url = url or self.VALID_URL
        response.headers = headers or {}
        response.iter_content.return_value = iter(
            chunks
            if chunks is not None
            else [b"%PDF-test"]
        )

        if status_code >= 400:
            response.raise_for_status.side_effect = (
                requests.HTTPError(
                    f"HTTP {status_code}",
                    response=response,
                )
            )
        else:
            response.raise_for_status.return_value = None

        return response

    def test_official_monthly_pdf_url_is_accepted(
        self,
    ) -> None:
        self.assertEqual(
            validate_jpx_monthly_pdf_url(
                self.VALID_URL
            ),
            self.VALID_URL,
        )

    def test_http_url_is_rejected(
        self,
    ) -> None:
        invalid_url = self.VALID_URL.replace(
            "https://",
            "http://",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "https",
        ):
            validate_jpx_monthly_pdf_url(
                invalid_url
            )

    def test_other_host_is_rejected(
        self,
    ) -> None:
        invalid_url = self.VALID_URL.replace(
            "www.jpx.co.jp",
            "example.com",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            validate_jpx_monthly_pdf_url(
                invalid_url
            )

    def test_host_suffix_attack_is_rejected(
        self,
    ) -> None:
        invalid_url = self.VALID_URL.replace(
            "www.jpx.co.jp",
            "www.jpx.co.jp.example.com",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            validate_jpx_monthly_pdf_url(
                invalid_url
            )

    def test_non_standard_port_is_rejected(
        self,
    ) -> None:
        invalid_url = self.VALID_URL.replace(
            "www.jpx.co.jp",
            "www.jpx.co.jp:8443",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "標準HTTPS",
        ):
            validate_jpx_monthly_pdf_url(
                invalid_url
            )

    def test_query_string_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "クエリ",
        ):
            validate_jpx_monthly_pdf_url(
                self.VALID_URL + "?download=1"
            )

    def test_path_traversal_is_rejected(
        self,
    ) -> None:
        invalid_url = (
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "../secret.pdf"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "不正なパス",
        ):
            validate_jpx_monthly_pdf_url(
                invalid_url
            )

    def test_encoded_path_traversal_is_rejected(
        self,
    ) -> None:
        invalid_url = (
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "%2e%2e/secret.pdf"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "不正なパス",
        ):
            validate_jpx_monthly_pdf_url(
                invalid_url
            )

    def test_pdf_is_downloaded_in_streaming_mode(
        self,
    ) -> None:
        response = self.create_response(
            chunks=[
                b"%PDF-",
                b"streamed",
            ]
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                return_value=response,
            ) as get_mock:
                content = download_monthly_pdf(
                    session,
                    self.VALID_URL,
                )

        self.assertEqual(
            content,
            b"%PDF-streamed",
        )
        get_mock.assert_called_once()
        self.assertTrue(
            get_mock.call_args.kwargs["stream"]
        )
        response.close.assert_called_once()

    def test_redirect_to_other_host_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            url=(
                "https://example.com/"
                "17_kenri2607.pdf"
            )
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                return_value=response,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "ホスト",
                ):
                    download_monthly_pdf(
                        session,
                        self.VALID_URL,
                    )

        response.close.assert_called_once()

    def test_declared_oversized_pdf_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            headers={
                "Content-Length": str(
                    25 * 1024 * 1024 + 1
                )
            }
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                return_value=response,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Content-Lengthが上限",
                ):
                    download_monthly_pdf(
                        session,
                        self.VALID_URL,
                    )

        response.iter_content.assert_not_called()
        response.close.assert_called_once()

    def test_actual_oversized_pdf_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            chunks=[
                b"%PDF-",
                b"1234567890",
            ]
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                return_value=response,
            ):
                with patch(
                    "update_jpx_corporate_actions."
                    "MAX_PDF_CONTENT_BYTES",
                    8,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "実データサイズが上限",
                    ):
                        download_monthly_pdf(
                            session,
                            self.VALID_URL,
                        )

        response.close.assert_called_once()

    def test_non_pdf_response_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            chunks=[
                b"<html>",
                b"error</html>",
            ]
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                return_value=response,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "PDFではありません",
                ):
                    download_monthly_pdf(
                        session,
                        self.VALID_URL,
                    )

        response.close.assert_called_once()

    def test_http_404_is_not_retried(
        self,
    ) -> None:
        response = self.create_response(
            status_code=404
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                return_value=response,
            ) as get_mock:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "取得に失敗",
                ):
                    download_monthly_pdf(
                        session,
                        self.VALID_URL,
                    )

        self.assertEqual(
            get_mock.call_count,
            1,
        )
        response.close.assert_called_once()

    def test_http_500_is_retried_then_succeeds(
        self,
    ) -> None:
        failed_response = self.create_response(
            status_code=500
        )
        successful_response = self.create_response(
            chunks=[b"%PDF-success"]
        )

        with requests.Session() as session:
            with patch.object(
                session,
                "get",
                side_effect=[
                    failed_response,
                    successful_response,
                ],
            ) as get_mock:
                with patch(
                    "update_jpx_corporate_actions."
                    "time.sleep"
                ) as sleep_mock:
                    content = download_monthly_pdf(
                        session,
                        self.VALID_URL,
                    )

        self.assertEqual(
            content,
            b"%PDF-success",
        )
        self.assertEqual(
            get_mock.call_count,
            2,
        )
        sleep_mock.assert_called_once()
        failed_response.close.assert_called_once()
        successful_response.close.assert_called_once()

    def test_invalid_session_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "requests.Session",
        ):
            download_monthly_pdf(
                MagicMock(),
                self.VALID_URL,
            )

# ============================================================
# JPX統計月報ページ解析テスト
# ============================================================

class JpxMonthlyPdfSourceParsingTest(unittest.TestCase):
    """JPX統計月報ページからのPDFリンク抽出を検証する。"""

    INDEX_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/index.html"
    )

    ARCHIVE_2022_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "00-archives-04.html"
    )

    PDF_2022_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "nlsgeu000006c4e0-att/18_kenri2203.pdf"
    )

    PDF_2024_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "mklp77000000luwj-att/17_kenri2410.pdf"
    )

    def test_index_page_url_is_allowed(
        self,
    ) -> None:
        self.assertEqual(
            validate_jpx_monthly_page_url(
                self.INDEX_URL
            ),
            self.INDEX_URL,
        )

    def test_archive_page_url_is_allowed(
        self,
    ) -> None:
        self.assertEqual(
            validate_jpx_monthly_page_url(
                self.ARCHIVE_2022_URL
            ),
            self.ARCHIVE_2022_URL,
        )

    def test_invalid_page_urls_are_rejected(
        self,
    ) -> None:
        invalid_urls = (
            "",
            "http://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/index.html",
            "https://example.com/"
            "markets/statistics-equities/monthly/index.html",
            "https://user:password@www.jpx.co.jp/"
            "markets/statistics-equities/monthly/index.html",
            "https://www.jpx.co.jp:444/"
            "markets/statistics-equities/monthly/index.html",
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/",
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "00-archives-1.html",
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "../index.html",
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "%2e%2e/index.html",
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "index.html?year=2024",
            "https://www.jpx.co.jp/"
            "markets/statistics-equities/monthly/"
            "index.html#monthly",
        )

        for invalid_url in invalid_urls:
            with self.subTest(url=invalid_url):
                with self.assertRaises(RuntimeError):
                    validate_jpx_monthly_page_url(
                        invalid_url
                    )

    def test_non_string_page_url_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "str",
        ):
            validate_jpx_monthly_page_url(None)

    def test_month_is_extracted_from_old_pdf_name(
        self,
    ) -> None:
        self.assertEqual(
            extract_month_from_pdf_url(
                self.PDF_2022_URL
            ),
            date(2022, 3, 1),
        )

    def test_month_is_extracted_from_new_pdf_name(
        self,
    ) -> None:
        self.assertEqual(
            extract_month_from_pdf_url(
                self.PDF_2024_URL
            ),
            date(2024, 10, 1),
        )

    def test_unrelated_pdf_name_has_no_month(
        self,
    ) -> None:
        self.assertIsNone(
            extract_month_from_pdf_url(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                "example-att/01_statistics2410.pdf"
            )
        )

    def test_official_pdf_links_are_parsed_and_sorted(
        self,
    ) -> None:
        html = f"""
        <html>
          <body>
            <a href="{self.PDF_2024_URL}">
              2024年10月 新株落・権利落等一覧
            </a>
            <a href="nlsgeu000006c4e0-att/18_kenri2203.pdf">
              2022年3月 新株落・権利落等一覧
            </a>
            <a href="example-att/01_statistics2410.pdf">
              その他統計
            </a>
          </body>
        </html>
        """

        sources = parse_monthly_pdf_sources(
            html,
            page_url=self.ARCHIVE_2022_URL,
        )

        self.assertEqual(
            sources,
            (
                JpxMonthlyPdfSource(
                    coverage_month=date(2022, 3, 1),
                    source_url=self.PDF_2022_URL,
                ),
                JpxMonthlyPdfSource(
                    coverage_month=date(2024, 10, 1),
                    source_url=self.PDF_2024_URL,
                ),
            ),
        )

    def test_same_pdf_link_is_deduplicated(
        self,
    ) -> None:
        html = f"""
        <html>
          <body>
            <a href="{self.PDF_2022_URL}">
              PDF
            </a>
            <a href="{self.PDF_2022_URL}">
              PDF再掲
            </a>
          </body>
        </html>
        """

        sources = parse_monthly_pdf_sources(
            html,
            page_url=self.ARCHIVE_2022_URL,
        )

        self.assertEqual(
            sources,
            (
                JpxMonthlyPdfSource(
                    coverage_month=date(2022, 3, 1),
                    source_url=self.PDF_2022_URL,
                ),
            ),
        )

    def test_different_pdfs_for_same_month_are_rejected(
        self,
    ) -> None:
        html = """
        <html>
          <body>
            <a href="first-att/18_kenri2203.pdf">
              最初のPDF
            </a>
            <a href="second-att/18_kenri2203.pdf">
              別のPDF
            </a>
          </body>
        </html>
        """

        with self.assertRaisesRegex(
            RuntimeError,
            "同一対象年月",
        ):
            parse_monthly_pdf_sources(
                html,
                page_url=self.ARCHIVE_2022_URL,
            )

    def test_external_pdf_url_is_rejected(
        self,
    ) -> None:
        html = """
        <html>
          <body>
            <a href="https://example.com/
            markets/statistics-equities/monthly/
            example-att/18_kenri2203.pdf">
              外部PDF
            </a>
          </body>
        </html>
        """

        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            parse_monthly_pdf_sources(
                html,
                page_url=self.ARCHIVE_2022_URL,
            )

    def test_page_without_target_pdf_is_rejected(
        self,
    ) -> None:
        html = """
        <html>
          <body>
            <a href="example-att/01_statistics2203.pdf">
              その他統計
            </a>
          </body>
        </html>
        """

        with self.assertRaisesRegex(
            RuntimeError,
            "取得できませんでした",
        ):
            parse_monthly_pdf_sources(
                html,
                page_url=self.ARCHIVE_2022_URL,
            )

    def test_empty_html_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "空",
        ):
            parse_monthly_pdf_sources(
                "",
                page_url=self.ARCHIVE_2022_URL,
            )

    def test_non_string_html_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "str",
        ):
            parse_monthly_pdf_sources(
                None,  # type: ignore[arg-type]
                page_url=self.ARCHIVE_2022_URL,
            )

# ============================================================
# JPX統計月報ページ取得テスト
# ============================================================

class JpxMonthlyPageDownloadTest(unittest.TestCase):
    """JPX統計月報ページの安全な取得を検証する。"""

    INDEX_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/index.html"
    )

    ARCHIVE_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "00-archives-04.html"
    )

    HTML_CONTENT = (
        "<!doctype html>"
        "<html lang=\"ja\">"
        "<head><title>月間相場表</title></head>"
        "<body>新株落・権利落等一覧</body>"
        "</html>"
    ).encode("utf-8")

    @staticmethod
    def create_response(
        *,
        url: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> MagicMock:
        """HTTPレスポンスのモックを作成する。"""

        response = MagicMock(
            spec=requests.Response
        )
        response.url = url
        response.status_code = status_code
        response.headers = headers or {
            "Content-Type": (
                "text/html; charset=UTF-8"
            ),
        }
        response.iter_content.return_value = iter(
            chunks or []
        )

        if status_code >= 400:
            response.raise_for_status.side_effect = (
                requests.HTTPError(
                    f"HTTP {status_code}"
                )
            )

        return response

    def test_valid_html_content_is_decoded(
        self,
    ) -> None:
        html = validate_html_content(
            self.HTML_CONTENT
        )

        self.assertIn(
            "新株落・権利落等一覧",
            html,
        )

    def test_utf8_bom_is_accepted(
        self,
    ) -> None:
        content = (
            b"\xef\xbb\xbf"
            b"<!doctype html><html></html>"
        )

        self.assertEqual(
            validate_html_content(content),
            "<!doctype html><html></html>",
        )

    def test_invalid_html_contents_are_rejected(
        self,
    ) -> None:
        invalid_contents = (
            b"",
            b"%PDF-1.7",
            b"plain text",
            b"\xff\xfe\x00\x00",
        )

        for content in invalid_contents:
            with self.subTest(content=content):
                with self.assertRaises(
                    RuntimeError
                ):
                    validate_html_content(content)

    def test_non_bytes_html_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "bytes",
        ):
            validate_html_content(  # type: ignore[arg-type]
                "<html></html>"
            )

    def test_html_larger_than_limit_is_rejected(
        self,
    ) -> None:
        content = (
            b"<html>"
            + b"x" * MAX_HTML_CONTENT_BYTES
            + b"</html>"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "上限",
        ):
            validate_html_content(content)

    def test_page_is_downloaded_as_stream(
        self,
    ) -> None:
        response = self.create_response(
            url=self.INDEX_URL,
            headers={
                "Content-Type": (
                    "text/html; charset=UTF-8"
                ),
                "Content-Length": str(
                    len(self.HTML_CONTENT)
                ),
            },
            chunks=[
                self.HTML_CONTENT[:20],
                b"",
                self.HTML_CONTENT[20:],
            ],
        )
        session = requests.Session()

        with patch.object(
            session,
            "get",
            return_value=response,
        ) as get_mock:
            html = download_monthly_page(
                session,
                self.INDEX_URL,
            )

        self.assertIn(
            "新株落・権利落等一覧",
            html,
        )
        get_mock.assert_called_once_with(
            self.INDEX_URL,
            headers={
                "User-Agent": (
                    "progressive-dividend-screener/"
                    "jpx-corporate-actions"
                ),
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml"
                ),
            },
            timeout=120,
            allow_redirects=True,
            stream=True,
        )
        response.close.assert_called_once()

    def test_allowed_redirect_is_accepted(
        self,
    ) -> None:
        response = self.create_response(
            url=self.ARCHIVE_URL,
            chunks=[self.HTML_CONTENT],
        )
        session = requests.Session()

        with patch.object(
            session,
            "get",
            return_value=response,
        ):
            html = download_monthly_page(
                session,
                self.INDEX_URL,
            )

        self.assertIn(
            "<html",
            html.lower(),
        )
        response.close.assert_called_once()

    def test_external_redirect_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            url=(
                "https://example.com/"
                "markets/statistics-equities/monthly/"
                "index.html"
            ),
            chunks=[self.HTML_CONTENT],
        )
        session = requests.Session()

        with patch.object(
            session,
            "get",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "ホスト",
            ):
                download_monthly_page(
                    session,
                    self.INDEX_URL,
                )

        response.close.assert_called_once()

    def test_non_html_content_type_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            url=self.INDEX_URL,
            headers={
                "Content-Type": "application/pdf",
            },
            chunks=[self.HTML_CONTENT],
        )
        session = requests.Session()

        with patch.object(
            session,
            "get",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Content-Type",
            ):
                download_monthly_page(
                    session,
                    self.INDEX_URL,
                )

        response.close.assert_called_once()

    def test_invalid_content_length_is_rejected(
        self,
    ) -> None:
        invalid_lengths = (
            "invalid",
            "-1",
            str(MAX_HTML_CONTENT_BYTES + 1),
        )

        for content_length in invalid_lengths:
            with self.subTest(
                content_length=content_length
            ):
                response = self.create_response(
                    url=self.INDEX_URL,
                    headers={
                        "Content-Type": "text/html",
                        "Content-Length": (
                            content_length
                        ),
                    },
                    chunks=[self.HTML_CONTENT],
                )
                session = requests.Session()

                with patch.object(
                    session,
                    "get",
                    return_value=response,
                ):
                    with self.assertRaises(
                        RuntimeError
                    ):
                        download_monthly_page(
                            session,
                            self.INDEX_URL,
                        )

                response.close.assert_called_once()

    def test_stream_larger_than_limit_is_rejected(
        self,
    ) -> None:
        response = self.create_response(
            url=self.INDEX_URL,
            chunks=[
                b"<html>",
                b"x" * MAX_HTML_CONTENT_BYTES,
            ],
        )
        session = requests.Session()

        with patch.object(
            session,
            "get",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "実データサイズ",
            ):
                download_monthly_page(
                    session,
                    self.INDEX_URL,
                )

        response.close.assert_called_once()

    def test_server_error_is_retried(
        self,
    ) -> None:
        failed_response = self.create_response(
            url=self.INDEX_URL,
            status_code=500,
            headers={
                "Content-Type": "text/html",
                "Retry-After": "0",
            },
        )
        successful_response = self.create_response(
            url=self.INDEX_URL,
            chunks=[self.HTML_CONTENT],
        )
        session = requests.Session()

        with (
            patch.object(
                session,
                "get",
                side_effect=[
                    failed_response,
                    successful_response,
                ],
            ) as get_mock,
            patch(
                "update_jpx_corporate_actions.time.sleep"
            ) as sleep_mock,
        ):
            html = download_monthly_page(
                session,
                self.INDEX_URL,
            )

        self.assertIn(
            "<html",
            html.lower(),
        )
        self.assertEqual(
            get_mock.call_count,
            2,
        )
        sleep_mock.assert_called_once()
        failed_response.close.assert_called_once()
        successful_response.close.assert_called_once()

    def test_connection_error_is_retried(
        self,
    ) -> None:
        successful_response = self.create_response(
            url=self.INDEX_URL,
            chunks=[self.HTML_CONTENT],
        )
        session = requests.Session()

        with (
            patch.object(
                session,
                "get",
                side_effect=[
                    requests.ConnectionError(
                        "temporary failure"
                    ),
                    successful_response,
                ],
            ) as get_mock,
            patch(
                "update_jpx_corporate_actions.time.sleep"
            ) as sleep_mock,
        ):
            html = download_monthly_page(
                session,
                self.INDEX_URL,
            )

        self.assertIn(
            "<html",
            html.lower(),
        )
        self.assertEqual(
            get_mock.call_count,
            2,
        )
        sleep_mock.assert_called_once()
        successful_response.close.assert_called_once()

    def test_client_error_is_not_retried(
        self,
    ) -> None:
        response = self.create_response(
            url=self.INDEX_URL,
            status_code=404,
        )
        session = requests.Session()

        with (
            patch.object(
                session,
                "get",
                return_value=response,
            ) as get_mock,
            patch(
                "update_jpx_corporate_actions.time.sleep"
            ) as sleep_mock,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "HTTPError",
            ):
                download_monthly_page(
                    session,
                    self.INDEX_URL,
                )

        get_mock.assert_called_once()
        sleep_mock.assert_not_called()
        response.close.assert_called_once()

    def test_invalid_session_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "requests.Session",
        ):
            download_monthly_page(
                MagicMock(),
                self.INDEX_URL,
            )

# ============================================================
# JPXバックナンバー統合テスト
# ============================================================

class JpxMonthlyArchiveDiscoveryTest(unittest.TestCase):
    """バックナンバーページの発見とPDF情報統合を検証する。"""

    INDEX_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/index.html"
    )

    ARCHIVE_2024_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "00-archives-02.html"
    )

    ARCHIVE_2022_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "00-archives-04.html"
    )

    PDF_2022_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "nlsgeu000006c4e0-att/18_kenri2203.pdf"
    )

    PDF_2024_URL = (
        "https://www.jpx.co.jp/"
        "markets/statistics-equities/monthly/"
        "mklp77000000luwj-att/17_kenri2410.pdf"
    )

    def test_anchor_and_option_pages_are_discovered(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <a href="00-archives-04.html">
              2022年
            </a>
            <select class="backnumber">
              <option value="index.html">
                2026年
              </option>
              <option value="00-archives-02.html">
                2024年
              </option>
            </select>
          </body>
        </html>
        """

        page_urls = parse_monthly_page_urls(
            html,
            page_url=self.INDEX_URL,
        )

        self.assertEqual(
            page_urls,
            (
                self.ARCHIVE_2024_URL,
                self.ARCHIVE_2022_URL,
                self.INDEX_URL,
            ),
        )

    def test_duplicate_page_urls_are_removed(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <a href="00-archives-04.html">
              2022年
            </a>
            <option value="00-archives-04.html">
              2022年
            </option>
          </body>
        </html>
        """

        page_urls = parse_monthly_page_urls(
            html,
            page_url=self.INDEX_URL,
        )

        self.assertEqual(
            page_urls,
            (
                self.ARCHIVE_2022_URL,
                self.INDEX_URL,
            ),
        )

    def test_unrelated_links_are_ignored(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <a href="01.html">ご利用の手引き</a>
            <a href="/markets/index.html">
              マーケット情報
            </a>
            <a href="00-archives-04.html">
              2022年
            </a>
          </body>
        </html>
        """

        page_urls = parse_monthly_page_urls(
            html,
            page_url=self.INDEX_URL,
        )

        self.assertEqual(
            page_urls,
            (
                self.ARCHIVE_2022_URL,
                self.INDEX_URL,
            ),
        )

    def test_external_archive_page_is_rejected(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <a href="https://example.com/
            markets/statistics-equities/monthly/
            00-archives-04.html">
              外部ページ
            </a>
          </body>
        </html>
        """

        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            parse_monthly_page_urls(
                html,
                page_url=self.INDEX_URL,
            )

    def test_index_without_archive_is_rejected(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <a href="01.html">
              ご利用の手引き
            </a>
          </body>
        </html>
        """

        with self.assertRaisesRegex(
            RuntimeError,
            "バックナンバー",
        ):
            parse_monthly_page_urls(
                html,
                page_url=self.INDEX_URL,
            )

    def test_archive_page_can_contain_only_itself(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <p>2022年バックナンバー</p>
          </body>
        </html>
        """

        self.assertEqual(
            parse_monthly_page_urls(
                html,
                page_url=self.ARCHIVE_2022_URL,
            ),
            (
                self.ARCHIVE_2022_URL,
            ),
        )

    def test_page_count_limit_is_enforced(
        self,
    ) -> None:
        html = """
        <!doctype html>
        <html>
          <body>
            <a href="00-archives-01.html">2025年</a>
            <a href="00-archives-02.html">2024年</a>
          </body>
        </html>
        """

        with patch(
            "update_jpx_corporate_actions."
            "MAX_MONTHLY_PAGE_COUNT",
            2,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "安全上限",
            ):
                parse_monthly_page_urls(
                    html,
                    page_url=self.INDEX_URL,
                )

    def test_pdf_source_groups_are_merged_and_sorted(
        self,
    ) -> None:
        source_2022 = JpxMonthlyPdfSource(
            coverage_month=date(2022, 3, 1),
            source_url=self.PDF_2022_URL,
        )
        source_2024 = JpxMonthlyPdfSource(
            coverage_month=date(2024, 10, 1),
            source_url=self.PDF_2024_URL,
        )

        merged = merge_monthly_pdf_sources(
            [
                (source_2024,),
                (source_2022,),
                (source_2022,),
            ]
        )

        self.assertEqual(
            merged,
            (
                source_2022,
                source_2024,
            ),
        )

    def test_conflicting_pdf_sources_are_rejected(
        self,
    ) -> None:
        first = JpxMonthlyPdfSource(
            coverage_month=date(2022, 3, 1),
            source_url=self.PDF_2022_URL,
        )
        second = JpxMonthlyPdfSource(
            coverage_month=date(2022, 3, 1),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                "another-att/18_kenri2203.pdf"
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "同一対象年月",
        ):
            merge_monthly_pdf_sources(
                [
                    (first,),
                    (second,),
                ]
            )

    def test_invalid_source_group_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "tuple",
        ):
            merge_monthly_pdf_sources(
                [
                    [],  # type: ignore[list-item]
                ]
            )

    def test_invalid_source_type_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "型",
        ):
            merge_monthly_pdf_sources(
                [
                    (
                        "invalid",  # type: ignore[arg-type]
                    ),
                ]
            )

    def test_empty_source_groups_are_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "取得できませんでした",
        ):
            merge_monthly_pdf_sources([])

    def test_all_pages_are_downloaded_and_merged(
        self,
    ) -> None:
        index_html = f"""
        <!doctype html>
        <html>
          <body>
            <select class="backnumber">
              <option value="index.html">
                現在年
              </option>
              <option value="00-archives-04.html">
                2022年
              </option>
            </select>
            <a href="{self.PDF_2024_URL}">
              2024年10月
            </a>
          </body>
        </html>
        """

        archive_html = f"""
        <!doctype html>
        <html>
          <body>
            <a href="{self.PDF_2022_URL}">
              2022年3月
            </a>
          </body>
        </html>
        """

        def fake_download(
            session: requests.Session,
            page_url: str,
        ) -> str:
            del session

            if page_url == self.INDEX_URL:
                return index_html

            if page_url == self.ARCHIVE_2022_URL:
                return archive_html

            raise AssertionError(
                f"予期しないURLです: {page_url}"
            )

        session = requests.Session()

        with patch(
            "update_jpx_corporate_actions."
            "download_monthly_page",
            side_effect=fake_download,
        ) as download_mock:
            sources = discover_monthly_pdf_sources(
                session
            )

        self.assertEqual(
            sources,
            (
                JpxMonthlyPdfSource(
                    coverage_month=date(2022, 3, 1),
                    source_url=self.PDF_2022_URL,
                ),
                JpxMonthlyPdfSource(
                    coverage_month=date(2024, 10, 1),
                    source_url=self.PDF_2024_URL,
                ),
            ),
        )
        self.assertEqual(
            download_mock.call_count,
            2,
        )

    def test_invalid_session_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "requests.Session",
        ):
            discover_monthly_pdf_sources(
                MagicMock()
            )

# ============================================================
# JPX対象期間選択テスト
# ============================================================

class JpxMonthlyCoverageSelectionTest(unittest.TestCase):
    """JPX月次PDFの対象期間選択を検証する。"""

    @staticmethod
    def create_source(
        year: int,
        month: int,
        *,
        directory: str = "example-att",
    ) -> JpxMonthlyPdfSource:
        """指定年月のテスト用PDF情報を作成する。"""

        return JpxMonthlyPdfSource(
            coverage_month=date(
                year,
                month,
                1,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                f"{directory}/"
                f"17_kenri{year % 100:02d}"
                f"{month:02d}.pdf"
            ),
        )

    def test_date_is_normalized_to_month_start(
        self,
    ) -> None:
        self.assertEqual(
            normalize_month(
                date(2024, 10, 31),
                field_name="対象日",
            ),
            date(2024, 10, 1),
        )

    def test_non_date_month_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "date",
        ):
            normalize_month(
                "2024-10",  # type: ignore[arg-type]
                field_name="対象月",
            )

    def test_next_month_handles_year_boundary(
        self,
    ) -> None:
        self.assertEqual(
            next_month(
                date(2023, 12, 15)
            ),
            date(2024, 1, 1),
        )

    def test_month_range_is_inclusive(
        self,
    ) -> None:
        self.assertEqual(
            build_month_range(
                date(2023, 11, 30),
                date(2024, 2, 29),
            ),
            (
                date(2023, 11, 1),
                date(2023, 12, 1),
                date(2024, 1, 1),
                date(2024, 2, 1),
            ),
        )

    def test_start_after_end_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "開始月",
        ):
            build_month_range(
                date(2024, 2, 1),
                date(2024, 1, 1),
            )

    def test_month_count_limit_is_enforced(
        self,
    ) -> None:
        with patch(
            "update_jpx_corporate_actions."
            "MAX_COVERAGE_MONTH_COUNT",
            2,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "安全上限",
            ):
                build_month_range(
                    date(2024, 1, 1),
                    date(2024, 3, 1),
                )

    def test_complete_period_is_selected_in_order(
        self,
    ) -> None:
        january = self.create_source(
            2024,
            1,
        )
        february = self.create_source(
            2024,
            2,
        )
        march = self.create_source(
            2024,
            3,
        )
        outside = self.create_source(
            2023,
            12,
        )

        selected = select_monthly_pdf_sources(
            (
                march,
                outside,
                january,
                february,
            ),
            coverage_start=date(2024, 1, 20),
            coverage_end=date(2024, 3, 31),
        )

        self.assertEqual(
            selected,
            (
                january,
                february,
                march,
            ),
        )

    def test_source_outside_period_is_ignored(
        self,
    ) -> None:
        january = self.create_source(
            2024,
            1,
        )
        december = self.create_source(
            2023,
            12,
        )
        february = self.create_source(
            2024,
            2,
        )

        selected = select_monthly_pdf_sources(
            (
                december,
                january,
                february,
            ),
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 1, 31),
        )

        self.assertEqual(
            selected,
            (
                january,
            ),
        )

    def test_same_source_is_deduplicated(
        self,
    ) -> None:
        january = self.create_source(
            2024,
            1,
        )

        selected = select_monthly_pdf_sources(
            (
                january,
                january,
            ),
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 1, 1),
        )

        self.assertEqual(
            selected,
            (
                january,
            ),
        )

    def test_conflicting_source_is_rejected(
        self,
    ) -> None:
        first = self.create_source(
            2024,
            1,
            directory="first-att",
        )
        second = self.create_source(
            2024,
            1,
            directory="second-att",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "同一対象年月",
        ):
            select_monthly_pdf_sources(
                (
                    first,
                    second,
                ),
                coverage_start=date(2024, 1, 1),
                coverage_end=date(2024, 1, 1),
            )

    def test_missing_month_is_rejected(
        self,
    ) -> None:
        january = self.create_source(
            2024,
            1,
        )
        march = self.create_source(
            2024,
            3,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "2024-02",
        ):
            select_monthly_pdf_sources(
                (
                    january,
                    march,
                ),
                coverage_start=date(2024, 1, 1),
                coverage_end=date(2024, 3, 1),
            )

    def test_non_month_start_source_is_rejected(
        self,
    ) -> None:
        source = JpxMonthlyPdfSource(
            coverage_month=date(
                2024,
                1,
                15,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                "example-att/17_kenri2401.pdf"
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "月初",
        ):
            select_monthly_pdf_sources(
                (
                    source,
                ),
                coverage_start=date(2024, 1, 1),
                coverage_end=date(2024, 1, 31),
            )

    def test_invalid_source_url_is_rejected(
        self,
    ) -> None:
        source = JpxMonthlyPdfSource(
            coverage_month=date(
                2024,
                1,
                1,
            ),
            source_url=(
                "https://example.com/"
                "markets/statistics-equities/monthly/"
                "example-att/17_kenri2401.pdf"
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            select_monthly_pdf_sources(
                (
                    source,
                ),
                coverage_start=date(2024, 1, 1),
                coverage_end=date(2024, 1, 1),
            )

    def test_invalid_sources_container_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "tuple",
        ):
            select_monthly_pdf_sources(
                [],  # type: ignore[arg-type]
                coverage_start=date(2024, 1, 1),
                coverage_end=date(2024, 1, 1),
            )

    def test_invalid_source_type_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "型",
        ):
            select_monthly_pdf_sources(
                (
                    "invalid",  # type: ignore[arg-type]
                ),
                coverage_start=date(2024, 1, 1),
                coverage_end=date(2024, 1, 1),
            )

# ============================================================
# JPX月次PDF一括解析テスト
# ============================================================

class JpxMonthlyBatchParsingTest(unittest.TestCase):
    """選択したJPX月次PDFの取得・解析・統合を検証する。"""

    @staticmethod
    def create_source(
        year: int,
        month: int,
        *,
        directory: str = "example-att",
    ) -> JpxMonthlyPdfSource:
        """指定年月のPDF情報を作成する。"""

        return JpxMonthlyPdfSource(
            coverage_month=date(
                year,
                month,
                1,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                f"{directory}/"
                f"17_kenri{year % 100:02d}"
                f"{month:02d}.pdf"
            ),
        )

    @staticmethod
    def create_action(
        *,
        security_code: str,
        effective_date: date,
        adjustment_factor: str = "0.5000000000",
        ex_right_type: str = "1",
    ) -> JpxCorporateAction:
        """テスト用企業行動を作成する。"""

        return JpxCorporateAction(
            security_code=security_code,
            effective_date=effective_date,
            adjustment_factor=Decimal(
                adjustment_factor
            ),
            ex_right_type=ex_right_type,
            description=(
                f"{security_code} "
                f"{effective_date} 株式分割"
            ),
        )

    @staticmethod
    def create_parsed_pdf(
        content: bytes,
        *,
        actions: tuple[
            JpxCorporateAction,
            ...,
        ] = (),
        content_sha256: str | None = None,
    ) -> ParsedJpxPdf:
        """テスト用PDF解析結果を作成する。"""

        return ParsedJpxPdf(
            content_sha256=(
                content_sha256
                if content_sha256 is not None
                else hashlib.sha256(
                    content
                ).hexdigest()
            ),
            page_count=2,
            table_count=3,
            row_count=10,
            actions=actions,
        )

    def test_sources_are_downloaded_and_parsed_in_month_order(
        self,
    ) -> None:
        january_source = self.create_source(
            2024,
            1,
            directory="january-att",
        )
        february_source = self.create_source(
            2024,
            2,
            directory="february-att",
        )

        january_content = b"%PDF-january"
        february_content = b"%PDF-february"

        january_action = self.create_action(
            security_code="2222",
            effective_date=date(
                2024,
                1,
                30,
            ),
        )
        february_action = self.create_action(
            security_code="1111",
            effective_date=date(
                2024,
                2,
                28,
            ),
            adjustment_factor="10.0000000000",
            ex_right_type="2",
        )

        contents_by_url = {
            january_source.source_url: (
                january_content
            ),
            february_source.source_url: (
                february_content
            ),
        }
        parsed_by_content = {
            january_content: self.create_parsed_pdf(
                january_content,
                actions=(
                    january_action,
                ),
            ),
            february_content: self.create_parsed_pdf(
                february_content,
                actions=(
                    february_action,
                ),
            ),
        }

        def fake_download(
            session: requests.Session,
            source_url: str,
        ) -> bytes:
            del session
            return contents_by_url[source_url]

        def fake_parse(
            content: bytes,
        ) -> ParsedJpxPdf:
            return parsed_by_content[content]

        session = requests.Session()

        with (
            patch(
                "update_jpx_corporate_actions."
                "download_monthly_pdf",
                side_effect=fake_download,
            ) as download_mock,
            patch(
                "update_jpx_corporate_actions."
                "parse_monthly_pdf",
                side_effect=fake_parse,
            ) as parse_mock,
        ):
            result = (
                download_and_parse_monthly_sources(
                    session,
                    (
                        february_source,
                        january_source,
                    ),
                )
            )

        self.assertIsInstance(
            result,
            ParsedJpxCoverage,
        )
        self.assertEqual(
            result.coverage_start,
            date(2024, 1, 1),
        )
        self.assertEqual(
            result.coverage_end,
            date(2024, 2, 1),
        )
        self.assertEqual(
            result.actions,
            (
                january_action,
                february_action,
            ),
        )
        self.assertEqual(
            tuple(
                parsed_source.source
                for parsed_source
                in result.source_files
            ),
            (
                january_source,
                february_source,
            ),
        )
        self.assertEqual(
            result.source_files[0],
            ParsedJpxMonthlySource(
                source=january_source,
                content_sha256=hashlib.sha256(
                    january_content
                ).hexdigest(),
                page_count=2,
                table_count=3,
                row_count=10,
                actions=(
                    january_action,
                ),
            ),
        )
        self.assertEqual(
            download_mock.call_count,
            2,
        )
        self.assertEqual(
            parse_mock.call_count,
            2,
        )

    def test_duplicate_same_source_is_processed_once(
        self,
    ) -> None:
        source = self.create_source(
            2024,
            1,
        )
        content = b"%PDF-january"
        parsed = self.create_parsed_pdf(
            content
        )
        session = requests.Session()

        with (
            patch(
                "update_jpx_corporate_actions."
                "download_monthly_pdf",
                return_value=content,
            ) as download_mock,
            patch(
                "update_jpx_corporate_actions."
                "parse_monthly_pdf",
                return_value=parsed,
            ) as parse_mock,
        ):
            result = (
                download_and_parse_monthly_sources(
                    session,
                    (
                        source,
                        source,
                    ),
                )
            )

        self.assertEqual(
            len(result.source_files),
            1,
        )
        download_mock.assert_called_once()
        parse_mock.assert_called_once()

    def test_missing_month_is_rejected_before_download(
        self,
    ) -> None:
        january = self.create_source(
            2024,
            1,
        )
        march = self.create_source(
            2024,
            3,
        )
        session = requests.Session()

        with patch(
            "update_jpx_corporate_actions."
            "download_monthly_pdf",
        ) as download_mock:
            with self.assertRaisesRegex(
                RuntimeError,
                "2024-02",
            ):
                download_and_parse_monthly_sources(
                    session,
                    (
                        january,
                        march,
                    ),
                )

        download_mock.assert_not_called()

    def test_filename_month_mismatch_is_rejected(
        self,
    ) -> None:
        source = JpxMonthlyPdfSource(
            coverage_month=date(
                2024,
                1,
                1,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                "example-att/17_kenri2402.pdf"
            ),
        )
        session = requests.Session()

        with self.assertRaisesRegex(
            RuntimeError,
            "一致しません",
        ):
            download_and_parse_monthly_sources(
                session,
                (
                    source,
                ),
            )

    def test_unrecognized_pdf_filename_is_rejected(
        self,
    ) -> None:
        source = JpxMonthlyPdfSource(
            coverage_month=date(
                2024,
                1,
                1,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/statistics-equities/monthly/"
                "example-att/monthly.pdf"
            ),
        )
        session = requests.Session()

        with self.assertRaisesRegex(
            RuntimeError,
            "ファイル名",
        ):
            download_and_parse_monthly_sources(
                session,
                (
                    source,
                ),
            )

    def test_sha256_mismatch_is_rejected(
        self,
    ) -> None:
        source = self.create_source(
            2024,
            1,
        )
        content = b"%PDF-january"
        parsed = self.create_parsed_pdf(
            content,
            content_sha256="0" * 64,
        )
        session = requests.Session()

        with (
            patch(
                "update_jpx_corporate_actions."
                "download_monthly_pdf",
                return_value=content,
            ),
            patch(
                "update_jpx_corporate_actions."
                "parse_monthly_pdf",
                return_value=parsed,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "SHA-256",
            ):
                download_and_parse_monthly_sources(
                    session,
                    (
                        source,
                    ),
                )

    def test_action_outside_pdf_month_is_rejected(
        self,
    ) -> None:
        source = self.create_source(
            2024,
            1,
        )
        content = b"%PDF-january"
        outside_action = self.create_action(
            security_code="1111",
            effective_date=date(
                2024,
                2,
                1,
            ),
        )
        parsed = self.create_parsed_pdf(
            content,
            actions=(
                outside_action,
            ),
        )
        session = requests.Session()

        with (
            patch(
                "update_jpx_corporate_actions."
                "download_monthly_pdf",
                return_value=content,
            ),
            patch(
                "update_jpx_corporate_actions."
                "parse_monthly_pdf",
                return_value=parsed,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "対象年月外",
            ):
                download_and_parse_monthly_sources(
                    session,
                    (
                        source,
                    ),
                )

    def test_duplicate_identical_action_is_merged(
        self,
    ) -> None:
        source = self.create_source(
            2024,
            1,
        )
        content = b"%PDF-january"
        action = self.create_action(
            security_code="1111",
            effective_date=date(
                2024,
                1,
                30,
            ),
        )
        parsed = self.create_parsed_pdf(
            content,
            actions=(
                action,
                action,
            ),
        )
        session = requests.Session()

        with (
            patch(
                "update_jpx_corporate_actions."
                "download_monthly_pdf",
                return_value=content,
            ),
            patch(
                "update_jpx_corporate_actions."
                "parse_monthly_pdf",
                return_value=parsed,
            ),
        ):
            result = (
                download_and_parse_monthly_sources(
                    session,
                    (
                        source,
                    ),
                )
            )

        self.assertEqual(
            result.actions,
            (
                action,
            ),
        )

    def test_conflicting_actions_are_rejected(
        self,
    ) -> None:
        source = self.create_source(
            2024,
            1,
        )
        content = b"%PDF-january"
        first = self.create_action(
            security_code="1111",
            effective_date=date(
                2024,
                1,
                30,
            ),
            adjustment_factor="0.5000000000",
            ex_right_type="1",
        )
        second = self.create_action(
            security_code="1111",
            effective_date=date(
                2024,
                1,
                30,
            ),
            adjustment_factor="10.0000000000",
            ex_right_type="2",
        )
        parsed = self.create_parsed_pdf(
            content,
            actions=(
                first,
                second,
            ),
        )
        session = requests.Session()

        with (
            patch(
                "update_jpx_corporate_actions."
                "download_monthly_pdf",
                return_value=content,
            ),
            patch(
                "update_jpx_corporate_actions."
                "parse_monthly_pdf",
                return_value=parsed,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "矛盾",
            ):
                download_and_parse_monthly_sources(
                    session,
                    (
                        source,
                    ),
                )

    def test_empty_sources_are_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "空",
        ):
            download_and_parse_monthly_sources(
                requests.Session(),
                (),
            )

    def test_invalid_sources_container_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "tuple",
        ):
            download_and_parse_monthly_sources(
                requests.Session(),
                [],  # type: ignore[arg-type]
            )

    def test_invalid_source_type_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "型",
        ):
            download_and_parse_monthly_sources(
                requests.Session(),
                (
                    "invalid",  # type: ignore[arg-type]
                ),
            )

    def test_invalid_session_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "requests.Session",
        ):
            download_and_parse_monthly_sources(
                MagicMock(),
                (
                    self.create_source(
                        2024,
                        1,
                    ),
                ),
            )

# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
