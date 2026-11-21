"""
EDINET有価証券報告書の配当政策について、
PDF本文を取得して機械判定を保存する。

対象は銘柄マスターに有効な証券コードのうち、
EDINET書類テーブルに登録された
有価証券報告書（訂正報告書を除く）の
最新提出分のみ。

PDFファイル本体はDBへ保存せず、
SHA-256、文字数、判定根拠となった短い文章だけを
screener.edinet_policy_analysesへUPSERTする。

1回の実行で処理する件数は上限で制御し、
残りは次回実行時に処理する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
import io
import os
import time
from dataclasses import dataclass
from datetime import date

# ============================================================
# プロジェクト内モジュール
# ============================================================

import requests

from analyze_tdnet_policy_pdfs import (
    ANALYZER_VERSION,
    classify_policy_pages,
)
from database import create_database_connection
from fetch_tdnet_policy_pdfs import (
    MAXIMUM_PDF_PAGE_COUNT,
    MAXIMUM_PDF_SIZE_BYTES,
    TdnetPdfFetchError,
    TdnetPdfTextExtractionError,
    extract_pdf_text,
)


# ============================================================
# 定数
# ============================================================

EDINET_PDF_URL_TEMPLATE = (
    "https://disclosure2dl.edinet-fsa.go.jp/"
    "searchdocument/pdf/{doc_id}.pdf"
)

ANALYSIS_STATUS_COMPLETED = "completed"
ANALYSIS_STATUS_FETCH_FAILED = "fetch_failed"
ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED = (
    "text_extraction_failed"
)

MAXIMUM_FETCH_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 60
REQUEST_INTERVAL_SECONDS = 0.5
MAX_RETRIES = 3

DEFAULT_MAXIMUM_TARGETS = 100


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class EdinetPolicyPdfTarget:
    """EDINET有報配当政策解析の対象書類。"""

    doc_id: str
    security_code: str
    submitted_date: date
    fiscal_period_end: date | None
    company_name: str
    pdf_url: str


# ============================================================
# 対象書類の選択
# ============================================================

def select_edinet_policy_targets(
    connection,
    *,
    maximum_targets: int,
) -> tuple[
    list[EdinetPolicyPdfTarget],
    dict[str, int],
]:
    """
    最新有報のうち本文解析が必要な対象だけを選ぶ。

    未処理、解析ロジック更新、再試行可能な失敗を対象にし、
    completedかつ同じanalyzer_versionの書類は再取得しない。
    analyzer_versionが変わった場合は試行回数にかかわらず
    再解析する。
    """

    if maximum_targets <= 0:
        raise ValueError(
            "maximum_targetsは1以上で指定してください。"
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                latest.doc_id,
                latest.security_code,
                latest.submitted_date,
                latest.fiscal_period_end,
                latest.company_name,
                latest.pdf_url,
                existing.analysis_status,
                existing.analyzer_version,
                existing.fetch_attempt_count
            FROM (
                SELECT DISTINCT ON (documents.security_code)
                    documents.doc_id,
                    documents.security_code,
                    documents.submitted_at::date
                        AS submitted_date,
                    documents.period_end
                        AS fiscal_period_end,
                    documents.filer_name
                        AS company_name,
                    documents.source_url
                        AS pdf_url
                FROM screener.edinet_documents documents
                INNER JOIN screener.securities securities
                    ON securities.security_code =
                        documents.security_code
                    AND securities.is_active = TRUE
                WHERE documents.document_type_code = '120'
                  AND documents.security_code IS NOT NULL
                  AND documents.source_url IS NOT NULL
                  AND (
                      documents.document_type_name IS NULL
                      OR documents.document_type_name
                          NOT LIKE '%訂正%'
                  )
                ORDER BY
                    documents.security_code,
                    documents.period_end DESC NULLS LAST,
                    documents.submitted_at DESC,
                    documents.doc_id DESC
            ) latest
            LEFT JOIN
                screener.edinet_policy_analyses existing
                ON existing.doc_id = latest.doc_id
            WHERE
                existing.doc_id IS NULL
                OR existing.analyzer_version
                    <> %(analyzer_version)s
                OR (
                    existing.analysis_status
                        IN (
                            'fetch_failed',
                            'text_extraction_failed'
                        )
                    AND existing.fetch_attempt_count
                        < %(maximum_attempts)s
                )
            ORDER BY
                latest.security_code,
                latest.submitted_date DESC;
            """,
            {
                "analyzer_version": ANALYZER_VERSION,
                "maximum_attempts": MAXIMUM_FETCH_ATTEMPTS,
            },
        )
        rows = cursor.fetchall()

    selection_summary = {
        "candidate_count": len(rows),
    }

    targets = [
        EdinetPolicyPdfTarget(
            doc_id=str(row["doc_id"]).strip(),
            security_code=str(row["security_code"]).strip().upper(),
            submitted_date=row["submitted_date"],
            fiscal_period_end=row["fiscal_period_end"],
            company_name=str(row["company_name"]).strip(),
            pdf_url=str(row["pdf_url"]).strip(),
        )
        for row in rows
    ]

    return targets[:maximum_targets], selection_summary


# ============================================================
# EDINET PDFの取得
# ============================================================

def fetch_edinet_pdf(
    session: requests.Session,
    pdf_url: str,
) -> tuple[bytes, int]:
    """EDINET PDFをリトライ付きで取得する。"""

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(
                pdf_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 429:
                wait_seconds = 5 * (attempt + 1)
                print(
                    f"EDINET PDF取得 429: "
                    f"{wait_seconds}秒待機します。"
                )
                time.sleep(wait_seconds)
                continue

            if response.status_code != 200:
                raise TdnetPdfFetchError(
                    "EDINET PDFのHTTPステータスが"
                    "200ではありません。"
                    f"ステータス: {response.status_code}"
                )

            content = response.content

            if content[:5] != b"%PDF-":
                raise TdnetPdfFetchError(
                    "EDINET PDF応答にPDFヘッダーがありません。"
                    f"先頭: {content[:16]!r}"
                )

            if len(content) > MAXIMUM_PDF_SIZE_BYTES:
                raise TdnetPdfFetchError(
                    "EDINET PDFのサイズが上限を超えました。"
                    f"サイズ: {len(content):,}"
                )

            return content, response.status_code

        except TdnetPdfFetchError as error:
            last_error = error
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
        except requests.RequestException as error:
            last_error = error
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    raise TdnetPdfFetchError(
        "EDINET PDFの取得に失敗しました。"
        f"エラー種別: {type(last_error).__name__}"
    ) from last_error


# ============================================================
# 本文判定
# ============================================================

def analyze_edinet_policy_pdf(
    session: requests.Session,
    target: EdinetPolicyPdfTarget,
) -> dict:
    """
    有報PDFを取得して本文判定を行う。

    戻り値はUPSERT用の列値辞書。
    """

    result: dict = {
        "doc_id": target.doc_id,
        "security_code": target.security_code,
        "submitted_date": target.submitted_date,
        "fiscal_period_end": target.fiscal_period_end,
        "company_name": target.company_name,
        "pdf_url": target.pdf_url,
        "content_sha256": None,
        "content_type": None,
        "content_length_bytes": None,
        "http_status_code": None,
        "page_count": None,
        "extracted_text_length": None,
        "analysis_status": "pending",
        "policy_classification": None,
        "matched_phrase": None,
        "evidence_text": None,
        "evidence_page_number": None,
        "fetched_at": None,
        "analyzed_at": None,
        "last_error": None,
    }

    try:
        content, status_code = fetch_edinet_pdf(
            session,
            target.pdf_url,
        )
    except TdnetPdfFetchError as error:
        result[
            "analysis_status"
        ] = ANALYSIS_STATUS_FETCH_FAILED
        result["last_error"] = str(error)[:2000]
        return result

    result[
        "content_sha256"
    ] = hashlib.sha256(content).hexdigest()
    result["content_type"] = "application/pdf"
    result["content_length_bytes"] = len(content)
    result["http_status_code"] = status_code
    result["fetched_at"] = "now()"

    try:
        extracted = extract_pdf_text(
            content,
            maximum_page_count=MAXIMUM_PDF_PAGE_COUNT,
        )
    except TdnetPdfTextExtractionError as error:
        result[
            "analysis_status"
        ] = ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED
        result["last_error"] = str(error)[:2000]
        return result

    result["page_count"] = extracted.page_count
    result[
        "extracted_text_length"
    ] = extracted.extracted_text_length

    analysis = classify_policy_pages(
        extracted.page_texts
    )

    result[
        "analysis_status"
    ] = ANALYSIS_STATUS_COMPLETED
    result["analyzed_at"] = "now()"

    if analysis.evidence is not None:
        result[
            "matched_phrase"
        ] = analysis.evidence.matched_phrase
        result[
            "evidence_text"
        ] = analysis.evidence.evidence_text
        result[
            "evidence_page_number"
        ] = analysis.evidence.page_number

    result["policy_classification"] = (
        analysis.classification
    )

    return result


# ============================================================
# UPSERT
# ============================================================

def save_edinet_policy_result(
    connection,
    result: dict,
) -> None:
    """有報解析結果を書類ID単位でUPSERTする。"""

    query = """
        INSERT INTO screener.edinet_policy_analyses (
            doc_id,
            security_code,
            submitted_date,
            fiscal_period_end,
            company_name,
            pdf_url,
            content_sha256,
            content_type,
            content_length_bytes,
            http_status_code,
            page_count,
            extracted_text_length,
            analysis_status,
            policy_classification,
            matched_phrase,
            evidence_text,
            evidence_page_number,
            analyzer_version,
            fetch_attempt_count,
            fetched_at,
            analyzed_at,
            last_error
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            1,
            CASE
                WHEN %s
                THEN CURRENT_TIMESTAMP
                ELSE NULL
            END,
            CASE
                WHEN %s
                THEN CURRENT_TIMESTAMP
                ELSE NULL
            END,
            %s
        )
        ON CONFLICT (doc_id)
        DO UPDATE SET
            security_code = EXCLUDED.security_code,
            submitted_date = EXCLUDED.submitted_date,
            fiscal_period_end = EXCLUDED.fiscal_period_end,
            company_name = EXCLUDED.company_name,
            pdf_url = EXCLUDED.pdf_url,
            content_sha256 = EXCLUDED.content_sha256,
            content_type = EXCLUDED.content_type,
            content_length_bytes =
                EXCLUDED.content_length_bytes,
            http_status_code = EXCLUDED.http_status_code,
            page_count = EXCLUDED.page_count,
            extracted_text_length =
                EXCLUDED.extracted_text_length,
            analysis_status = EXCLUDED.analysis_status,
            policy_classification =
                EXCLUDED.policy_classification,
            matched_phrase = EXCLUDED.matched_phrase,
            evidence_text = EXCLUDED.evidence_text,
            evidence_page_number =
                EXCLUDED.evidence_page_number,
            analyzer_version = EXCLUDED.analyzer_version,
            fetch_attempt_count =
                screener.edinet_policy_analyses
                    .fetch_attempt_count
                + 1,
            fetched_at = EXCLUDED.fetched_at,
            analyzed_at = EXCLUDED.analyzed_at,
            last_error = EXCLUDED.last_error;
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                result["doc_id"],
                result["security_code"],
                result["submitted_date"],
                result["fiscal_period_end"],
                result["company_name"],
                result["pdf_url"],
                result["content_sha256"],
                result["content_type"],
                result["content_length_bytes"],
                result["http_status_code"],
                result["page_count"],
                result["extracted_text_length"],
                result["analysis_status"],
                result["policy_classification"],
                result["matched_phrase"],
                result["evidence_text"],
                result["evidence_page_number"],
                ANALYZER_VERSION,
                result["fetched_at"] is not None,
                result["analyzed_at"] is not None,
                result["last_error"],
            ),
        )


# ============================================================
# エントリポイント
# ============================================================

def main() -> None:
    maximum_targets = int(
        os.getenv(
            "MAX_POLICY_DOCS",
            str(DEFAULT_MAXIMUM_TARGETS),
        ).strip() or str(DEFAULT_MAXIMUM_TARGETS)
    )

    with create_database_connection(
        "update_edinet_policy_analyses"
    ) as connection:
        targets, selection_summary = (
            select_edinet_policy_targets(
                connection,
                maximum_targets=maximum_targets,
            )
        )

        print(
            "EDINET有報配当政策の解析対象を選択しました。"
            f"候補: "
            f"{selection_summary['candidate_count']:,}, "
            f"今回処理: {len(targets):,}"
        )

        if not targets:
            print(
                "EDINET有報配当政策の解析対象はありません。"
            )
            return

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; "
                    "progressive-dividend-screener/0.4)"
                )
            }
        )

        summary: dict[str, int] = {
            "completed": 0,
            "confirmed": 0,
            "manual_review": 0,
            "fetch_failed": 0,
            "text_extraction_failed": 0,
        }

        for index, target in enumerate(
            targets,
            start=1,
        ):
            result = analyze_edinet_policy_pdf(
                session,
                target,
            )

            save_edinet_policy_result(
                connection,
                result,
            )

            connection.commit()

            status = result["analysis_status"]
            summary[status] = summary.get(status, 0) + 1

            if (
                result["policy_classification"]
                == "confirmed"
            ):
                summary["confirmed"] += 1
            elif (
                result["policy_classification"]
                == "manual_review"
            ):
                summary["manual_review"] += 1

            if index % 20 == 0 or index == len(targets):
                print(
                    "EDINET有報解析の進捗: "
                    f"{index:,}/{len(targets):,} "
                    f"(confirmed: {summary['confirmed']:,})"
                )

            time.sleep(REQUEST_INTERVAL_SECONDS)

        print(
            "EDINET有報配当政策の解析が完了しました。"
            f"処理: {len(targets):,}, "
            f"completed: {summary['completed']:,}, "
            f"confirmed: {summary['confirmed']:,}, "
            f"manual_review: {summary['manual_review']:,}, "
            f"取得失敗: {summary['fetch_failed']:,}, "
            "本文抽出失敗: "
            f"{summary['text_extraction_failed']:,}"
        )


if __name__ == "__main__":
    main()
