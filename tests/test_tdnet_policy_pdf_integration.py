"""
TDnet一覧取得処理とPDF本文解析処理の統合テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import patch


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

from update_tdnet_dividend_disclosures import (  # noqa: E402
    TdnetDisclosure,
    analyze_fetched_policy_disclosures,
    build_policy_pdf_targets,
    disclosure_to_policy_pdf_target,
    parse_disclosure_published_date,
    parse_disclosure_published_time,
)


# ============================================================
# テストデータ
# ============================================================

def create_disclosure(
    *,
    disclosure_id: str = "140120260101000001",
    is_policy_candidate: bool = True,
    published_date: str = "2026-01-01",
    published_time: str = "15:30",
) -> TdnetDisclosure:
    """統合テスト用のTDnet開示を作成する。"""

    return TdnetDisclosure(
        disclosure_id=disclosure_id,
        published_date=published_date,
        published_time=published_time,
        security_code="1234",
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


# ============================================================
# 日付・時刻変換
# ============================================================

class TdnetPolicyPdfDateTimeTest(
    unittest.TestCase
):
    """TDnet文字列の日付・時刻変換を確認する。"""

    def test_iso_date_is_converted(
        self,
    ) -> None:
        self.assertEqual(
            parse_disclosure_published_date(
                "2026-01-01"
            ),
            date(2026, 1, 1),
        )

    def test_hour_and_minute_are_converted(
        self,
    ) -> None:
        self.assertEqual(
            parse_disclosure_published_time(
                "15:30"
            ),
            time(15, 30),
        )

    def test_seconds_are_converted(
        self,
    ) -> None:
        self.assertEqual(
            parse_disclosure_published_time(
                "15:30:45"
            ),
            time(15, 30, 45),
        )

    def test_empty_time_returns_none(
        self,
    ) -> None:
        self.assertIsNone(
            parse_disclosure_published_time("")
        )

    def test_invalid_date_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(RuntimeError):
            parse_disclosure_published_date(
                "2026/01/01"
            )

    def test_invalid_time_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(RuntimeError):
            parse_disclosure_published_time(
                "午後3時30分"
            )


# ============================================================
# 解析対象変換
# ============================================================

class TdnetPolicyPdfTargetConversionTest(
    unittest.TestCase
):
    """TDnet方針候補の解析対象変換を確認する。"""

    def test_policy_candidate_is_converted(
        self,
    ) -> None:
        target = (
            disclosure_to_policy_pdf_target(
                create_disclosure()
            )
        )

        self.assertEqual(
            target.disclosure_id,
            "140120260101000001",
        )
        self.assertEqual(
            target.security_code,
            "1234",
        )
        self.assertEqual(
            target.published_date,
            date(2026, 1, 1),
        )
        self.assertEqual(
            target.published_time,
            time(15, 30),
        )

    def test_non_policy_disclosure_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            disclosure_to_policy_pdf_target(
                create_disclosure(
                    is_policy_candidate=False
                )
            )

    def test_non_policy_disclosure_is_filtered(
        self,
    ) -> None:
        targets = build_policy_pdf_targets(
            [
                create_disclosure(
                    disclosure_id=(
                        "140120260101000001"
                    ),
                    is_policy_candidate=True,
                ),
                create_disclosure(
                    disclosure_id=(
                        "140120260101000002"
                    ),
                    is_policy_candidate=False,
                ),
            ]
        )

        self.assertEqual(
            len(targets),
            1,
        )
        self.assertEqual(
            targets[0].disclosure_id,
            "140120260101000001",
        )

    def test_targets_are_sorted_by_date_and_time(
        self,
    ) -> None:
        targets = build_policy_pdf_targets(
            [
                create_disclosure(
                    disclosure_id=(
                        "140120260102000001"
                    ),
                    published_date="2026-01-02",
                    published_time="09:00",
                ),
                create_disclosure(
                    disclosure_id=(
                        "140120260101000001"
                    ),
                    published_date="2026-01-01",
                    published_time="15:30",
                ),
            ]
        )

        self.assertEqual(
            [
                target.disclosure_id
                for target in targets
            ],
            [
                "140120260101000001",
                "140120260102000001",
            ],
        )

    def test_duplicate_disclosure_id_is_rejected(
        self,
    ) -> None:
        disclosure = create_disclosure()

        with self.assertRaises(RuntimeError):
            build_policy_pdf_targets(
                [
                    disclosure,
                    disclosure,
                ]
            )


# ============================================================
# 一括処理接続
# ============================================================

class TdnetPolicyPdfProcessingIntegrationTest(
    unittest.TestCase
):
    """解析対象が保存処理へ渡されることを確認する。"""

    @patch(
        "update_tdnet_dividend_disclosures."
        "process_tdnet_policy_pdf_targets"
    )
    def test_policy_targets_are_passed_to_processor(
        self,
        mocked_process,
    ) -> None:
        mocked_process.return_value = {
            "target_count": 1,
            "processing_target_count": 1,
            "completed_skipped_count": 0,
            "retry_exhausted_count": 0,
            "completed_count": 1,
            "confirmed_count": 1,
            "not_confirmed_count": 0,
            "manual_review_count": 0,
            "fetch_failed_count": 0,
            "text_extraction_failed_count": 0,
        }

        result = (
            analyze_fetched_policy_disclosures(
                [
                    create_disclosure(),
                    create_disclosure(
                        disclosure_id=(
                            "140120260101000002"
                        ),
                        is_policy_candidate=False,
                    ),
                ]
            )
        )

        mocked_process.assert_called_once()
        targets = (
            mocked_process.call_args.args[0]
        )

        self.assertEqual(
            len(targets),
            1,
        )
        self.assertEqual(
            targets[0].disclosure_id,
            "140120260101000001",
        )
        self.assertEqual(
            mocked_process.call_args.kwargs[
                "maximum_attempts"
            ],
            3,
        )
        self.assertEqual(
            result["confirmed_count"],
            1,
        )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
