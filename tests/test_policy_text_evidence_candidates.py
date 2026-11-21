"""
本文根拠（confirmed/manual_review）による
累進配当候補追加のテスト。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock


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

from export_progressive_dividend_candidates import (  # noqa: E402
    CANDIDATE_CONDITION_SQL,
    CONDITION_MATCHED_REASON,
    POLICY_TEXT_REASON_BY_CLASSIFICATION,
    CandidateCriteria,
    annotate_candidates_with_policy_text_evidence,
    apply_policy_text_evidence_columns,
    build_candidate_condition_parameters,
    build_candidate_rows,
    build_policy_text_evidence_records,
    load_screener_rows_by_security_codes,
)
from load_policy_text_evidence_results import (  # noqa: E402
    PolicyTextEvidenceResult,
    load_policy_text_evidence_results_with_connection,
    row_to_policy_text_evidence_result,
)


# ============================================================
# テストデータ
# ============================================================

def create_evidence(
    *,
    security_code: str = "7203",
    source_type: str = "tdnet",
    policy_classification: str = "confirmed",
    evidence_date: date = date(2026, 9, 2),
    document_id: str = "140120260901529442",
) -> PolicyTextEvidenceResult:
    """本文根拠モデルを作成する。"""

    return PolicyTextEvidenceResult(
        security_code=security_code,
        source_type=source_type,
        document_id=document_id,
        evidence_date=evidence_date,
        company_name="テスト株式会社",
        analysis_status="completed",
        policy_classification=policy_classification,
        matched_phrase="累進配当の導入",
        evidence_text="累進配当の導入に関するお知らせ",
        evidence_page_number=1,
        analyzer_version="v3",
        title="配当方針の変更",
        pdf_url=(
            "https://www.release.tdnet.info/"
            f"inbs/{document_id}.pdf"
        ),
    )


def create_connection(rows: list[dict]):
    """指定行を返すDB接続モックを作成する。"""

    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    connection.cursor.return_value.__enter__.return_value = (
        cursor
    )

    return connection, cursor


def create_screener_row(
    security_code: str = "7203",
) -> dict:
    """スクリーナー指標行を作成する。"""

    return {
        "security_code": security_code,
        "company_name": "テスト株式会社",
        "market": "プライム",
        "industry_33_name": "情報通信業",
        "trading_date": date(2026, 11, 19),
        "close_price": Decimal("1000"),
        "dividend_yield_percent": Decimal("2.0"),
        "payout_ratio_percent": Decimal("40"),
        "per_ratio": Decimal("10"),
        "pbr_ratio": Decimal("1"),
        "roe_percent": Decimal("10"),
        "equity_ratio_percent": Decimal("30"),
        "per_undervaluation_years": Decimal("3"),
        "investment_efficiency_10y": Decimal("2"),
        "net_cash_jpy": Decimal("1000000"),
        "treasury_shares_ratio_percent": Decimal("1"),
        "free_cash_flow_jpy": Decimal("10000"),
        "dividend_cagr_5y_percent": Decimal("5"),
        "dividend_increase_count_5y": 3,
        "dividend_unchanged_count_5y": 2,
        "consecutive_non_decrease_periods": 6,
        "consecutive_increase_periods": 3,
        "dividend_latest_annual_dividend_yen": Decimal("40"),
        "oldest_annual_dividend_yen_5y": Decimal("30"),
        "fiscal_periods_5y": [date(2026, 3, 31)],
        "annual_dividends_yen_5y": [Decimal("40")],
        "is_progressive_dividend_5y_raw": True,
        "progressive_dividend_status_5y": "progressive",
        "dividend_adjustment_status": "complete",
        "is_adjustment_coverage_complete": True,
        "latest_cumulative_adjustment_factor": Decimal("1"),
        "oldest_cumulative_adjustment_factor_5y": Decimal("1"),
        "dividend_cagr_5y_adjusted_percent": Decimal("5"),
        "is_progressive_dividend_5y_adjusted": True,
        "progressive_dividend_status_5y_adjusted": (
            "progressive"
        ),
        "adjusted_fiscal_periods_5y": [date(2026, 3, 31)],
        "adjusted_annual_dividends_yen_5y": [Decimal("40")],
        "financial_source_url": "https://example.com",
    }


# ============================================================
# DB行変換
# ============================================================

class RowToEvidenceResultTest(unittest.TestCase):
    """DB行を本文根拠モデルへ変換するテスト。"""

    def test_tdnet_row_is_converted(self) -> None:
        row = {
            "security_code": "7203",
            "source_type": "tdnet",
            "document_id": "140120260901529442",
            "evidence_date": date(2026, 9, 2),
            "company_name": "テスト株式会社",
            "analysis_status": "completed",
            "policy_classification": "confirmed",
            "matched_phrase": "累進配当の導入",
            "evidence_text": "累進配当の導入に関するお知らせ",
            "evidence_page_number": 1,
            "analyzer_version": "v3",
            "title": "配当方針の変更",
            "pdf_url": "https://www.release.tdnet.info/inbs/x.pdf",
        }

        result = row_to_policy_text_evidence_result(row)

        self.assertEqual(
            result.security_code,
            "7203",
        )
        self.assertEqual(
            result.source_type,
            "tdnet",
        )
        self.assertEqual(
            result.policy_classification,
            "confirmed",
        )

    def test_edinet_row_has_empty_title(self) -> None:
        row = {
            "security_code": "5706",
            "source_type": "edinet",
            "document_id": "S100YIC7",
            "evidence_date": date(2026, 6, 24),
            "company_name": "三井金属",
            "analysis_status": "completed",
            "policy_classification": "manual_review",
            "matched_phrase": "目指します",
            "evidence_text": "累進配当を目指します。",
            "evidence_page_number": 12,
            "analyzer_version": "v3",
            "title": None,
            "pdf_url": "https://example.com/x.pdf",
        }

        result = row_to_policy_text_evidence_result(row)

        self.assertEqual(
            result.source_type,
            "edinet",
        )
        self.assertEqual(result.title, "")
        self.assertEqual(
            result.policy_classification,
            "manual_review",
        )

    def test_not_confirmed_classification_is_rejected(self) -> None:
        row = {
            "security_code": "7203",
            "source_type": "tdnet",
            "document_id": "x",
            "evidence_date": date(2026, 9, 2),
            "company_name": "テスト株式会社",
            "analysis_status": "completed",
            "policy_classification": "not_confirmed",
            "matched_phrase": "累進配当",
            "evidence_text": "x",
            "evidence_page_number": 1,
            "analyzer_version": "v3",
            "title": "t",
            "pdf_url": "https://example.com/x.pdf",
        }

        with self.assertRaises(RuntimeError):
            row_to_policy_text_evidence_result(row)

    def test_invalid_security_code_is_rejected(self) -> None:
        row = {
            "security_code": "72035",
            "source_type": "tdnet",
            "document_id": "x",
            "evidence_date": date(2026, 9, 2),
            "company_name": "テスト株式会社",
            "analysis_status": "completed",
            "policy_classification": "confirmed",
            "matched_phrase": "累進配当",
            "evidence_text": "x",
            "evidence_page_number": 1,
            "analyzer_version": "v3",
            "title": "t",
            "pdf_url": "https://example.com/x.pdf",
        }

        with self.assertRaises(RuntimeError):
            row_to_policy_text_evidence_result(row)


# ============================================================
# PostgreSQL読込
# ============================================================

class LoadEvidenceResultsTest(unittest.TestCase):
    """本文根拠のPostgreSQL読込テスト。"""

    def test_returns_empty_list_without_rows(self) -> None:
        connection, _ = create_connection([])

        results = (
            load_policy_text_evidence_results_with_connection(
                connection
            )
        )

        self.assertEqual(results, [])

    def test_rejects_duplicate_security_codes(self) -> None:
        first = {
            "security_code": "7203",
            "source_type": "tdnet",
            "document_id": "a",
            "evidence_date": date(2026, 9, 2),
            "company_name": "テスト株式会社",
            "analysis_status": "completed",
            "policy_classification": "confirmed",
            "matched_phrase": "累進配当の導入",
            "evidence_text": "x",
            "evidence_page_number": 1,
            "analyzer_version": "v3",
            "title": "t",
            "pdf_url": "https://example.com/a.pdf",
        }
        second = {
            **first,
            "document_id": "b",
        }
        connection, _ = create_connection([first, second])

        with self.assertRaises(RuntimeError):
            load_policy_text_evidence_results_with_connection(
                connection
            )

    def test_query_filters_confirmed_and_manual_review(self) -> None:
        connection, cursor = create_connection([])

        load_policy_text_evidence_results_with_connection(
            connection
        )

        query = cursor.execute.call_args.args[0]
        normalized_query = " ".join(query.split())

        self.assertIn(
            "policy_classification IN ('confirmed', 'manual_review')",
            normalized_query,
        )
        self.assertIn(
            "matched_phrase IS NOT NULL",
            normalized_query,
        )
        self.assertIn("UNION ALL", normalized_query)
        self.assertIn("ROW_NUMBER() OVER", normalized_query)


# ============================================================
# 本文根拠あり列
# ============================================================

class CandidateEvidenceFlagColumnTest(unittest.TestCase):
    """候補シート「本文根拠あり」列のテスト。"""

    def test_row_with_evidence_is_flagged(self) -> None:
        record = {
            "security_code": "7203",
            "policy_text_evidence": create_evidence(),
        }

        row = build_candidate_rows([record])[0]

        self.assertIs(row[62], True)

    def test_row_without_evidence_is_not_flagged(
        self,
    ) -> None:
        record = {"security_code": "7203"}

        row = build_candidate_rows([record])[0]

        self.assertIs(row[62], False)


# ============================================================
# 指標行読込
# ============================================================

def create_criteria() -> CandidateCriteria:
    """テスト用の抽出条件を作成する。"""

    return CandidateCriteria(
        min_dividend_yield_percent=Decimal("3"),
        max_payout_ratio_percent=Decimal("70"),
        max_per_ratio=Decimal("25"),
        max_pbr_ratio=Decimal("3"),
        min_roe_percent=Decimal("8"),
        max_per_discount_years=Decimal("5.5"),
        min_investment_efficiency=Decimal("1.7"),
        require_positive_free_cash_flow=True,
        max_candidates=300,
    )


class LoadScreenerRowsByCodesTest(unittest.TestCase):
    """本文根拠銘柄の指標行読込テスト。"""

    def test_applies_same_quantitative_conditions(
        self,
    ) -> None:
        connection, cursor = create_connection([])

        load_screener_rows_by_security_codes(
            connection,
            ["7203"],
            create_criteria(),
        )

        query = " ".join(
            cursor.execute.call_args.args[0].split()
        )

        self.assertIn(
            "security_code = ANY(%s)",
            query,
        )
        self.assertIn(
            "is_progressive_dividend_5y_adjusted IS TRUE",
            query,
        )
        self.assertIn(
            "dividend_yield_percent >= %s",
            query,
        )
        self.assertIn(
            "payout_ratio_percent BETWEEN 0 AND %s",
            query,
        )

        parameters = cursor.execute.call_args.args[1]

        self.assertEqual(parameters[0], ["7203"])
        self.assertEqual(
            parameters[1:],
            build_candidate_condition_parameters(
                create_criteria()
            ),
        )

    def test_returns_empty_list_without_codes(self) -> None:
        rows = load_screener_rows_by_security_codes(
            MagicMock(),
            [],
            create_criteria(),
        )

        self.assertEqual(rows, [])


# ============================================================
# 候補レコード変換
# ============================================================

class BuildEvidenceRecordsTest(unittest.TestCase):
    """本文根拠レコードを候補レコードへ変換するテスト。"""

    def test_appends_reason_and_evidence(self) -> None:
        evidence = create_evidence()
        record = build_policy_text_evidence_records(
            [evidence],
            [create_screener_row("7203")],
            set(),
            max_candidates=100,
        )[0]

        self.assertEqual(
            record["candidate_added_reason"],
            "本文根拠（confirmed）",
        )
        self.assertEqual(
            record["policy_text_evidence"],
            evidence,
        )

    def test_excludes_existing_candidates(self) -> None:
        evidence = create_evidence()

        records = build_policy_text_evidence_records(
            [evidence],
            [create_screener_row("7203")],
            {"7203"},
            max_candidates=100,
        )

        self.assertEqual(records, [])

    def test_skips_missing_screener_rows(self) -> None:
        evidence = create_evidence(
            security_code="9999"
        )

        records = build_policy_text_evidence_records(
            [evidence],
            [create_screener_row("7203")],
            set(),
            max_candidates=100,
        )

        self.assertEqual(records, [])

    def test_cap_limits_appended_records(self) -> None:
        evidence_first = create_evidence(
            security_code="1111"
        )
        evidence_second = create_evidence(
            security_code="2222",
            policy_classification="manual_review",
        )

        records = build_policy_text_evidence_records(
            [evidence_first, evidence_second],
            [
                create_screener_row("1111"),
                create_screener_row("2222"),
            ],
            set(),
            max_candidates=1,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["security_code"],
            "1111",
        )

    def test_zero_max_candidates_returns_empty(self) -> None:
        records = build_policy_text_evidence_records(
            [create_evidence()],
            [create_screener_row("7203")],
            set(),
            max_candidates=0,
        )

        self.assertEqual(records, [])

    def test_duplicate_screener_rows_are_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            build_policy_text_evidence_records(
                [create_evidence()],
                [
                    create_screener_row("7203"),
                    create_screener_row("7203"),
                ],
                set(),
                max_candidates=100,
            )

    def test_manual_review_reason_is_labeled(self) -> None:
        self.assertEqual(
            POLICY_TEXT_REASON_BY_CLASSIFICATION[
                "manual_review"
            ],
            "本文根拠（manual_review）",
        )


# ============================================================
# 既存候補への根拠設定
# ============================================================

class AnnotateCandidatesTest(unittest.TestCase):
    """既存候補の本文根拠アノテーションテスト。"""

    def test_annotates_candidate_with_evidence(self) -> None:
        evidence = create_evidence()
        record = {"security_code": "7203"}

        annotated_count = (
            annotate_candidates_with_policy_text_evidence(
                [record],
                [evidence],
            )
        )

        self.assertEqual(annotated_count, 1)
        self.assertEqual(
            record["candidate_added_reason"],
            "本文根拠（confirmed）",
        )
        self.assertEqual(
            record["policy_text_evidence"],
            evidence,
        )

    def test_ignores_candidates_without_evidence(self) -> None:
        record = {
            "security_code": "7203",
            "candidate_added_reason": (
                CONDITION_MATCHED_REASON
            ),
        }

        annotated_count = (
            annotate_candidates_with_policy_text_evidence(
                [record],
                [create_evidence(security_code="5706")],
            )
        )

        self.assertEqual(annotated_count, 0)
        self.assertEqual(
            record["candidate_added_reason"],
            CONDITION_MATCHED_REASON,
        )
        self.assertNotIn(
            "policy_text_evidence",
            record,
        )

    def test_invalid_classification_is_rejected(self) -> None:
        evidence = create_evidence(
            policy_classification="not_confirmed"
        )
        record = {"security_code": "7203"}

        with self.assertRaises(RuntimeError):
            annotate_candidates_with_policy_text_evidence(
                [record],
                [evidence],
            )


# ============================================================
# 本文列の上書き
# ============================================================

class ApplyEvidenceColumnsTest(unittest.TestCase):
    """本文根拠で追加した候補の本文列上書きテスト。"""

    def test_tdnet_evidence_overwrites_tdnet_columns(
        self,
    ) -> None:
        evidence = create_evidence()
        record = {
            "security_code": "7203",
            "candidate_added_reason": (
                "本文根拠（confirmed）"
            ),
            "policy_text_evidence": evidence,
            "tdnet_policy_matched_phrase": "",
        }

        apply_policy_text_evidence_columns([record])

        self.assertEqual(
            record["tdnet_policy_candidate"],
            True,
        )
        self.assertEqual(
            record["tdnet_policy_date"],
            date(2026, 9, 2),
        )
        self.assertEqual(
            record["tdnet_policy_classification"],
            "confirmed",
        )
        self.assertEqual(
            record["tdnet_policy_matched_phrase"],
            "累進配当の導入",
        )
        self.assertEqual(
            record["tdnet_policy_analyzer_version"],
            "v3",
        )

    def test_edinet_evidence_overwrites_edinet_columns(
        self,
    ) -> None:
        evidence = create_evidence(
            security_code="5706",
            source_type="edinet",
            document_id="S100YIC7",
        )
        record = {
            "security_code": "5706",
            "candidate_added_reason": (
                "本文根拠（confirmed）"
            ),
            "policy_text_evidence": evidence,
        }

        apply_policy_text_evidence_columns([record])

        self.assertEqual(
            record["edinet_policy_analysis_status"],
            "completed",
        )
        self.assertEqual(
            record["edinet_policy_classification"],
            "confirmed",
        )
        self.assertEqual(
            record["edinet_policy_matched_phrase"],
            "累進配当の導入",
        )
        self.assertNotIn(
            "tdnet_policy_classification",
            record,
        )

    def test_records_without_evidence_are_untouched(
        self,
    ) -> None:
        record = {
            "security_code": "7203",
            "candidate_added_reason": (
                CONDITION_MATCHED_REASON
            ),
        }

        apply_policy_text_evidence_columns([record])

        self.assertNotIn(
            "tdnet_policy_classification",
            record,
        )


# ============================================================
# 抽出条件
# ============================================================

class CriteriaPolicyTextRowsTest(unittest.TestCase):
    """本文根拠追加上限の設定テスト。"""

    def test_to_settings_contains_policy_text_rows(
        self,
    ) -> None:
        criteria = CandidateCriteria(
            min_dividend_yield_percent=Decimal("3"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            max_per_discount_years=Decimal("5.5"),
            min_investment_efficiency=Decimal("1.7"),
            require_positive_free_cash_flow=True,
            max_candidates=300,
        )

        settings = criteria.to_settings()

        self.assertEqual(
            settings[
                "CANDIDATE_MAX_POLICY_TEXT_ROWS"
            ],
            "100",
        )

    def test_zero_disables_evidence_append(self) -> None:
        criteria = CandidateCriteria(
            min_dividend_yield_percent=Decimal("3"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            max_per_discount_years=Decimal("5.5"),
            min_investment_efficiency=Decimal("1.7"),
            require_positive_free_cash_flow=True,
            max_candidates=300,
            max_policy_text_candidates=0,
        )

        self.assertEqual(
            criteria.max_policy_text_candidates,
            0,
        )


if __name__ == "__main__":
    unittest.main()
