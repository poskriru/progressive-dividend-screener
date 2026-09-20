"""
TDnet開示とPDF本文解析結果をGoogle Sheetsへ同期する。

既存のTDnet配当開示12列へ本文解析5列を追加し、
累進配当方針候補シートにも同じ解析結果を反映する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from datetime import datetime
from typing import Any, Iterable


# ============================================================
# プロジェクト内モジュール
# ============================================================

from format_tdnet_policy_sheet_results import (
    TDNET_POLICY_ANALYSIS_HEADERS,
    build_policy_analysis_result_counts,
    build_policy_analysis_sheet_values,
)

from load_tdnet_policy_pdf_analyses import (
    TdnetPolicyPdfAnalysisResult,
)

from update_edinet_financials import (
    JST,
    write_sheet,
)


# ============================================================
# 定数
# ============================================================

TDNET_DISCLOSURE_SHEET_NAME = "TDnet配当開示"
PROGRESSIVE_POLICY_SHEET_NAME = "累進配当方針候補"

TDNET_SOURCE_NAME = "TDnet適時開示情報閲覧サービス"

POLICY_CAUTION = (
    "PDF本文の機械判定結果です。"
    "投資判断に使用する前に会社IRの一次資料を"
    "確認してください。"
)

TDNET_DISCLOSURE_BASE_HEADERS = [
    "開示ID",
    "公開日",
    "公開時刻",
    "証券コード",
    "会社名",
    "表題",
    "分類",
    "累進配当方針候補",
    "一致キーワード",
    "PDF URL",
    "上場取引所",
    "データ出典",
]

TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS = [
    *TDNET_DISCLOSURE_BASE_HEADERS,
    *TDNET_POLICY_ANALYSIS_HEADERS,
]

PROGRESSIVE_POLICY_HEADERS_WITH_ANALYSIS = [
    "更新日時",
    "証券コード",
    "会社名",
    "最新開示日",
    "最新開示時刻",
    "最新表題",
    "分類",
    "一致キーワード",
    "PDF URL",
    *TDNET_POLICY_ANALYSIS_HEADERS,
    "判定注記",
]


# ============================================================
# 共通処理
# ============================================================

def get_disclosure_analysis_result(
    disclosure,
    analysis_results: dict[
        str,
        TdnetPolicyPdfAnalysisResult,
    ],
) -> TdnetPolicyPdfAnalysisResult | None:
    """開示IDに対応するPDF本文解析結果を返す。"""

    disclosure_id = str(
        disclosure.disclosure_id
    ).strip()

    result = analysis_results.get(
        disclosure_id
    )

    if (
        result is not None
        and result.disclosure_id != disclosure_id
    ):
        raise RuntimeError(
            "TDnet開示とPDF本文解析結果の"
            "開示IDが一致しません。"
            f"開示ID: {disclosure_id}, "
            f"解析結果: {result.disclosure_id}"
        )

    return result


def sort_disclosures(
    disclosures: Iterable[Any],
) -> list[Any]:
    """TDnet開示を公開日時順へ並べる。"""

    disclosures_by_id: dict[str, Any] = {}

    for disclosure in disclosures:
        disclosure_id = str(
            disclosure.disclosure_id
        ).strip()

        if not disclosure_id:
            raise RuntimeError(
                "Google Sheetsへ同期する"
                "TDnet開示IDが空です。"
            )

        if disclosure_id in disclosures_by_id:
            raise RuntimeError(
                "Google Sheetsへ同期する"
                "TDnet開示IDが重複しています。"
                f"開示ID: {disclosure_id}"
            )

        disclosures_by_id[
            disclosure_id
        ] = disclosure

    return sorted(
        disclosures_by_id.values(),
        key=lambda disclosure: (
            str(disclosure.published_date),
            str(disclosure.published_time),
            str(disclosure.disclosure_id),
        ),
    )


# ============================================================
# TDnet配当開示シート
# ============================================================

def build_tdnet_disclosure_sheet_row(
    disclosure,
    analysis_result: (
        TdnetPolicyPdfAnalysisResult | None
    ),
) -> list[Any]:
    """TDnet開示を本文解析結果付きの行へ変換する。"""

    base_values = [
        disclosure.disclosure_id,
        disclosure.published_date,
        disclosure.published_time,
        disclosure.security_code,
        disclosure.company_name,
        disclosure.title,
        disclosure.category,
        disclosure.is_policy_candidate,
        ", ".join(
            disclosure.matched_keywords
        ),
        disclosure.pdf_url,
        disclosure.exchange,
        TDNET_SOURCE_NAME,
    ]

    analysis_values = (
        build_policy_analysis_sheet_values(
            is_policy_candidate=(
                disclosure.is_policy_candidate
            ),
            analysis_result=analysis_result,
        )
    )

    return [
        *base_values,
        *analysis_values,
    ]


def build_tdnet_disclosure_sheet_rows(
    disclosures: Iterable[Any],
    analysis_results: dict[
        str,
        TdnetPolicyPdfAnalysisResult,
    ],
) -> list[list[Any]]:
    """TDnet配当開示シートの全行を作成する。"""

    rows: list[list[Any]] = []

    for disclosure in sort_disclosures(
        disclosures
    ):
        analysis_result = (
            get_disclosure_analysis_result(
                disclosure,
                analysis_results,
            )
        )
        rows.append(
            build_tdnet_disclosure_sheet_row(
                disclosure,
                analysis_result,
            )
        )

    return rows


# ============================================================
# 累進配当方針候補シート
# ============================================================

def select_latest_policy_disclosures(
    disclosures: Iterable[Any],
) -> dict[str, Any]:
    """証券コードごとの最新方針候補を取得する。"""

    latest_by_security: dict[str, Any] = {}

    for disclosure in sort_disclosures(
        disclosures
    ):
        if not disclosure.is_policy_candidate:
            continue

        security_code = str(
            disclosure.security_code
        ).strip()

        current = latest_by_security.get(
            security_code
        )

        if current is None or (
            str(disclosure.published_date),
            str(disclosure.published_time),
            str(disclosure.disclosure_id),
        ) > (
            str(current.published_date),
            str(current.published_time),
            str(current.disclosure_id),
        ):
            latest_by_security[
                security_code
            ] = disclosure

    return latest_by_security


def build_progressive_policy_sheet_rows(
    disclosures: Iterable[Any],
    analysis_results: dict[
        str,
        TdnetPolicyPdfAnalysisResult,
    ],
    *,
    updated_at: str | None = None,
) -> list[list[Any]]:
    """本文解析結果付きの累進配当方針候補行を作成する。"""

    if updated_at is None:
        updated_at = datetime.now(
            JST
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    latest_by_security = (
        select_latest_policy_disclosures(
            disclosures
        )
    )
    rows: list[list[Any]] = []

    for security_code in sorted(
        latest_by_security
    ):
        disclosure = latest_by_security[
            security_code
        ]
        analysis_result = (
            get_disclosure_analysis_result(
                disclosure,
                analysis_results,
            )
        )
        analysis_values = (
            build_policy_analysis_sheet_values(
                is_policy_candidate=True,
                analysis_result=analysis_result,
            )
        )

        rows.append(
            [
                updated_at,
                disclosure.security_code,
                disclosure.company_name,
                disclosure.published_date,
                disclosure.published_time,
                disclosure.title,
                disclosure.category,
                ", ".join(
                    disclosure.matched_keywords
                ),
                disclosure.pdf_url,
                *analysis_values,
                POLICY_CAUTION,
            ]
        )

    return rows


# ============================================================
# Google Sheets同期
# ============================================================

def sync_tdnet_policy_analysis_sheets(
    sheets_service,
    spreadsheet_id: str,
    disclosures: Iterable[Any],
    analysis_results: dict[
        str,
        TdnetPolicyPdfAnalysisResult,
    ],
) -> dict[str, int]:
    """TDnetの2シートを本文解析結果付きで同期する。"""

    sorted_disclosures = sort_disclosures(
        disclosures
    )
    disclosure_rows = (
        build_tdnet_disclosure_sheet_rows(
            sorted_disclosures,
            analysis_results,
        )
    )
    policy_rows = (
        build_progressive_policy_sheet_rows(
            sorted_disclosures,
            analysis_results,
        )
    )

    write_sheet(
        sheets_service,
        spreadsheet_id,
        TDNET_DISCLOSURE_SHEET_NAME,
        TDNET_DISCLOSURE_HEADERS_WITH_ANALYSIS,
        disclosure_rows,
    )
    write_sheet(
        sheets_service,
        spreadsheet_id,
        PROGRESSIVE_POLICY_SHEET_NAME,
        PROGRESSIVE_POLICY_HEADERS_WITH_ANALYSIS,
        policy_rows,
    )

    result_counts = (
        build_policy_analysis_result_counts(
            analysis_results
        )
    )

    summary = {
        "disclosure_row_count": len(
            disclosure_rows
        ),
        "policy_row_count": len(
            policy_rows
        ),
        **result_counts,
    }

    print(
        "TDnet PDF本文解析結果を"
        "Google Sheetsへ反映しました。"
        "開示履歴: "
        f"{summary['disclosure_row_count']:,}, "
        "方針候補: "
        f"{summary['policy_row_count']:,}, "
        "解析結果: "
        f"{summary['result_count']:,}, "
        "confirmed: "
        f"{summary['confirmed_count']:,}, "
        "manual_review: "
        f"{summary['manual_review_count']:,}"
    )

    return summary
