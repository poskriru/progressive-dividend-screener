"""edinet_source_linksのテスト。"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from edinet_source_links import (  # noqa: E402
    build_edinet_pdf_url,
    extract_edinet_document_id,
    to_edinet_sheet_url,
)


class EdinetSourceLinksTests(unittest.TestCase):
    def test_build_edinet_pdf_url(self) -> None:
        self.assertEqual(
            build_edinet_pdf_url("S100YE8K"),
            (
                "https://disclosure2dl.edinet-fsa.go.jp/"
                "searchdocument/pdf/S100YE8K.pdf"
            ),
        )

    def test_extract_from_pdf_url(self) -> None:
        self.assertEqual(
            extract_edinet_document_id(
                "https://disclosure2dl.edinet-fsa.go.jp/"
                "searchdocument/pdf/S100YE8K.pdf"
            ),
            "S100YE8K",
        )

    def test_extract_from_legacy_wzek_url(self) -> None:
        self.assertEqual(
            extract_edinet_document_id(
                "https://disclosure2.edinet-fsa.go.jp/"
                "WZEK0040.aspx?S100=S100AJ83"
            ),
            "S100AJ83",
        )

    def test_extract_rejects_invalid_urls(self) -> None:
        invalid_values = (
            None,
            123,
            "",
            "https://disclosure2.edinet-fsa.go.jp/",
            "https://disclosure2dl.edinet-fsa.go.jp/"
            "searchdocument/pdf/S100YE8K.pdf?x=1",
            "https://disclosure2dl.edinet-fsa.go.jp/"
            "searchdocument/pdf/S100YE8K.evil.pdf",
            "https://example.com/searchdocument/pdf/S100YE8K.pdf",
        )

        for value in invalid_values:
            with self.subTest(value=value):
                self.assertIsNone(
                    extract_edinet_document_id(value)
                )

    def test_to_edinet_sheet_url_converts_stored_urls(
        self,
    ) -> None:
        self.assertEqual(
            to_edinet_sheet_url(
                "https://disclosure2.edinet-fsa.go.jp/"
                "WZEK0040.aspx?S100=S100AJ83"
            ),
            (
                "https://disclosure2dl.edinet-fsa.go.jp/"
                "searchdocument/pdf/S100AJ83.pdf"
            ),
        )
        self.assertEqual(
            to_edinet_sheet_url(
                "https://disclosure2dl.edinet-fsa.go.jp/"
                "searchdocument/pdf/S100YE8K.pdf"
            ),
            (
                "https://disclosure2dl.edinet-fsa.go.jp/"
                "searchdocument/pdf/S100YE8K.pdf"
            ),
        )

    def test_to_edinet_sheet_url_keeps_unknown_values(
        self,
    ) -> None:
        self.assertEqual(to_edinet_sheet_url(None), "")
        self.assertEqual(to_edinet_sheet_url(""), "")
        self.assertEqual(
            to_edinet_sheet_url(
                "https://disclosure2.edinet-fsa.go.jp/"
            ),
            "https://disclosure2.edinet-fsa.go.jp/",
        )


if __name__ == "__main__":
    unittest.main()
