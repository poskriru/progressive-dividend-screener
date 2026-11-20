"""
証券コードごとの最新EDINET有報配当政策本文解析結果を
PostgreSQLから読み込む。

累進配当候補のGoogle Sheets出力への反映に使用する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable


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


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class LatestEdinetPolicyResult:
    """証券コードごとの最新有報配当政策本文解析結果。"""

    security_code: str
    doc_id: str
    submitted_date: date
    fiscal_period_end: date | None
    company_name: str
    pdf_url: str
    analysis_status: str
    policy_classification: str | None
    matched_phrase: str | None
    evidence_text: str | None
    evidence_page_number: int | None
    analyzer_version: str
    analyzed_at: datetime | None
    last_error: str | None


# ============================================================
# 入力正規化
# ============================================================

def normalize_security_codes(
    security_codes: Iterable[str],
) -> list[str]:
    """証券コードを検証し、重複のない並びへ正規化する。"""

    if isinstance(security_codes, (str, bytes)):
        raise TypeError(
            "security_codesには文字列ではなく、"
            "証券コードのIterableを指定してください。"
        )

    normalized_codes: set[str] = set()

    for raw_security_code in security_codes:
        security_code = str(
            raw_security_code
        ).strip().upper()

        if not security_code:
            continue

        if not SECURITY_CODE_PATTERN.fullmatch(
            security_code
        ):
            raise ValueError(
                "証券コードの形式が不正です。"
                f"証券コード: {security_code}"
            )

        normalized_codes.add(security_code)

    return sorted(normalized_codes)


# ============================================================
# DB行変換
# ============================================================

def get_required_text(
    row: dict[str, Any],
    field_name: str,
    *,
    doc_id: str,
) -> str:
    """DB行の必須文字列を取得する。"""

    value = str(
        row.get(field_name) or ""
    ).strip()

    if not value:
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            f"{field_name}が空です。"
            f"書類ID: {doc_id}"
        )

    return value


def get_optional_text(
    row: dict[str, Any],
    field_name: str,
) -> str | None:
    """DB行の任意文字列を取得する。"""

    value = str(
        row.get(field_name) or ""
    ).strip()

    return value or None


def get_optional_int(
    row: dict[str, Any],
    field_name: str,
    *,
    doc_id: str,
) -> int | None:
    """DB行の任意整数を取得する。"""

    value = row.get(field_name)

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            f"{field_name}が不正です。"
            f"書類ID: {doc_id}"
        ) from error


def get_optional_datetime(
    row: dict[str, Any],
    field_name: str,
    *,
    doc_id: str,
) -> datetime | None:
    """DB行の任意日時を取得する。"""

    value = row.get(field_name)

    if value is None:
        return None

    if not isinstance(value, datetime):
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            f"{field_name}が不正です。"
            f"書類ID: {doc_id}"
        )

    return value


def row_to_latest_policy_result(
    row: dict[str, Any],
) -> LatestEdinetPolicyResult:
    """PostgreSQLの行を最新解析結果モデルへ変換する。"""

    doc_id = str(row.get("doc_id") or "").strip()

    if not doc_id:
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            "doc_idが空です。"
        )

    security_code = str(
        row.get("security_code") or ""
    ).strip().upper()

    if not SECURITY_CODE_PATTERN.fullmatch(
        security_code
    ):
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            "証券コードが不正です。"
            f"書類ID: {doc_id}, "
            f"証券コード: {security_code}"
        )

    submitted_date = row.get("submitted_date")

    if not isinstance(submitted_date, date):
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            "提出日が不正です。"
            f"書類ID: {doc_id}"
        )

    fiscal_period_end = row.get("fiscal_period_end")

    if (
        fiscal_period_end is not None
        and not isinstance(fiscal_period_end, date)
    ):
        raise RuntimeError(
            "最新EDINET有報配当政策解析結果の"
            "期末日が不正です。"
            f"書類ID: {doc_id}"
        )

    analysis_status = get_required_text(
        row,
        "analysis_status",
        doc_id=doc_id,
    )
    analyzer_version = get_required_text(
        row,
        "analyzer_version",
        doc_id=doc_id,
    )

    return LatestEdinetPolicyResult(
        security_code=security_code,
        doc_id=doc_id,
        submitted_date=submitted_date,
        fiscal_period_end=fiscal_period_end,
        company_name=get_required_text(
            row,
            "company_name",
            doc_id=doc_id,
        ),
        pdf_url=get_required_text(
            row,
            "pdf_url",
            doc_id=doc_id,
        ),
        analysis_status=analysis_status,
        policy_classification=get_optional_text(
            row,
            "policy_classification",
        ),
        matched_phrase=get_optional_text(
            row,
            "matched_phrase",
        ),
        evidence_text=get_optional_text(
            row,
            "evidence_text",
        ),
        evidence_page_number=get_optional_int(
            row,
            "evidence_page_number",
            doc_id=doc_id,
        ),
        analyzer_version=analyzer_version,
        analyzed_at=get_optional_datetime(
            row,
            "analyzed_at",
            doc_id=doc_id,
        ),
        last_error=get_optional_text(
            row,
            "last_error",
        ),
    )


# ============================================================
# PostgreSQL読込
# ============================================================

def load_latest_policy_results_with_connection(
    connection,
    security_codes: Iterable[str],
) -> dict[str, LatestEdinetPolicyResult]:
    """指定銘柄の最新解析結果を既存接続で読み込む。"""

    normalized_codes = normalize_security_codes(
        security_codes
    )

    if not normalized_codes:
        return {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (security_code)
                doc_id,
                security_code,
                submitted_date,
                fiscal_period_end,
                company_name,
                pdf_url,
                analysis_status,
                policy_classification,
                matched_phrase,
                evidence_text,
                evidence_page_number,
                analyzer_version,
                analyzed_at,
                last_error
            FROM screener.edinet_policy_analyses
            WHERE security_code = ANY(%s)
            ORDER BY
                security_code,
                submitted_date DESC,
                doc_id DESC;
            """,
            (normalized_codes,),
        )
        rows = cursor.fetchall()

    requested_codes = set(normalized_codes)
    results: dict[
        str,
        LatestEdinetPolicyResult,
    ] = {}

    for row in rows:
        result = row_to_latest_policy_result(
            dict(row)
        )

        if result.security_code not in requested_codes:
            raise RuntimeError(
                "要求していない証券コードの"
                "有報配当政策解析結果が返されました。"
                f"証券コード: {result.security_code}"
            )

        if result.security_code in results:
            raise RuntimeError(
                "証券コードごとの最新"
                "有報配当政策解析結果が重複しています。"
                f"証券コード: {result.security_code}"
            )

        results[result.security_code] = result

    return results


def load_latest_edinet_policy_results(
    security_codes: Iterable[str],
) -> dict[str, LatestEdinetPolicyResult]:
    """指定銘柄の最新解析結果をPostgreSQLから読み込む。"""

    normalized_codes = normalize_security_codes(
        security_codes
    )

    if not normalized_codes:
        return {}

    with create_database_connection(
        "latest_edinet_policy_results"
    ) as connection:
        return load_latest_policy_results_with_connection(
            connection,
            normalized_codes,
        )
