"""
TDnet PDF本文解析結果をPostgreSQLから読み込む。

Google Sheetsへの本文確認状態、判定根拠、
根拠ページ番号の反映に使用する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection


# ============================================================
# 定数
# ============================================================

ALLOWED_ANALYSIS_STATUSES = {
    "pending",
    "completed",
    "fetch_failed",
    "text_extraction_failed",
}

ALLOWED_POLICY_CLASSIFICATIONS = {
    "confirmed",
    "not_confirmed",
    "manual_review",
}


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class TdnetPolicyPdfAnalysisResult:
    """Google Sheetsへ反映するPDF本文解析結果。"""

    disclosure_id: str
    analysis_status: str
    policy_classification: str | None
    matched_phrase: str | None
    evidence_text: str | None
    evidence_page_number: int | None
    analyzer_version: str
    fetch_attempt_count: int
    analyzed_at: datetime | None
    last_error: str | None


# ============================================================
# 入力正規化
# ============================================================

def normalize_disclosure_ids(
    disclosure_ids: Iterable[str],
) -> list[str]:
    """開示IDを重複のない並びへ正規化する。"""

    if isinstance(disclosure_ids, (str, bytes)):
        raise TypeError(
            "disclosure_idsには文字列ではなく、"
            "開示IDのIterableを指定してください。"
        )

    normalized_ids = {
        str(disclosure_id).strip()
        for disclosure_id in disclosure_ids
        if str(disclosure_id).strip()
    }

    return sorted(normalized_ids)


# ============================================================
# DB行変換
# ============================================================

def row_to_analysis_result(
    row: dict[str, Any],
) -> TdnetPolicyPdfAnalysisResult:
    """PostgreSQLの行を解析結果モデルへ変換する。"""

    disclosure_id = str(
        row.get("disclosure_id") or ""
    ).strip()
    analysis_status = str(
        row.get("analysis_status") or ""
    ).strip()
    raw_classification = row.get(
        "policy_classification"
    )
    policy_classification = (
        str(raw_classification).strip()
        if raw_classification is not None
        else None
    )
    analyzer_version = str(
        row.get("analyzer_version") or ""
    ).strip()

    if not disclosure_id:
        raise RuntimeError(
            "TDnet PDF本文解析結果の"
            "開示IDが空です。"
        )

    if (
        analysis_status
        not in ALLOWED_ANALYSIS_STATUSES
    ):
        raise RuntimeError(
            "TDnet PDF本文解析結果の"
            "analysis_statusが不正です。"
            f"開示ID: {disclosure_id}, "
            f"状態: {analysis_status}"
        )

    if (
        policy_classification is not None
        and policy_classification
        not in ALLOWED_POLICY_CLASSIFICATIONS
    ):
        raise RuntimeError(
            "TDnet PDF本文解析結果の"
            "policy_classificationが不正です。"
            f"開示ID: {disclosure_id}, "
            f"判定: {policy_classification}"
        )

    if (
        analysis_status == "completed"
        and policy_classification is None
    ):
        raise RuntimeError(
            "completedのTDnet PDF本文解析結果に"
            "policy_classificationがありません。"
            f"開示ID: {disclosure_id}"
        )

    if (
        analysis_status != "completed"
        and policy_classification is not None
    ):
        raise RuntimeError(
            "未完了のTDnet PDF本文解析結果に"
            "policy_classificationがあります。"
            f"開示ID: {disclosure_id}"
        )

    if not analyzer_version:
        raise RuntimeError(
            "TDnet PDF本文解析結果の"
            "analyzer_versionが空です。"
            f"開示ID: {disclosure_id}"
        )

    raw_evidence_page_number = row.get(
        "evidence_page_number"
    )
    evidence_page_number = (
        int(raw_evidence_page_number)
        if raw_evidence_page_number is not None
        else None
    )

    if (
        evidence_page_number is not None
        and evidence_page_number < 1
    ):
        raise RuntimeError(
            "TDnet PDF本文解析結果の"
            "根拠ページ番号が不正です。"
            f"開示ID: {disclosure_id}, "
            f"ページ番号: {evidence_page_number}"
        )

    fetch_attempt_count = int(
        row.get("fetch_attempt_count") or 0
    )

    if fetch_attempt_count < 0:
        raise RuntimeError(
            "TDnet PDF本文解析結果の"
            "取得試行回数が不正です。"
            f"開示ID: {disclosure_id}, "
            f"取得試行回数: {fetch_attempt_count}"
        )

    return TdnetPolicyPdfAnalysisResult(
        disclosure_id=disclosure_id,
        analysis_status=analysis_status,
        policy_classification=(
            policy_classification
        ),
        matched_phrase=(
            str(row["matched_phrase"]).strip()
            if row.get("matched_phrase") is not None
            else None
        ),
        evidence_text=(
            str(row["evidence_text"]).strip()
            if row.get("evidence_text") is not None
            else None
        ),
        evidence_page_number=(
            evidence_page_number
        ),
        analyzer_version=analyzer_version,
        fetch_attempt_count=fetch_attempt_count,
        analyzed_at=row.get("analyzed_at"),
        last_error=(
            str(row["last_error"]).strip()
            if row.get("last_error") is not None
            else None
        ),
    )


# ============================================================
# PostgreSQL読込
# ============================================================

def load_analysis_results_with_connection(
    connection,
    disclosure_ids: Iterable[str],
) -> dict[str, TdnetPolicyPdfAnalysisResult]:
    """指定した開示IDの解析結果を既存接続で読み込む。"""

    normalized_ids = normalize_disclosure_ids(
        disclosure_ids
    )

    if not normalized_ids:
        return {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                disclosure_id,
                analysis_status,
                policy_classification,
                matched_phrase,
                evidence_text,
                evidence_page_number,
                analyzer_version,
                fetch_attempt_count,
                analyzed_at,
                last_error
            FROM screener.tdnet_policy_pdf_analyses
            WHERE disclosure_id = ANY(%s)
            ORDER BY disclosure_id;
            """,
            (normalized_ids,),
        )
        rows = cursor.fetchall()

    results: dict[
        str,
        TdnetPolicyPdfAnalysisResult,
    ] = {}

    for row in rows:
        result = row_to_analysis_result(
            dict(row)
        )

        if result.disclosure_id in results:
            raise RuntimeError(
                "TDnet PDF本文解析結果の"
                "開示IDが重複しています。"
                f"開示ID: {result.disclosure_id}"
            )

        results[result.disclosure_id] = result

    return results


def load_tdnet_policy_pdf_analysis_results(
    disclosure_ids: Iterable[str],
) -> dict[str, TdnetPolicyPdfAnalysisResult]:
    """指定した開示IDの解析結果をPostgreSQLから読み込む。"""

    normalized_ids = normalize_disclosure_ids(
        disclosure_ids
    )

    if not normalized_ids:
        return {}

    with create_database_connection(
        "tdnet_policy_pdf_sheet_results"
    ) as connection:
        return load_analysis_results_with_connection(
            connection,
            normalized_ids,
        )
