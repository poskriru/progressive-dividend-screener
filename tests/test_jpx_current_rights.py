"""JPX当月権利処理候補の取得・解析テスト。"""

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

from update_jpx_current_rights import (  # noqa: E402
    JpxCurrentRightsCsvSource,
    get_csv_heading_identifier,
    normalize_security_code,
    parse_allocation_date_from_heading,
    parse_current_rights_csv,
    parse_current_rights_csv_sources,
    parse_split_adjustment_factor,
    validate_jpx_current_rights_csv_url,
    validate_jpx_current_rights_page_url,
)


# ============================================================
# 共通変換
# ============================================================


class JpxCurrentRightsCommonTest(
    unittest.TestCase
):
    """JPX当月データの共通変換を検証する。"""

    def test_numeric_security_code_is_preserved(
        self,
    ) -> None:
        self.assertEqual(
            normalize_security_code("2814"),
            "2814",
        )

    def test_alphanumeric_security_code_is_preserved(
        self,
    ) -> None:
        self.assertEqual(
            normalize_security_code("278a"),
            "278A",
        )

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "銘柄コード",
        ):
            normalize_security_code("123")

    def test_two_for_one_split_uses_half_factor(
        self,
    ) -> None:
        self.assertEqual(
            parse_split_adjustment_factor("1:2"),
            Decimal("0.5000000000"),
        )

    def test_decimal_split_ratio_is_supported(
        self,
    ) -> None:
        self.assertEqual(
            parse_split_adjustment_factor("1:1.2"),
            Decimal("0.8333333333"),
        )

    def test_invalid_ratio_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "分割比率",
        ):
            parse_split_adjustment_factor("2分の1")


# ============================================================
# 割当日
# ============================================================


class JpxCurrentRightsAllocationDateTest(
    unittest.TestCase
):
    """JPX見出しの割当日解析を検証する。"""

    def test_specific_allocation_date_is_parsed(
        self,
    ) -> None:
        self.assertEqual(
            parse_allocation_date_from_heading(
                "2026年9月15日割当銘柄(1銘柄)"
            ),
            date(2026, 9, 15),
        )

    def test_month_end_allocation_date_is_parsed(
        self,
    ) -> None:
        self.assertEqual(
            parse_allocation_date_from_heading(
                "2026年9月 末日割当銘柄(53銘柄)"
            ),
            date(2026, 9, 30),
        )

    def test_leap_year_month_end_is_supported(
        self,
    ) -> None:
        self.assertEqual(
            parse_allocation_date_from_heading(
                "2024年2月末日割当銘柄"
            ),
            date(2024, 2, 29),
        )

    def test_invalid_heading_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "割当日",
        ):
            parse_allocation_date_from_heading(
                "対象年月不明"
            )


# ============================================================
# URL
# ============================================================


class JpxCurrentRightsUrlTest(
    unittest.TestCase
):
    """JPX当月ページとCSVのURL制限を検証する。"""

    def test_official_page_url_is_accepted(
        self,
    ) -> None:
        page_url = (
            "https://www.jpx.co.jp/"
            "markets/equities/rights/index.html"
        )

        self.assertEqual(
            validate_jpx_current_rights_page_url(
                page_url
            ),
            page_url,
        )

    def test_external_page_url_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            validate_jpx_current_rights_page_url(
                "https://example.com/index.html"
            )

    def test_official_csv_url_is_accepted(
        self,
    ) -> None:
        csv_url = (
            "https://www.jpx.co.jp/"
            "markets/equities/rights/"
            "sample-att/sample.csv"
        )

        self.assertEqual(
            validate_jpx_current_rights_csv_url(
                csv_url
            ),
            csv_url,
        )

    def test_external_csv_url_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "ホスト",
        ):
            validate_jpx_current_rights_csv_url(
                "https://example.com/sample.csv"
            )

    def test_heading_identifier_is_extracted(
        self,
    ) -> None:
        self.assertEqual(
            get_csv_heading_identifier(
                (
                    "https://www.jpx.co.jp/"
                    "markets/equities/rights/"
                    "sample123-att/data.csv"
                )
            ),
            "sample123",
        )


# ============================================================
# HTML
# ============================================================


class JpxCurrentRightsHtmlParsingTest(
    unittest.TestCase
):
    """JPX当月ページのCSV情報抽出を検証する。"""

    def test_csv_sources_are_linked_to_headings(
        self,
    ) -> None:
        html = """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
        <script>
        $.ajax({
            url: '/markets/equities/rights/'
                 'sample123-att/sample.csv'
        });
        </script>
        </head>
        <body>
        <h3 id="title_sample123">
            2026年9月15日割当銘柄(
        </h3>
        </body>
        </html>
        """

        html = html.replace(
            (
                "'/markets/equities/rights/'"
                "\n                 "
                "'sample123-att/sample.csv'"
            ),
            (
                "'/markets/equities/rights/"
                "sample123-att/sample.csv'"
            ),
        )

        sources = parse_current_rights_csv_sources(
            html,
            page_url=(
                "https://www.jpx.co.jp/"
                "markets/equities/rights/index.html"
            ),
        )

        self.assertEqual(
            sources,
            (
                JpxCurrentRightsCsvSource(
                    allocation_date=date(
                        2026,
                        9,
                        15,
                    ),
                    source_url=(
                        "https://www.jpx.co.jp/"
                        "markets/equities/rights/"
                        "sample123-att/sample.csv"
                    ),
                ),
            ),
        )

    def test_missing_heading_is_rejected(
        self,
    ) -> None:
        html = """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
        <script>
        $.ajax({
            url: '/markets/equities/rights/'
                 'sample123-att/sample.csv'
        });
        </script>
        </head>
        <body></body>
        </html>
        """

        html = html.replace(
            (
                "'/markets/equities/rights/'"
                "\n                 "
                "'sample123-att/sample.csv'"
            ),
            (
                "'/markets/equities/rights/"
                "sample123-att/sample.csv'"
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "見出し",
        ):
            parse_current_rights_csv_sources(
                html,
                page_url=(
                    "https://www.jpx.co.jp/"
                    "markets/equities/rights/"
                    "index.html"
                ),
            )


# ============================================================
# CSV
# ============================================================


class JpxCurrentRightsCsvParsingTest(
    unittest.TestCase
):
    """JPX当月CSVの行解析を検証する。"""

    def test_csv_row_is_converted_to_watchlist_record(
        self,
    ) -> None:
        source = JpxCurrentRightsCsvSource(
            allocation_date=date(
                2026,
                9,
                15,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/equities/rights/"
                "sample123-att/sample.csv"
            ),
        )

        csv_text = (
            "佐藤食品工業（株）,2814,100,"
            "1:2,2,2分の1\r\n"
        )

        records = parse_current_rights_csv(
            csv_text.encode("cp932"),
            source=source,
        )

        self.assertEqual(
            len(records),
            1,
        )
        self.assertEqual(
            records[0].coverage_month,
            date(2026, 9, 1),
        )
        self.assertEqual(
            records[0].security_code,
            "2814",
        )
        self.assertEqual(
            records[0].allocation_date,
            date(2026, 9, 15),
        )
        self.assertEqual(
            records[0].adjustment_factor,
            Decimal("0.5000000000"),
        )

    def test_blank_csv_rows_are_ignored(
        self,
    ) -> None:
        source = JpxCurrentRightsCsvSource(
            allocation_date=date(
                2026,
                9,
                30,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/equities/rights/"
                "sample123-att/sample.csv"
            ),
        )

        csv_text = (
            "企業A,1718,100,1:3,3,3分の1\r\n"
            ",,,,,\r\n"
        )

        records = parse_current_rights_csv(
            csv_text.encode("cp932"),
            source=source,
        )

        self.assertEqual(
            len(records),
            1,
        )
        self.assertEqual(
            records[0].security_code,
            "1718",
        )

    def test_invalid_column_count_is_rejected(
        self,
    ) -> None:
        source = JpxCurrentRightsCsvSource(
            allocation_date=date(
                2026,
                9,
                30,
            ),
            source_url=(
                "https://www.jpx.co.jp/"
                "markets/equities/rights/"
                "sample123-att/sample.csv"
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "列数",
        ):
            parse_current_rights_csv(
                "企業A,1718,1:3\r\n".encode(
                    "cp932"
                ),
                source=source,
            )


if __name__ == "__main__":
    unittest.main()
