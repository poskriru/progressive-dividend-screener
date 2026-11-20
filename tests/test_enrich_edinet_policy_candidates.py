"""
累進配当候補へのEDINET有報配当政策解析結果付与テスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date
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

from enrich_edinet_policy_candidates import (  # noqa: E402
    build_empty_policy_analysis_fields,
    build_policy_analysis_fields,
    enrich_candidates_with_edinet_policy_results,
)

from load_latest_edinet_policy_results import (  # noqa: E402
    LatestEdinetPolicyResult,
)


# ============================================================
# テストデータ
# ============================================================

def create_result(
    *,
    security_code: str = "8057",
    classification: str | None = "confirmed",
) -> LatestEdinetPolicyResult:
    """正常な解析結果を作成する。"""

    return LatestEdinetPolicyResult(
        security_code=security_code,
        doc_id="S100YIC7",
        submitted_date=date(2026, 6, 24),
        fiscal_period_end=date(2026, 3, 31),
        company_name="内田洋行",
        pdf_url=(
            "https://disclosure2dl.edinet-fsa.go.jp/"
            "searchdocument/pdf/S100YIC7.pdf"
        ),
        analysis_status="completed",
        policy_classification=classification,
        matched_phrase="連続増配",
        evidence_text=(
            "当社は連続増配を継続します。"
        ),
        evidence_page_number=12,
        analyzer_version="v3",
        analyzed_at=None,
        last_error=None,
    )


# ============================================================
# フィールド作成
# ============================================================

class EdinetPolicyFieldsTest(
    unittest.TestCase
):
    """解析結果フィールドの作成を確認する。"""

    def test_empty_fields_are_created(
        self,
    ) -> None:
        fields = build_empty_policy_analysis_fields()

        self.assertEqual(
            sorted(fields),
            [
                "edinet_policy_analysis_status",
                "edinet_policy_analyzer_version",
                "edinet_policy_classification",
                "edinet_policy_evidence_page_number",
                "edinet_policy_evidence_text",
                "edinet_policy_matched_phrase",
            ],
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
                "edinet_policy_analysis_status"
            ],
            "completed",
        )
        self.assertEqual(
            fields[
                "edinet_policy_classification"
            ],
            "confirmed",
        )
        self.assertEqual(
            fields[
                "edinet_policy_matched_phrase"
            ],
            "連続増配",
        )
        self.assertEqual(
            fields[
                "edinet_policy_evidence_page_number"
            ],
            12,
        )
        self.assertEqual(
            fields[
                "edinet_policy_analyzer_version"
            ],
            "v3",
        )


# ============================================================
# 候補への反映
# ============================================================

class EdinetEnrichCandidatesTest(
    unittest.TestCase
):
    """候補レコードへの解析結果付与を確認する。"""

    def test_matching_result_is_attached(
        self,
    ) -> None:
        records = [
            {
                "security_code": "8057",
                "company_name": "内田洋行",
            }
        ]

        enriched = (
            enrich_candidates_with_edinet_policy_results(
                records,
                {"8057": create_result()},
            )
        )

        self.assertEqual(
            len(enriched),
            1,
        )
        self.assertEqual(
            enriched[0][
                "edinet_policy_classification"
            ],
            "confirmed",
        )
        self.assertEqual(
            enriched[0][
                "edinet_policy_matched_phrase"
            ],
            "連続増配",
        )

    def test_missing_result_produces_empty_fields(
        self,
    ) -> None:
        records = [
            {
                "security_code": "1904",
                "company_name": "大成温調",
            }
        ]

        enriched = (
            enrich_candidates_with_edinet_policy_results(
                records,
                {},
            )
        )

        self.assertIsNone(
            enriched[0][
                "edinet_policy_classification"
            ],
        )
        self.assertIsNone(
            enriched[0][
                "edinet_policy_matched_phrase"
            ],
        )

    def test_stale_existing_fields_are_cleared(
        self,
    ) -> None:
        records = [
            {
                "security_code": "8057",
                "company_name": "内田洋行",
                "edinet_policy_classification":
                    "confirmed",
                "edinet_policy_matched_phrase":
                    "古いフレーズ",
            }
        ]

        enriched = (
            enrich_candidates_with_edinet_policy_results(
                records,
                {},
            )
        )

        self.assertIsNone(
            enriched[0][
                "edinet_policy_classification"
            ],
        )
        self.assertIsNone(
            enriched[0][
                "edinet_policy_matched_phrase"
            ],
        )

    def test_source_record_is_not_mutated(
        self,
    ) -> None:
        records = [
            {
                "security_code": "8057",
                "company_name": "内田洋行",
            }
        ]

        enrich_candidates_with_edinet_policy_results(
            records,
            {"8057": create_result()},
        )

        self.assertNotIn(
            "edinet_policy_classification",
            records[0],
        )

    def test_mismatched_result_code_is_rejected(
        self,
    ) -> None:
        records = [
            {
                "security_code": "8057",
                "company_name": "内田洋行",
            }
        ]

        result = create_result(
            security_code="1904",
        )

        with self.assertRaises(RuntimeError):
            enrich_candidates_with_edinet_policy_results(
                records,
                {"8057": result},
            )

    def test_duplicate_candidate_code_is_rejected(
        self,
    ) -> None:
        records = [
            {
                "security_code": "8057",
                "company_name": "内田洋行",
            },
            {
                "security_code": "8057",
                "company_name": "内田洋行",
            },
        ]

        with self.assertRaises(RuntimeError):
            enrich_candidates_with_edinet_policy_results(
                records,
                {},
            )


if __name__ == "__main__":
    unittest.main()
