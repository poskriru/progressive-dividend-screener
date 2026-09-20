"""
TDnet PDF取得・本文抽出処理の単体テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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

from fetch_tdnet_policy_pdfs import (  # noqa: E402
    TdnetPdfFetchError,
    TdnetPdfTextExtractionError,
    extract_pdf_text,
    fetch_tdnet_pdf,
    has_pdf_signature,
    normalize_content_type,
    parse_content_length,
    validate_tdnet_pdf_url,
)


# ============================================================
# テスト用HTTPレスポンス
# ============================================================

class FakeResponse:
    """requests.Responseのテスト用最小実装。"""

    def __init__(
        self,
        *,
        content: bytes,
        content_type: str = "application/pdf",
        status_code: int = 200,
        url: str = (
            "https://www.release.tdnet.info/"
            "inbs/test.pdf"
        ),
        content_length: str | None = None,
    ) -> None:
        self._content = content
        self.status_code = status_code
        self.url = url
        self.closed = False
        self.headers = {
            "Content-Type": content_type,
        }

        if content_length is not None:
            self.headers[
                "Content-Length"
            ] = content_length

    def iter_content(
        self,
        *,
        chunk_size: int,
    ):
        for offset in range(
            0,
            len(self._content),
            chunk_size,
        ):
            yield self._content[
                offset:offset + chunk_size
            ]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """requests.Sessionのテスト用最小実装。"""

    def __init__(
        self,
        response: FakeResponse,
    ) -> None:
        self.response = response
        self.requested_url = ""
        self.request_options = {}

    def get(
        self,
        url: str,
        **kwargs,
    ) -> FakeResponse:
        self.requested_url = url
        self.request_options = kwargs
        return self.response


# ============================================================
# URL検証
# ============================================================

class TdnetPdfUrlValidationTest(
    unittest.TestCase
):
    """TDnet PDF URL制限を確認する。"""

    def test_official_https_pdf_url_is_allowed(
        self,
    ) -> None:
        url = (
            "https://www.release.tdnet.info/"
            "inbs/140120260101000001.pdf"
        )

        self.assertEqual(
            validate_tdnet_pdf_url(url),
            url,
        )

    def test_http_url_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TdnetPdfFetchError
        ):
            validate_tdnet_pdf_url(
                "http://www.release.tdnet.info/"
                "inbs/test.pdf"
            )

    def test_external_host_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TdnetPdfFetchError
        ):
            validate_tdnet_pdf_url(
                "https://example.com/inbs/test.pdf"
            )

    def test_misleading_subdomain_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TdnetPdfFetchError
        ):
            validate_tdnet_pdf_url(
                "https://www.release.tdnet.info."
                "example.com/inbs/test.pdf"
            )

    def test_non_pdf_path_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TdnetPdfFetchError
        ):
            validate_tdnet_pdf_url(
                "https://www.release.tdnet.info/"
                "inbs/test.html"
            )

    def test_path_outside_inbs_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TdnetPdfFetchError
        ):
            validate_tdnet_pdf_url(
                "https://www.release.tdnet.info/"
                "other/test.pdf"
            )


# ============================================================
# ヘッダー検証
# ============================================================

class TdnetPdfHeaderValidationTest(
    unittest.TestCase
):
    """Content-Type等の正規化を確認する。"""

    def test_content_type_parameters_are_removed(
        self,
    ) -> None:
        self.assertEqual(
            normalize_content_type(
                "application/pdf; charset=binary"
            ),
            "application/pdf",
        )

    def test_empty_content_length_returns_none(
        self,
    ) -> None:
        self.assertIsNone(
            parse_content_length("")
        )

    def test_numeric_content_length_is_parsed(
        self,
    ) -> None:
        self.assertEqual(
            parse_content_length("1234"),
            1234,
        )

    def test_invalid_content_length_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TdnetPdfFetchError
        ):
            parse_content_length("invalid")

    def test_pdf_signature_with_prefix_is_detected(
        self,
    ) -> None:
        self.assertTrue(
            has_pdf_signature(
                b"\xef\xbb\xbf%PDF-1.7\n"
            )
        )


# ============================================================
# PDF取得
# ============================================================

class TdnetPdfFetchTest(unittest.TestCase):
    """サイズ制限付きPDF取得を確認する。"""

    def test_valid_pdf_is_downloaded_and_hashed(
        self,
    ) -> None:
        content = b"%PDF-1.7\nsample"
        response = FakeResponse(
            content=content,
            content_length=str(len(content)),
        )
        session = FakeSession(response)

        result = fetch_tdnet_pdf(
            session,
            response.url,
        )

        self.assertEqual(
            result.content,
            content,
        )
        self.assertEqual(
            result.content_sha256,
            hashlib.sha256(content).hexdigest(),
        )
        self.assertEqual(
            result.content_length_bytes,
            len(content),
        )
        self.assertEqual(
            result.content_type,
            "application/pdf",
        )
        self.assertEqual(
            result.http_status_code,
            200,
        )
        self.assertTrue(response.closed)
        self.assertTrue(
            session.request_options["stream"]
        )
        self.assertTrue(
            session.request_options[
                "allow_redirects"
            ]
        )

    def test_non_200_status_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"",
            status_code=404,
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                response.url,
            )

        self.assertTrue(response.closed)

    def test_non_pdf_content_type_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"<html>error</html>",
            content_type="text/html",
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                response.url,
            )

    def test_declared_oversize_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"%PDF-1.7\n",
            content_length="101",
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                response.url,
                maximum_size_bytes=100,
            )

    def test_streamed_oversize_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"%PDF-1.7\n" + b"x" * 100,
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                response.url,
                maximum_size_bytes=20,
            )

    def test_empty_response_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"",
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                response.url,
            )

    def test_content_without_pdf_signature_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"not a pdf",
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                response.url,
            )

    def test_redirect_to_external_host_is_rejected(
        self,
    ) -> None:
        response = FakeResponse(
            content=b"%PDF-1.7\n",
            url="https://example.com/test.pdf",
        )

        with self.assertRaises(
            TdnetPdfFetchError
        ):
            fetch_tdnet_pdf(
                FakeSession(response),
                (
                    "https://www.release.tdnet.info/"
                    "inbs/test.pdf"
                ),
            )

        self.assertTrue(response.closed)


# ============================================================
# PDF本文抽出
# ============================================================

class TdnetPdfTextExtractionTest(
    unittest.TestCase
):
    """pdfplumberによるページ本文抽出を確認する。"""

    @patch(
        "fetch_tdnet_policy_pdfs.pdfplumber.open"
    )
    def test_page_text_is_extracted(
        self,
        mocked_pdf_open,
    ) -> None:
        first_page = MagicMock()
        first_page.extract_text.return_value = (
            "1ページ目"
        )
        second_page = MagicMock()
        second_page.extract_text.return_value = (
            "2ページ目の累進配当方針"
        )

        mocked_pdf = MagicMock()
        mocked_pdf.pages = [
            first_page,
            second_page,
        ]
        mocked_pdf_open.return_value.__enter__.return_value = (
            mocked_pdf
        )

        result = extract_pdf_text(
            b"%PDF-1.7\ntest"
        )

        self.assertEqual(
            result.page_texts,
            (
                "1ページ目",
                "2ページ目の累進配当方針",
            ),
        )
        self.assertEqual(
            result.page_count,
            2,
        )
        self.assertEqual(
            result.extracted_text_length,
            len("1ページ目")
            + len("2ページ目の累進配当方針"),
        )

    @patch(
        "fetch_tdnet_policy_pdfs.pdfplumber.open"
    )
    def test_image_only_pdf_is_rejected(
        self,
        mocked_pdf_open,
    ) -> None:
        page = MagicMock()
        page.extract_text.return_value = None

        mocked_pdf = MagicMock()
        mocked_pdf.pages = [page]
        mocked_pdf_open.return_value.__enter__.return_value = (
            mocked_pdf
        )

        with self.assertRaises(
            TdnetPdfTextExtractionError
        ):
            extract_pdf_text(
                b"%PDF-1.7\ntest"
            )

    @patch(
        "fetch_tdnet_policy_pdfs.pdfplumber.open"
    )
    def test_page_limit_is_enforced(
        self,
        mocked_pdf_open,
    ) -> None:
        mocked_pdf = MagicMock()
        mocked_pdf.pages = [
            MagicMock(),
            MagicMock(),
        ]
        mocked_pdf_open.return_value.__enter__.return_value = (
            mocked_pdf
        )

        with self.assertRaises(
            TdnetPdfTextExtractionError
        ):
            extract_pdf_text(
                b"%PDF-1.7\ntest",
                maximum_page_count=1,
            )

    @patch(
        "fetch_tdnet_policy_pdfs.pdfplumber.open"
    )
    def test_page_extraction_error_is_wrapped(
        self,
        mocked_pdf_open,
    ) -> None:
        page = MagicMock()
        page.extract_text.side_effect = ValueError(
            "broken page"
        )

        mocked_pdf = MagicMock()
        mocked_pdf.pages = [page]
        mocked_pdf_open.return_value.__enter__.return_value = (
            mocked_pdf
        )

        with self.assertRaises(
            TdnetPdfTextExtractionError
        ):
            extract_pdf_text(
                b"%PDF-1.7\ntest"
            )

    @patch(
        "fetch_tdnet_policy_pdfs.pdfplumber.open"
    )
    def test_pdf_open_error_is_wrapped(
        self,
        mocked_pdf_open,
    ) -> None:
        mocked_pdf_open.side_effect = ValueError(
            "broken pdf"
        )

        with self.assertRaises(
            TdnetPdfTextExtractionError
        ):
            extract_pdf_text(
                b"%PDF-1.7\ntest"
            )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
