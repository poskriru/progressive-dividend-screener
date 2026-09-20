"""
TDnetの方針候補PDFを取得・抽出・判定し、
PostgreSQLへ監査可能な形で保存する。

個別PDFの取得失敗や本文抽出失敗はDBへ記録し、
他のPDF処理を継続する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import re
import time
from dataclasses import dataclass
from datetime import date, time as datetime_time
from typing import Iterable


# ============================================================
# プロジェクト内モジュール
# ============================================================

from analyze_tdnet_policy_pdfs import (
    ANALYZER_VERSION,
    PolicyAnalysis,
    classify_policy_pages,
)

from database import create_database_connection

from fetch_tdnet_policy_pdfs import (
    ExtractedPdfText,
    FetchedPdf,
    TdnetPdfFetchError,
    TdnetPdfTextExtractionError,
    create_pdf_http_session,
    extract_pdf_text,
    fetch_tdnet_pdf,
)


# ============================================================
# 定数
# ============================================================

ANALYSIS_STATUS_COMPLETED = "completed"
ANALYSIS_STATUS_FETCH_FAILED = "fetch_failed"
ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED = (
    "text_extraction_failed"
)

REQUEST_INTERVAL_SECONDS = 0.5
MAXIMUM_ERROR_LENGTH = 2000

SECURITY_CODE_PATTERN = re.compile(
    r"^[0-9A-Z]{4}$"
)


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class TdnetPolicyPdfTarget:
    """解析対象となるTDnet方針候補。"""

    disclosure_id: str
    security_code: str
    published_date: date
    published_time: datetime_time | None
    company_name: str
    title: str
    pdf_url: str


@dataclass(frozen=True)
class TdnetPolicyPdfProcessingResult:
    """1件のPDF解析・保存結果。"""

    analysis_status: str
    policy_classification: str | None
    content_sha256: str | None = None
    content_type: str | None = None
    content_length_bytes: int | None = None
    http_status_code: int | None = None
    page_count: int | None = None
    extracted_text_length: int | None = None
    matched_phrase: str | None = None
    evidence_text: str | None = None
    evidence_page_number: int | None = None
    last_error: str | None = None

    @property
    def fetched_successfully(self) -> bool:
        """PDF取得が完了している場合にTRUEを返す。"""

        return self.content_sha256 is not None


# ============================================================
# 入力検証
# ============================================================

def validate_target(
    target: TdnetPolicyPdfTarget,
) -> None:
    """DB保存前に解析対象の必須項目を検証する。"""

    if not target.disclosure_id.strip():
        raise ValueError(
            "TDnet開示IDが空です。"
        )

    security_code = target.security_code.strip().upper()

    if not SECURITY_CODE_PATTERN.fullmatch(
        security_code
    ):
        raise ValueError(
            "TDnet解析対象の証券コードが不正です。"
            f"証券コード: {target.security_code}"
        )

    if not isinstance(
        target.published_date,
        date,
    ):
        raise ValueError(
            "TDnet解析対象の公開日がdate型ではありません。"
        )

    if (
        target.published_time is not None
        and not isinstance(
            target.published_time,
            datetime_time,
        )
    ):
        raise ValueError(
            "TDnet解析対象の公開時刻がtime型ではありません。"
        )

    if not target.company_name.strip():
        raise ValueError(
            "TDnet解析対象の会社名が空です。"
        )

    if not target.title.strip():
        raise ValueError(
            "TDnet解析対象の表題が空です。"
        )

    if not target.pdf_url.strip():
        raise ValueError(
            "TDnet解析対象のPDF URLが空です。"
        )


# ============================================================
# エラー文字列
# ============================================================

def build_error_message(
    error: Exception,
) -> str:
    """DB保存用の短いエラー文字列を作成する。"""

    detail = re.sub(
        r"\s+",
        " ",
        str(error),
    ).strip()

    message = type(error).__name__

    if detail:
        message += f": {detail}"

    if len(message) > MAXIMUM_ERROR_LENGTH:
        message = (
            message[
                :MAXIMUM_ERROR_LENGTH - 1
            ]
            + "…"
        )

    return message


# ============================================================
# 処理結果作成
# ============================================================

def build_completed_result(
    fetched_pdf: FetchedPdf,
    extracted_pdf: ExtractedPdfText,
    policy_analysis: PolicyAnalysis,
) -> TdnetPolicyPdfProcessingResult:
    """正常完了したPDFの保存結果を作成する。"""

    evidence = policy_analysis.evidence

    return TdnetPolicyPdfProcessingResult(
        analysis_status=ANALYSIS_STATUS_COMPLETED,
        policy_classification=(
            policy_analysis.classification
        ),
        content_sha256=fetched_pdf.content_sha256,
        content_type=fetched_pdf.content_type,
        content_length_bytes=(
            fetched_pdf.content_length_bytes
        ),
        http_status_code=(
            fetched_pdf.http_status_code
        ),
        page_count=extracted_pdf.page_count,
        extracted_text_length=(
            extracted_pdf.extracted_text_length
        ),
        matched_phrase=(
            evidence.matched_phrase
            if evidence
            else None
        ),
        evidence_text=(
            evidence.evidence_text
            if evidence
            else None
        ),
        evidence_page_number=(
            evidence.page_number
            if evidence
            else None
        ),
        last_error=None,
    )


def build_fetch_failed_result(
    error: Exception,
) -> TdnetPolicyPdfProcessingResult:
    """PDF取得失敗の保存結果を作成する。"""

    return TdnetPolicyPdfProcessingResult(
        analysis_status=(
            ANALYSIS_STATUS_FETCH_FAILED
        ),
        policy_classification=None,
        last_error=build_error_message(error),
    )


def build_extraction_failed_result(
    fetched_pdf: FetchedPdf,
    error: Exception,
) -> TdnetPolicyPdfProcessingResult:
    """本文抽出失敗の保存結果を作成する。"""

    return TdnetPolicyPdfProcessingResult(
        analysis_status=(
            ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED
        ),
        policy_classification=None,
        content_sha256=fetched_pdf.content_sha256,
        content_type=fetched_pdf.content_type,
        content_length_bytes=(
            fetched_pdf.content_length_bytes
        ),
        http_status_code=(
            fetched_pdf.http_status_code
        ),
        last_error=build_error_message(error),
    )


# ============================================================
# PostgreSQL保存
# ============================================================

def save_processing_result(
    connection,
    target: TdnetPolicyPdfTarget,
    result: TdnetPolicyPdfProcessingResult,
) -> None:
    """PDF解析結果を開示ID単位でUPSERTする。"""

    validate_target(target)

    query = """
        INSERT INTO screener.tdnet_policy_pdf_analyses (
            disclosure_id,
            security_code,
            published_date,
            published_time,
            company_name,
            title,
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
            %s,
            1,
            CASE
                WHEN %s
                THEN CURRENT_TIMESTAMP
                ELSE NULL
            END,
            CURRENT_TIMESTAMP,
            %s
        )
        ON CONFLICT (disclosure_id)
        DO UPDATE SET
            security_code = EXCLUDED.security_code,
            published_date = EXCLUDED.published_date,
            published_time = EXCLUDED.published_time,
            company_name = EXCLUDED.company_name,
            title = EXCLUDED.title,
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
                screener.tdnet_policy_pdf_analyses
                    .fetch_attempt_count
                + 1,
            fetched_at = EXCLUDED.fetched_at,
            analyzed_at = EXCLUDED.analyzed_at,
            last_error = EXCLUDED.last_error;
    """

    parameters = (
        target.disclosure_id.strip(),
        target.security_code.strip().upper(),
        target.published_date,
        target.published_time,
        target.company_name.strip(),
        target.title.strip(),
        target.pdf_url.strip(),
        result.content_sha256,
        result.content_type,
        result.content_length_bytes,
        result.http_status_code,
        result.page_count,
        result.extracted_text_length,
        result.analysis_status,
        result.policy_classification,
        result.matched_phrase,
        result.evidence_text,
        result.evidence_page_number,
        ANALYZER_VERSION,
        result.fetched_successfully,
        result.last_error,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            parameters,
        )


# ============================================================
# 1件処理
# ============================================================

def analyze_and_store_tdnet_policy_pdf(
    connection,
    session,
    target: TdnetPolicyPdfTarget,
) -> TdnetPolicyPdfProcessingResult:
    """1件のTDnet PDFを解析してDBへ保存する。"""

    validate_target(target)

    try:
        fetched_pdf = fetch_tdnet_pdf(
            session,
            target.pdf_url,
        )
    except TdnetPdfFetchError as error:
        result = build_fetch_failed_result(
            error
        )
        save_processing_result(
            connection,
            target,
            result,
        )
        return result

    try:
        extracted_pdf = extract_pdf_text(
            fetched_pdf.content
        )
    except TdnetPdfTextExtractionError as error:
        result = build_extraction_failed_result(
            fetched_pdf,
            error,
        )
        save_processing_result(
            connection,
            target,
            result,
        )
        return result

    policy_analysis = classify_policy_pages(
        extracted_pdf.page_texts
    )
    result = build_completed_result(
        fetched_pdf,
        extracted_pdf,
        policy_analysis,
    )
    save_processing_result(
        connection,
        target,
        result,
    )

    return result


# ============================================================
# 一括処理
# ============================================================

def process_tdnet_policy_pdf_targets(
    targets: Iterable[TdnetPolicyPdfTarget],
) -> dict[str, int]:
    """複数のTDnet方針候補PDFを順番に解析する。"""

    target_list = list(targets)
    summary = {
        "target_count": len(target_list),
        "completed_count": 0,
        "confirmed_count": 0,
        "not_confirmed_count": 0,
        "manual_review_count": 0,
        "fetch_failed_count": 0,
        "text_extraction_failed_count": 0,
    }

    if not target_list:
        print(
            "TDnet PDF本文解析の対象はありません。"
        )
        return summary

    session = create_pdf_http_session()

    try:
        with create_database_connection(
            "store_tdnet_policy_pdf_analyses"
        ) as connection:
            for index, target in enumerate(
                target_list,
                start=1,
            ):
                result = (
                    analyze_and_store_tdnet_policy_pdf(
                        connection,
                        session,
                        target,
                    )
                )

                if (
                    result.analysis_status
                    == ANALYSIS_STATUS_COMPLETED
                ):
                    summary[
                        "completed_count"
                    ] += 1

                    classification_key = (
                        f"{result.policy_classification}"
                        "_count"
                    )

                    if classification_key in summary:
                        summary[
                            classification_key
                        ] += 1

                elif (
                    result.analysis_status
                    == ANALYSIS_STATUS_FETCH_FAILED
                ):
                    summary[
                        "fetch_failed_count"
                    ] += 1

                elif (
                    result.analysis_status
                    == (
                        ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED
                    )
                ):
                    summary[
                        "text_extraction_failed_count"
                    ] += 1

                print(
                    "TDnet PDF本文解析: "
                    f"{index:,}/{len(target_list):,}, "
                    f"開示ID={target.disclosure_id}, "
                    f"状態={result.analysis_status}, "
                    "判定="
                    f"{result.policy_classification or '-'}"
                )

                if index < len(target_list):
                    time.sleep(
                        REQUEST_INTERVAL_SECONDS
                    )

    finally:
        close = getattr(
            session,
            "close",
            None,
        )

        if callable(close):
            close()

    print(
        "TDnet PDF本文解析が完了しました。"
        f"対象: {summary['target_count']:,}, "
        f"完了: {summary['completed_count']:,}, "
        f"confirmed: {summary['confirmed_count']:,}, "
        "not_confirmed: "
        f"{summary['not_confirmed_count']:,}, "
        "manual_review: "
        f"{summary['manual_review_count']:,}, "
        f"取得失敗: {summary['fetch_failed_count']:,}, "
        "本文抽出失敗: "
        f"{summary['text_extraction_failed_count']:,}"
    )

    return summary
