"""
TDnet PDF本文解析結果をGoogle Sheets向けに整形する。

PostgreSQLの監査用ステータスや判定値を変更せず、
本文確認状態、本文判定、一致フレーズ、
根拠文、根拠ページ番号の5列へ変換する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from load_tdnet_policy_pdf_analyses import (
    TdnetPolicyPdfAnalysisResult,
)


# ============================================================
# Google Sheets列
# ============================================================

TDNET_POLICY_ANALYSIS_HEADERS = [
    "本文確認状態",
    "本文判定",
    "本文一致フレーズ",
    "本文根拠",
    "本文根拠ページ",
]

POLICY_ANALYSIS_NOT_APPLICABLE = "対象外"
POLICY_ANALYSIS_NOT_ANALYZED = "未解析"


# ============================================================
# Google Sheets向け変換
# ============================================================

def build_policy_analysis_sheet_values(
    *,
    is_policy_candidate: bool,
    analysis_result: (
        TdnetPolicyPdfAnalysisResult | None
    ),
) -> list[Any]:
    """
    PDF本文解析結果をGoogle Sheetsの5列へ変換する。

    方針候補ではない開示は「対象外」とする。
    方針候補でDBに解析結果がない場合は「未解析」とする。
    DBに結果がある場合は監査用の値をそのまま表示する。
    """

    if not is_policy_candidate:
        return [
            POLICY_ANALYSIS_NOT_APPLICABLE,
            "",
            "",
            "",
            "",
        ]

    if analysis_result is None:
        return [
            POLICY_ANALYSIS_NOT_ANALYZED,
            "",
            "",
            "",
            "",
        ]

    if analysis_result.analysis_status != "completed":
        return [
            analysis_result.analysis_status,
            "",
            "",
            "",
            "",
        ]

    return [
        analysis_result.analysis_status,
        analysis_result.policy_classification or "",
        analysis_result.matched_phrase or "",
        analysis_result.evidence_text or "",
        (
            analysis_result.evidence_page_number
            if (
                analysis_result.evidence_page_number
                is not None
            )
            else ""
        ),
    ]


def build_policy_analysis_result_counts(
    analysis_results: dict[
        str,
        TdnetPolicyPdfAnalysisResult,
    ],
) -> dict[str, int]:
    """Google Sheets反映対象の解析結果を集計する。"""

    counts = {
        "result_count": 0,
        "pending_count": 0,
        "completed_count": 0,
        "confirmed_count": 0,
        "not_confirmed_count": 0,
        "manual_review_count": 0,
        "fetch_failed_count": 0,
        "text_extraction_failed_count": 0,
    }

    for result in analysis_results.values():
        counts["result_count"] += 1

        status_key = (
            f"{result.analysis_status}_count"
        )

        if status_key in counts:
            counts[status_key] += 1

        if result.policy_classification:
            classification_key = (
                f"{result.policy_classification}_count"
            )

            if classification_key in counts:
                counts[classification_key] += 1

    return counts
