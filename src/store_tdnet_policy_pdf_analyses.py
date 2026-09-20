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
# 解析対象選択
# ============================================================

def select_targets_requiring_analysis(
    connection,
    targets: Iterable[TdnetPolicyPdfTarget],
    *,
    maximum_attempts: int = 3,
) -> tuple[
    list[TdnetPolicyPdfTarget],
    dict[str, int],
]:
    """
    未処理、解析ロジック更新、再試行可能な対象だけを返す。

    completedかつ同じanalyzer_versionの開示は再取得しない。
    失敗した開示はmaximum_attemptsまで再試行する。
    analyzer_versionが変わった場合は試行回数にかかわらず
    再解析する。
    """

    if maximum_attempts <= 0:
        raise ValueError(
            "maximum_attemptsは1以上で指定してください。"
        )

    target_list = list(targets)
    target_by_id: dict[
        str,
        TdnetPolicyPdfTarget,
    ] = {}

    for target in target_list:
        validate_target(target)
        disclosure_id = (
            target.disclosure_id.strip()
        )

        if disclosure_id in target_by_id:
            raise ValueError(
                "TDnet PDF解析対象の開示IDが重複しています。"
                f"開示ID: {disclosure_id}"
            )

        target_by_id[disclosure_id] = target

    selection_summary = {
        "input_count": len(target_list),
        "selected_count": 0,
        "completed_skipped_count": 0,
        "retry_exhausted_count": 0,
    }

    if not target_list:
        return [], selection_summary

    query = """
        SELECT
            disclosure_id,
            analysis_status,
            analyzer_version,
            fetch_attempt_count
        FROM screener.tdnet_policy_pdf_analyses
        WHERE disclosure_id = ANY(%s)
        ORDER BY disclosure_id;
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                list(target_by_id),
            ),
        )
        stored_by_id = {
            str(row["disclosure_id"]): dict(row)
            for row in cursor.fetchall()
        }

    selected_targets = []

    for target in target_list:
        disclosure_id = (
            target.disclosure_id.strip()
        )
        stored = stored_by_id.get(
            disclosure_id
        )

        if stored is None:
            selected_targets.append(target)
            continue

        stored_version = str(
            stored.get(
                "analyzer_version",
                "",
            )
            or ""
        )
        stored_status = str(
            stored.get(
                "analysis_status",
                "",
            )
            or ""
        )
        attempt_count = int(
            stored.get(
                "fetch_attempt_count",
                0,
            )
            or 0
        )

        if stored_version != ANALYZER_VERSION:
            selected_targets.append(target)
            continue

        if (
            stored_status
            == ANALYSIS_STATUS_COMPLETED
        ):
            selection_summary[
                "completed_skipped_count"
            ] += 1
            continue

        if attempt_count >= maximum_attempts:
            selection_summary[
                "retry_exhausted_count"
            ] += 1
            continue

        selected_targets.append(target)

    selection_summary[
        "selected_count"
    ] = len(selected_targets)

    return (
        selected_targets,
        selection_summary,
    )


# ============================================================
# 集計
# ============================================================

def create_processing_summary(
    target_count: int,
) -> dict[str, int]:
    """一括処理の初期集計を作成する。"""

    return {
        "target_count": target_count,
        "processing_target_count": 0,
        "completed_skipped_count": 0,
        "retry_exhausted_count": 0,
        "completed_count": 0,
        "confirmed_count": 0,
        "not_confirmed_count": 0,
        "manual_review_count": 0,
        "fetch_failed_count": 0,
        "text_extraction_failed_count": 0,
    }


def add_result_to_summary(
    summary: dict[str, int],
    result: TdnetPolicyPdfProcessingResult,
) -> None:
    """1件の処理結果を集計へ追加する。"""

    if (
        result.analysis_status
        == ANALYSIS_STATUS_COMPLETED
    ):
        summary["completed_count"] += 1

        classification_key = (
            f"{result.policy_classification}_count"
        )

        if classification_key in summary:
            summary[classification_key] += 1

        return

    if (
        result.analysis_status
        == ANALYSIS_STATUS_FETCH_FAILED
    ):
        summary["fetch_failed_count"] += 1
        return

    if (
        result.analysis_status
        == ANALYSIS_STATUS_TEXT_EXTRACTION_FAILED
    ):
        summary[
            "text_extraction_failed_count"
        ] += 1


# ============================================================
# 一括処理
# ============================================================

def process_tdnet_policy_pdf_targets(
    targets: Iterable[TdnetPolicyPdfTarget],
    *,
    maximum_attempts: int = 3,
) -> dict[str, int]:
    """
    必要なTDnet方針候補PDFだけを順番に解析する。

    completedかつ現在の解析バージョンと同じ開示は
    再取得しない。
    """

    target_list = list(targets)
    summary = create_processing_summary(
        len(target_list)
    )

    if not target_list:
        print(
            "TDnet PDF本文解析の対象はありません。"
        )
        return summary

    with create_database_connection(
        "store_tdnet_policy_pdf_analyses"
    ) as connection:
        (
            selected_targets,
            selection_summary,
        ) = select_targets_requiring_analysis(
            connection,
            target_list,
            maximum_attempts=maximum_attempts,
        )

        summary["processing_target_count"] = (
            selection_summary["selected_count"]
        )
        summary["completed_skipped_count"] = (
            selection_summary[
                "completed_skipped_count"
            ]
        )
        summary["retry_exhausted_count"] = (
            selection_summary[
                "retry_exhausted_count"
            ]
        )

        if not selected_targets:
            print(
                "TDnet PDF本文解析が必要な対象は"
                "ありません。"
                f"入力: {len(target_list):,}, "
                "解析済み省略: "
                f"{summary['completed_skipped_count']:,}, "
                "再試行上限: "
                f"{summary['retry_exhausted_count']:,}"
            )
            return summary

        session = create_pdf_http_session()

        try:
            for index, target in enumerate(
                selected_targets,
                start=1,
            ):
                result = (
                    analyze_and_store_tdnet_policy_pdf(
                        connection,
                        session,
                        target,
                    )
                )
                add_result_to_summary(
                    summary,
                    result,
                )

                print(
                    "TDnet PDF本文解析: "
                    f"{index:,}/"
                    f"{len(selected_targets):,}, "
                    f"開示ID={target.disclosure_id}, "
                    f"状態={result.analysis_status}, "
                    "判定="
                    f"{result.policy_classification or '-'}"
                )

                if index < len(selected_targets):
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
        f"入力: {summary['target_count']:,}, "
        "処理対象: "
        f"{summary['processing_target_count']:,}, "
        "解析済み省略: "
        f"{summary['completed_skipped_count']:,}, "
        "再試行上限: "
        f"{summary['retry_exhausted_count']:,}, "
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
