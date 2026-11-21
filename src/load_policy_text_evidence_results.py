"""
TDnetとEDINET有報の配当政策本文解析結果のうち、
本文一致フレーズのあるconfirmed/manual_reviewを
証券コードごとに集約してPostgreSQLから読み込む。

累進配当候補のGoogle Sheets出力への反映に使用する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection


# ============================================================
# 定数
# ============================================================

SECURITY_CODE_PATTERN = re.compile(
    r"^[0-9A-Z]{4}$"
)

POLICY_TEXT_EVIDENCE_CLASSIFICATIONS = (
    "confirmed",
    "manual_review",
)


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class PolicyTextEvidenceResult:
    """証券コードごとの最有力本文根拠。"""

    security_code: str
    source_type: str
    document_id: str
    evidence_date: date
    company_name: str
    analysis_status: str
    policy_classification: str
    matched_phrase: str
    evidence_text: str
    evidence_page_number: int | None
    analyzer_version: str
    title: str
    pdf_url: str


# ============================================================
# DB行変換
# ============================================================

def get_required_evidence_text(
    row: dict[str, Any],
    field_name: str,
    *,
    document_id: str,
) -> str:
    """DB行の必須文字列を取得する。"""

    value = str(
        row.get(field_name) or ""
    ).strip()

    if not value:
        raise RuntimeError(
            "本文根拠解析結果の"
            f"{field_name}が空です。"
            f"文書ID: {document_id}"
        )

    return value


def row_to_policy_text_evidence_result(
    row: dict[str, Any],
) -> PolicyTextEvidenceResult:
    """PostgreSQLの行を本文根拠モデルへ変換する。"""

    document_id = str(
        row.get("document_id") or ""
    ).strip()

    security_code = str(
        row.get("security_code") or ""
    ).strip().upper()

    if not SECURITY_CODE_PATTERN.fullmatch(
        security_code
    ):
        raise RuntimeError(
            "本文根拠解析結果の"
            "証券コードが不正です。"
            f"文書ID: {document_id}, "
            f"証券コード: {security_code}"
        )

    source_type = str(
        row.get("source_type") or ""
    ).strip()

    if source_type not in {"tdnet", "edinet"}:
        raise RuntimeError(
            "本文根拠解析結果の参照元が不正です。"
            f"文書ID: {document_id}, "
            f"参照元: {source_type}"
        )

    policy_classification = str(
        row.get("policy_classification") or ""
    ).strip()

    if policy_classification not in (
        POLICY_TEXT_EVIDENCE_CLASSIFICATIONS
    ):
        raise RuntimeError(
            "本文根拠解析結果の判定は"
            "confirmedまたはmanual_reviewである必要があります。"
            f"文書ID: {document_id}, "
            f"判定: {policy_classification}"
        )

    evidence_date = row.get("evidence_date")

    if not isinstance(evidence_date, date):
        raise RuntimeError(
            "本文根拠解析結果の根拠日が不正です。"
            f"文書ID: {document_id}, "
            f"根拠日: {evidence_date}"
        )

    return PolicyTextEvidenceResult(
        security_code=security_code,
        source_type=source_type,
        document_id=document_id,
        evidence_date=evidence_date,
        company_name=(
            get_required_evidence_text(
                row,
                "company_name",
                document_id=document_id,
            )
        ),
        analysis_status=(
            get_required_evidence_text(
                row,
                "analysis_status",
                document_id=document_id,
            )
        ),
        policy_classification=(
            policy_classification
        ),
        matched_phrase=(
            get_required_evidence_text(
                row,
                "matched_phrase",
                document_id=document_id,
            )
        ),
        evidence_text=(
            get_required_evidence_text(
                row,
                "evidence_text",
                document_id=document_id,
            )
        ),
        evidence_page_number=(
            row.get("evidence_page_number")
        ),
        analyzer_version=(
            get_required_evidence_text(
                row,
                "analyzer_version",
                document_id=document_id,
            )
        ),
        title=str(
            row.get("title") or ""
        ).strip(),
        pdf_url=str(
            row.get("pdf_url") or ""
        ).strip(),
    )


# ============================================================
# PostgreSQL読込
# ============================================================

def load_policy_text_evidence_results_with_connection(
    connection,
) -> list[PolicyTextEvidenceResult]:
    """本文根拠のある銘柄を既存接続で読み込む。"""

    query = """
        WITH evidence AS (
            SELECT
                security_code,
                'tdnet' AS source_type,
                disclosure_id AS document_id,
                published_date AS evidence_date,
                company_name,
                analysis_status,
                policy_classification,
                matched_phrase,
                evidence_text,
                evidence_page_number,
                analyzer_version,
                title,
                pdf_url
            FROM screener.tdnet_policy_pdf_analyses
            WHERE policy_classification
                IN ('confirmed', 'manual_review')
              AND matched_phrase IS NOT NULL
              AND matched_phrase <> ''
            UNION ALL
            SELECT
                security_code,
                'edinet' AS source_type,
                doc_id AS document_id,
                submitted_date AS evidence_date,
                company_name,
                analysis_status,
                policy_classification,
                matched_phrase,
                evidence_text,
                evidence_page_number,
                analyzer_version,
                NULL AS title,
                pdf_url
            FROM screener.edinet_policy_analyses
            WHERE policy_classification
                IN ('confirmed', 'manual_review')
              AND matched_phrase IS NOT NULL
              AND matched_phrase <> ''
        ),
        ranked AS (
            SELECT
                security_code,
                source_type,
                document_id,
                evidence_date,
                company_name,
                analysis_status,
                policy_classification,
                matched_phrase,
                evidence_text,
                evidence_page_number,
                analyzer_version,
                title,
                pdf_url,
                ROW_NUMBER() OVER (
                    PARTITION BY security_code
                    ORDER BY
                        (
                            policy_classification
                            = 'confirmed'
                        ) DESC,
                        evidence_date DESC,
                        document_id DESC
                ) AS evidence_rank
            FROM evidence
        )
        SELECT
            security_code,
            source_type,
            document_id,
            evidence_date,
            company_name,
            analysis_status,
            policy_classification,
            matched_phrase,
            evidence_text,
            evidence_page_number,
            analyzer_version,
            title,
            pdf_url
        FROM ranked
        WHERE evidence_rank = 1
        ORDER BY
            (policy_classification = 'confirmed') DESC,
            evidence_date DESC,
            security_code;
    """

    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()

    results: list[PolicyTextEvidenceResult] = []
    seen_security_codes: set[str] = set()

    for row in rows:
        result = row_to_policy_text_evidence_result(
            dict(row)
        )

        if (
            result.security_code
            in seen_security_codes
        ):
            raise RuntimeError(
                "証券コードごとの本文根拠が"
                "重複しています。"
                f"証券コード: {result.security_code}"
            )

        seen_security_codes.add(
            result.security_code
        )
        results.append(result)

    return results


def load_policy_text_evidence_results() -> list[
    PolicyTextEvidenceResult
]:
    """本文根拠のある銘柄をPostgreSQLから読み込む。"""

    with create_database_connection(
        "load_policy_text_evidence_results"
    ) as connection:
        return (
            load_policy_text_evidence_results_with_connection(
                connection
            )
        )
