"""
累進配当候補へのTDnet PDF本文解析結果結合テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path


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

from enrich_tdnet_policy_candidates import (  # noqa: E402
    TDNET_POLICY_ANALYSIS_RECORD_FIELDS,
    build_empty_policy_analysis_fields,
    build_policy_analysis_fields,
    enrich_candidates_with_tdnet_policy_results,
    normalize_candidate_security_code,
    normalize_policy_results,
)

from load_latest_tdnet_policy_results import (  # noqa: E402
    LatestTdnetPolicyResult,
)


# ============================================================
# テストデータ
# ============================================================

DISCLOSURE_ID = "140120260901529442"

PDF_URL = (
    "https://www.release.tdnet.info/inbs/"
    f"{DISCLOSURE_ID}.pdf"
)


def create_result(
    *,
    security_code: str = "8057",
) -> LatestTdnetPolicyResult:
    """正常な最新解析結果を作成する。"""

    return LatestTdnetPolicyResult(
        security_code=security_code,
        disclosure_id=DISCLOSURE_ID,
        published_date=date(
            2026,
            9,
            2,
        ),
        published_time=time(
            15,
            30,
        ),
        company_name="内田洋行",
        title=(
            "剰余金の配当および配当方針の変更"
            "(累進配当の導入)に関するお知らせ"
        ),
        pdf_url=PDF_URL,
        analysis_status="completed",
        policy_classification="confirmed",
        matched_phrase="累進配当の導入",
        evidence_text=(
            "剰余金の配当および配当方針の変更"
            "(累進配当の導入)に関するお知らせ"
        ),
        evidence_page_number=1,
        analyzer_version="v2",
        fetch_attempt_count=2,
        analyzed_at=datetime(
            2026,
            9,
            20,
            3,
            26,
            tzinfo=timezone.utc,
        ),
        last_error=None,
    )


def create_candidate(
    *,
    security_code: str = "8057",
    pdf_url: str = PDF_URL,
) -> dict:
    """累進配当候補レコードを作成する。"""

    return {
        "security_code": security_code,
        "company_name": "内田洋行",
        "tdnet_policy_candidate": True,
        "tdnet_policy_date": date(
            2026,
            9,
            2,
        ),
        "tdnet_policy_title": (
            "剰余金の配当および配当方針の変更"
            "(累進配当の導入)に関するお知らせ"
        ),
        "tdnet_policy_url": pdf_url,
    }


# ============================================================
# 証券コード
# ============================================================

class CandidateSecurityCodeTest(
    unittest.TestCase
):
    """候補証券コードの正規化を確認する。"""

    def test_security_code_is_normalized(
        self,
    ) -> None:
        self.assertEqual(
            normalize_candidate_security_code(
                " 245a "
            ),
            "245A",
        )

    def test_invalid_security_code_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(RuntimeError):
            normalize_candidate_security_code(
                "12345"
            )


# ============================================================
# 解析結果フィールド
# ============================================================

class PolicyAnalysisFieldTest(
    unittest.TestCase
):
    """候補用解析結果フィールドの変換を確認する。"""

    def test_empty_fields_are_created(
        self,
    ) -> None:
        fields = build_empty_policy_analysis_fields()

        self.assertEqual(
            set(fields),
            set(
                TDNET_POLICY_ANALYSIS_RECORD_FIELDS
            ),
        )
        self.assertTrue(
            all(
                value is None
                for value in fields.values()
            )
        )

    def test_result_is_converted_to_fields(
        self,
    ) -> None:
        fields = build_policy_analysis_fields(
            create_result()
        )

        self.assertEqual(
            fields[
                "tdnet_policy_analysis_status"
            ],
            "completed",
        )
        self.assertEqual(
            fields[
                "tdnet_policy_classification"
            ],
            "confirmed",
        )
        self.assertEqual(
            fields[
                "tdnet_policy_matched_phrase"
            ],
            "累進配当の導入",
        )
        self.assertEqual(
            fields[
                "tdnet_policy_evidence_page_number"
            ],
            1,
        )
        self.assertEqual(
            fields[
                "tdnet_policy_analyzer_version"
            ],
            "v2",
        )


# ============================================================
# 解析結果辞書
# ============================================================

class PolicyResultNormalizationTest(
    unittest.TestCase
):
    """解析結果辞書の検証を確認する。"""

    def test_result_key_is_normalized(
        self,
    ) -> None:
        result = create_result(
            security_code="245A",
        )

        normalized = normalize_policy_results(
            {
                "245a": result,
            }
        )

        self.assertEqual(
            list(normalized),
            ["245A"],
        )

    def test_mismatched_result_code_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(RuntimeError):
            normalize_policy_results(
                {
                    "8057": create_result(
                        security_code="245A",
                    ),
                }
            )

    def test_invalid_result_type_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            normalize_policy_results(
                {
                    "8057": object(),
                }
            )


# ============================================================
# 候補レコード結合
# ============================================================

class CandidatePolicyEnrichmentTest(
    unittest.TestCase
):
    """候補レコードへの解析結果結合を確認する。"""

    def test_matching_result_is_attached(
        self,
    ) -> None:
        candidate = create_candidate()

        enriched = (
            enrich_candidates_with_tdnet_policy_results(
                [candidate],
                {
                    "8057": create_result(),
                },
            )
        )[0]

        self.assertEqual(
            enriched[
                "tdnet_policy_analysis_status"
            ],
            "completed",
        )
        self.assertEqual(
            enriched[
                "tdnet_policy_classification"
            ],
            "confirmed",
        )
        self.assertEqual(
            enriched[
                "tdnet_policy_matched_phrase"
            ],
            "累進配当の導入",
        )
        self.assertEqual(
            enriched[
                "tdnet_policy_evidence_page_number"
            ],
            1,
        )
        self.assertEqual(
            enriched[
                "tdnet_policy_analyzer_version"
            ],
            "v2",
        )

    def test_source_record_is_not_mutated(
        self,
    ) -> None:
        candidate = create_candidate()
        original = dict(candidate)

        enrich_candidates_with_tdnet_policy_results(
            [candidate],
            {
                "8057": create_result(),
            },
        )

        self.assertEqual(
            candidate,
            original,
        )

    def test_missing_result_produces_empty_fields(
        self,
    ) -> None:
        enriched = (
            enrich_candidates_with_tdnet_policy_results(
                [create_candidate()],
                {},
            )
        )[0]

        for field_name in (
            TDNET_POLICY_ANALYSIS_RECORD_FIELDS
        ):
            self.assertIsNone(
                enriched[field_name]
            )

    def test_stale_existing_fields_are_cleared(
        self,
    ) -> None:
        candidate = create_candidate()
        candidate[
            "tdnet_policy_classification"
        ] = "confirmed"
        candidate[
            "tdnet_policy_matched_phrase"
        ] = "古い判定"

        enriched = (
            enrich_candidates_with_tdnet_policy_results(
                [candidate],
                {},
            )
        )[0]

        self.assertIsNone(
            enriched[
                "tdnet_policy_classification"
            ]
        )
        self.assertIsNone(
            enriched[
                "tdnet_policy_matched_phrase"
            ]
        )

    def test_mismatched_pdf_does_not_attach_result(
        self,
    ) -> None:
        candidate = create_candidate(
            pdf_url=(
                "https://www.release.tdnet.info/"
                "inbs/another.pdf"
            ),
        )

        enriched = (
            enrich_candidates_with_tdnet_policy_results(
                [candidate],
                {
                    "8057": create_result(),
                },
            )
        )[0]

        self.assertEqual(
            enriched["tdnet_policy_url"],
            (
                "https://www.release.tdnet.info/"
                "inbs/another.pdf"
            ),
        )
        self.assertIsNone(
            enriched[
                "tdnet_policy_analysis_status"
            ]
        )
        self.assertIsNone(
            enriched[
                "tdnet_policy_classification"
            ]
        )

    def test_result_populates_missing_policy_metadata(
        self,
    ) -> None:
        candidate = create_candidate(
            pdf_url="",
        )
        candidate["tdnet_policy_candidate"] = False
        candidate["tdnet_policy_date"] = None
        candidate["tdnet_policy_title"] = ""

        enriched = (
            enrich_candidates_with_tdnet_policy_results(
                [candidate],
                {
                    "8057": create_result(),
                },
            )
        )[0]

        self.assertTrue(
            enriched["tdnet_policy_candidate"]
        )
        self.assertEqual(
            enriched["tdnet_policy_date"],
            date(2026, 9, 2),
        )
        self.assertEqual(
            enriched["tdnet_policy_title"],
            create_result().title,
        )
        self.assertEqual(
            enriched["tdnet_policy_url"],
            PDF_URL,
        )
        self.assertEqual(
            enriched[
                "tdnet_policy_classification"
            ],
            "confirmed",
        )

    def test_alphanumeric_candidate_code_is_normalized(
        self,
    ) -> None:
        candidate = create_candidate(
            security_code="245a",
            pdf_url="",
        )
        result = create_result(
            security_code="245A",
        )

        enriched = (
            enrich_candidates_with_tdnet_policy_results(
                [candidate],
                {
                    "245A": result,
                },
            )
        )[0]

        self.assertEqual(
            enriched[
                "tdnet_policy_classification"
            ],
            "confirmed",
        )

    def test_duplicate_candidate_code_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(RuntimeError):
            enrich_candidates_with_tdnet_policy_results(
                [
                    create_candidate(),
                    create_candidate(),
                ],
                {
                    "8057": create_result(),
                },
            )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    unittest.main()
