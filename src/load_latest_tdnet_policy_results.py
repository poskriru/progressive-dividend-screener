"""
証券コードごとの最新TDnet PDF本文解析結果を
PostgreSQLから読み込む。

累進配当候補のGoogle Sheets出力と
Discord通知への反映に使用する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Iterable


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from load_tdnet_policy_pdf_analyses import (
    row_to_analysis_result,
)


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
class LatestTdnetPolicyResult:
    """証券コードごとの最新TDnet PDF本文解析結果。"""

    security_code: str
    disclosure_id: str
    published_date: date
    published_time: time | None
    company_name: str
    title: str
    pdf_url: str
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
    disclosure_id: str,
) -> str:
    """DB行の必須文字列を取得する。"""

    value = str(
        row.get(field_name) or ""
    ).strip()

    if not value:
        raise RuntimeError(
            "最新TDnet PDF本文解析結果の"
            f"{field_name}が空です。"
            f"開示ID: {disclosure_id}"
        )

    return value


def row_to_latest_policy_result(
    row: dict[str, Any],
) -> LatestTdnetPolicyResult:
    """PostgreSQLの行を最新解析結果モデルへ変換する。"""

    analysis_result = row_to_analysis_result(row)
    disclosure_id = analysis_result.disclosure_id

    security_code = str(
        row.get("security_code") or ""
    ).strip().upper()

    if not SECURITY_CODE_PATTERN.fullmatch(
        security_code
    ):
        raise RuntimeError(
            "最新TDnet PDF本文解析結果の"
            "証券コードが不正です。"
            f"開示ID: {disclosure_id}, "
            f"証券コード: {security_code}"
        )

    published_date = row.get("published_date")

    if not isinstance(published_date, date):
        raise RuntimeError(
            "最新TDnet PDF本文解析結果の"
            "公開日が不正です。"
            f"開示ID: {disclosure_id}"
        )

    published_time = row.get("published_time")

    if (
        published_time is not None
        and not isinstance(published_time, time)
    ):
        raise RuntimeError(
            "最新TDnet PDF本文解析結果の"
            "公開時刻が不正です。"
            f"開示ID: {disclosure_id}"
        )

    company_name = get_required_text(
        row,
        "company_name",
        disclosure_id=disclosure_id,
    )
    title = get_required_text(
        row,
        "title",
        disclosure_id=disclosure_id,
    )
    pdf_url = get_required_text(
        row,
        "pdf_url",
        disclosure_id=disclosure_id,
    )

    return LatestTdnetPolicyResult(
        security_code=security_code,
        disclosure_id=disclosure_id,
        published_date=published_date,
        published_time=published_time,
        company_name=company_name,
        title=title,
        pdf_url=pdf_url,
        analysis_status=(
            analysis_result.analysis_status
        ),
        policy_classification=(
            analysis_result.policy_classification
        ),
        matched_phrase=(
            analysis_result.matched_phrase
        ),
        evidence_text=(
            analysis_result.evidence_text
        ),
        evidence_page_number=(
            analysis_result.evidence_page_number
        ),
        analyzer_version=(
            analysis_result.analyzer_version
        ),
        fetch_attempt_count=(
            analysis_result.fetch_attempt_count
        ),
        analyzed_at=analysis_result.analyzed_at,
        last_error=analysis_result.last_error,
    )


# ============================================================
# PostgreSQL読込
# ============================================================

def load_latest_policy_results_with_connection(
    connection,
    security_codes: Iterable[str],
) -> dict[str, LatestTdnetPolicyResult]:
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
                disclosure_id,
                security_code,
                published_date,
                published_time,
                company_name,
                title,
                pdf_url,
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
            WHERE security_code = ANY(%s)
            ORDER BY
                security_code,
                published_date DESC,
                published_time DESC NULLS LAST,
                disclosure_id DESC;
            """,
            (normalized_codes,),
        )
        rows = cursor.fetchall()

    requested_codes = set(normalized_codes)
    results: dict[
        str,
        LatestTdnetPolicyResult,
    ] = {}

    for row in rows:
        result = row_to_latest_policy_result(
            dict(row)
        )

        if result.security_code not in requested_codes:
            raise RuntimeError(
                "要求していない証券コードの"
                "TDnet PDF本文解析結果が返されました。"
                f"証券コード: {result.security_code}"
            )

        if result.security_code in results:
            raise RuntimeError(
                "証券コードごとの最新"
                "TDnet PDF本文解析結果が重複しています。"
                f"証券コード: {result.security_code}"
            )

        results[result.security_code] = result

    return results


def load_latest_tdnet_policy_results(
    security_codes: Iterable[str],
) -> dict[str, LatestTdnetPolicyResult]:
    """指定銘柄の最新解析結果をPostgreSQLから読み込む。"""

    normalized_codes = normalize_security_codes(
        security_codes
    )

    if not normalized_codes:
        return {}

    with create_database_connection(
        "latest_tdnet_policy_results"
    ) as connection:
        return load_latest_policy_results_with_connection(
            connection,
            normalized_codes,
        )
