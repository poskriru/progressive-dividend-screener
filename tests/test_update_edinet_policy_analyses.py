"""
EDINET有報配当政策解析スクリプトのユニットテスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date
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

from analyze_tdnet_policy_pdfs import (  # noqa: E402
    POLICY_CLASSIFICATION_CONFIRMED,
)

from update_edinet_policy_analyses import (  # noqa: E402
    EdinetPolicyPdfTarget,
    analyze_edinet_policy_pdf,
    fetch_edinet_pdf,
)


# ============================================================
# テストデータ
# ============================================================

def create_target() -> EdinetPolicyPdfTarget:
    return EdinetPolicyPdfTarget(
        doc_id="S100YIC7",
        security_code="8057",
        submitted_date=date(2026, 6, 24),
        fiscal_period_end=date(2026, 3, 31),
        company_name="内田洋行",
        pdf_url=(
            "https://disclosure2dl.edinet-fsa.go.jp/"
            "searchdocument/pdf/S100YIC7.pdf"
        ),
    )


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.content = content


class EdinetPolicyFetchTest(
    unittest.TestCase
):
    """EDINET PDF取得の確認。"""

    def test_non_200_status_raises_fetch_error(
        self,
    ) -> None:
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=404,
            content=b"not found",
        )

        with patch(
            "update_edinet_policy_analyses.time.sleep",
        ):
            with self.assertRaises(Exception):
                fetch_edinet_pdf(
                    session,
                    create_target().pdf_url,
                )

    def test_missing_pdf_header_raises_fetch_error(
        self,
    ) -> None:
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=200,
            content=b"<html>not a pdf</html>",
        )

        with patch(
            "update_edinet_policy_analyses.time.sleep",
        ):
            with self.assertRaises(Exception):
                fetch_edinet_pdf(
                    session,
                    create_target().pdf_url,
                )

    def test_valid_pdf_returns_content_and_status(
        self,
    ) -> None:
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=200,
            content=b"%PDF-1.7 dummy",
        )

        content, status_code = fetch_edinet_pdf(
            session,
            create_target().pdf_url,
        )

        self.assertEqual(
            content,
            b"%PDF-1.7 dummy",
        )
        self.assertEqual(
            status_code,
            200,
        )


class EdinetPolicyAnalyzeTest(
    unittest.TestCase
):
    """有報PDF解析フローの確認。"""

    def test_fetch_failure_is_recorded(
        self,
    ) -> None:
        with patch(
            "update_edinet_policy_analyses.fetch_edinet_pdf",
        ) as mock_fetch:
            mock_fetch.side_effect = Exception(
                "接続エラー"
            )

            with self.assertRaises(Exception):
                analyze_edinet_policy_pdf(
                    MagicMock(),
                    create_target(),
                )

    def test_completed_analysis_has_classification(
        self,
    ) -> None:
        page_text = (
            "３【配当政策】\n"
            "当社は中期経営計画において"
            "連続増配を継続します。"
        )

        extracted = MagicMock()
        extracted.page_count = 1
        extracted.page_texts = (page_text,)
        extracted.extracted_text_length = len(
            page_text
        )

        with patch(
            "update_edinet_policy_analyses.fetch_edinet_pdf",
        ) as mock_fetch:
            mock_fetch.return_value = (
                b"%PDF-1.7 dummy",
                200,
            )

            with patch(
                "update_edinet_policy_analyses"
                ".extract_pdf_text",
            ) as mock_extract:
                mock_extract.return_value = extracted

                result = analyze_edinet_policy_pdf(
                    MagicMock(),
                    create_target(),
                )

        self.assertEqual(
            result["analysis_status"],
            "completed",
        )
        self.assertEqual(
            result["policy_classification"],
            POLICY_CLASSIFICATION_CONFIRMED,
        )
        self.assertIn(
            "連続増配",
            result["matched_phrase"],
        )
        self.assertEqual(
            result["evidence_page_number"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
