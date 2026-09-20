"""
TDnet PDF本文解析結果のGoogle Sheets同期テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import datetime, timezone
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

from load_tdnet_policy_pdf_analyses import (  # noqa: E402
    TdnetPolicyPdfAnalysisResult,
)

from sync_tdnet_policy_analysis_sheets import (  # noqa: E402
    PROGRESSIVE_POLICY_HEADERS_WITH_ANALYSIS,
    TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
    build_progressive_policy_sheet_rows,
    build_tdnet_disclosure_sheet_rows,
    select_latest_policy_disclosures,
    sort_disclosures,
    sync_tdnet_policy_analysis_sheets,
)

from update_tdnet_dividend_disclosures import (  # noqa: E402
    TdnetDisclosure,
)


# ============================================================
# テストデータ
# ============================================================

def create_disclosure(
    *,
    disclosure_id: str = "140120260101000001",
    published_date: str = "2026-01-01",
    published_time: str = "15:30",
    security_code: str = "1234",
    is_policy_candidate: bool = True,
) -> TdnetDisclosure:
    """Google Sheets同期テスト用の開示を作成する。"""

    return TdnetDisclosure(
        disclosure_id=disclosure_id,
        published_date=published_date,
        published_time=published_time,
        security_code=security_code,
        company_name="テスト株式会社",
        title="株主還元方針の変更について",
        category="配当・株主還元方針",
        is_policy_candidate=is_policy_candidate,
        matched_keywords=(
            "株主還元方針",
        ),
        pdf_url=(
            "https://www.release.tdnet.info/"
            f"inbs/{disclosure_id}.pdf"
        ),
        exchange="東",
    )


def create_analysis_result(
    *,
    disclosure_id: str = "140120260101000001",
    policy_classification: str = "confirmed",
) -> TdnetPolicyPdfAnalysisResult:
    """Google Sheets同期テスト用の解析結果を作成する。"""

    return TdnetPolicyPdfAnalysisResult(
        disclosure_id=disclosure_id,
        analysis_status="completed",
        policy_classification=(
            policy_classification
        ),
        matched_phrase="累進配当方針を導入",
        evidence_text=(
            "当社は累進配当方針を導入します。"
        ),
        evidence_page_number=2,
        analyzer_version="v1",
        fetch_attempt_count=1,
        analyzed_at=datetime(
            2026,
            1,
            1,
            6,
            30,
            tzinfo=timezone.utc,
        ),
        last_error=None,
    )


# ============================================================
# 列定義
# ============================================================

class TdnetPolicyAnalysisSheetHeaderTest(
    unittest.TestCase
):
    """本文解析列を追加したヘッダーを確認する。"""

    def test_disclosure_headers_have_seventeen_columns(
        self,
    ) -> None:
        self.assertEqual(
            len(
                TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS
            ),
            17,
        )
        self.assertEqual(
            TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS[
                -5:
            ],
            [
                "本文確認状態",
                "本文判定",
                "本文一致フレーズ",
                "本文根拠",
                "本文根拠ページ",
            ],
        )

    def test_policy_headers_have_fifteen_columns(
        self,
    ) -> None:
        self.assertEqual(
            len(
                PROGRESSIVE_POLICY_HEADERS_WITH_ANALYSIS
            ),
            15,
        )
        self.assertEqual(
            PROGRESSIVE_POLICY_HEADERS_WITH_ANALYSIS[
                9:14
            ],
            [
                "本文確認状態",
                "本文判定",
                "本文一致フレーズ",
                "本文根拠",
                "本文根拠ページ",
            ],
        )


# ============================================================
# 並び順・重複
# ============================================================

class TdnetPolicyAnalysisSheetSortingTest(
    unittest.TestCase
):
    """Google Sheetsへ出力する開示の並びを確認する。"""

    def test_disclosures_are_sorted(
        self,
    ) -> None:
        later = create_disclosure(
            disclosure_id="later",
            published_date="2026-01-02",
        )
        earlier = create_disclosure(
            disclosure_id="earlier",
            published_date="2026-01-01",
        )

        result = sort_disclosures(
            [
                later,
                earlier,
            ]
        )

        self.assertEqual(
            [
                disclosure.disclosure_id
                for disclosure in result
            ],
            [
                "earlier",
                "later",
            ],
        )

    def test_duplicate_disclosure_id_is_rejected(
        self,
    ) -> None:
        disclosure = create_disclosure()

        with self.assertRaises(RuntimeError):
            sort_disclosures(
                [
                    disclosure,
                    disclosure,
                ]
            )


# ============================================================
# TDnet配当開示シート
# ============================================================

class TdnetDisclosureSheetRowsTest(
    unittest.TestCase
):
    """TDnet配当開示シートの行生成を確認する。"""

    def test_confirmed_result_is_added(
        self,
    ) -> None:
        disclosure = create_disclosure()
        result = create_analysis_result()

        rows = build_tdnet_disclosure_sheet_rows(
            [disclosure],
            {
                disclosure.disclosure_id: result,
            },
        )

        self.assertEqual(
            len(rows[0]),
            17,
        )
        self.assertEqual(
            rows[0][-5:],
            [
                "completed",
                "confirmed",
                "累進配当方針を導入",
                "当社は累進配当方針を導入します。",
                2,
            ],
        )

    def test_non_candidate_is_marked_not_applicable(
        self,
    ) -> None:
        disclosure = create_disclosure(
            is_policy_candidate=False
        )

        rows = build_tdnet_disclosure_sheet_rows(
            [disclosure],
            {},
        )

        self.assertEqual(
            rows[0][-5:],
            [
                "対象外",
                "",
                "",
                "",
                "",
            ],
        )


# ============================================================
# 累進配当方針候補シート
# ============================================================

class ProgressivePolicySheetRowsTest(
    unittest.TestCase
):
    """最新方針候補シートの行生成を確認する。"""

    def test_latest_disclosure_is_selected(
        self,
    ) -> None:
        older = create_disclosure(
            disclosure_id="older",
            published_date="2026-01-01",
        )
        newer = create_disclosure(
            disclosure_id="newer",
            published_date="2026-01-02",
        )

        latest = select_latest_policy_disclosures(
            [
                newer,
                older,
            ]
        )

        self.assertEqual(
            latest["1234"].disclosure_id,
            "newer",
        )

    def test_policy_row_contains_analysis_result(
        self,
    ) -> None:
        disclosure = create_disclosure()
        result = create_analysis_result()

        rows = build_progressive_policy_sheet_rows(
            [disclosure],
            {
                disclosure.disclosure_id: result,
            },
            updated_at="2026-01-01 16:00:00",
        )

        self.assertEqual(
            len(rows[0]),
            15,
        )
        self.assertEqual(
            rows[0][9:14],
            [
                "completed",
                "confirmed",
                "累進配当方針を導入",
                "当社は累進配当方針を導入します。",
                2,
            ],
        )


# ============================================================
# Google Sheets同期
# ============================================================

class TdnetPolicyAnalysisSheetSyncTest(
    unittest.TestCase
):
    """2シートへの書込みを確認する。"""

    @patch(
        "sync_tdnet_policy_analysis_sheets."
        "write_sheet"
    )
    def test_two_sheets_are_written(
        self,
        mocked_write_sheet,
    ) -> None:
        disclosure = create_disclosure()
        result = create_analysis_result()
        sheets_service = MagicMock()

        summary = sync_tdnet_policy_analysis_sheets(
            sheets_service,
            "spreadsheet-id",
            [disclosure],
            {
                disclosure.disclosure_id: result,
            },
        )

        self.assertEqual(
            mocked_write_sheet.call_count,
            2,
        )

        first_call = (
            mocked_write_sheet.call_args_list[0]
        )
        second_call = (
            mocked_write_sheet.call_args_list[1]
        )

        self.assertEqual(
            first_call.args[2],
            "TDnet配当開示",
        )
        self.assertEqual(
            first_call.args[3],
            TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
        )
        self.assertEqual(
            second_call.args[2],
            "累進配当方針候補",
        )
        self.assertEqual(
            second_call.args[3],
            PROGRESSIVE_POLICY_HEADERS_WITH_ANALYSIS,
        )
        self.assertEqual(
            summary["disclosure_row_count"],
            1,
        )
        self.assertEqual(
            summary["policy_row_count"],
            1,
        )
        self.assertEqual(
            summary["confirmed_count"],
            1,
        )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
