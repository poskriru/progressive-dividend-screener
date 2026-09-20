"""
累進配当候補レコードへ、
証券コードごとの最新TDnet PDF本文解析結果を追加する。

既存の候補レコードは直接変更せず、
解析結果を追加した新しいdictを返す。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from collections.abc import Mapping
from typing import Any, Iterable


# ============================================================
# プロジェクト内モジュール
# ============================================================

from load_latest_tdnet_policy_results import (
    LatestTdnetPolicyResult,
    SECURITY_CODE_PATTERN,
)


# ============================================================
# 定数
# ============================================================

TDNET_POLICY_ANALYSIS_RECORD_FIELDS = (
    "tdnet_policy_analysis_status",
    "tdnet_policy_classification",
    "tdnet_policy_matched_phrase",
    "tdnet_policy_evidence_text",
    "tdnet_policy_evidence_page_number",
    "tdnet_policy_analyzer_version",
)


# ============================================================
# 証券コード
# ============================================================

def normalize_candidate_security_code(
    value: Any,
) -> str:
    """候補レコードの証券コードを正規化する。"""

    security_code = str(
        value or ""
    ).strip().upper()

    if not SECURITY_CODE_PATTERN.fullmatch(
        security_code
    ):
        raise RuntimeError(
            "累進配当候補の証券コードが不正です。"
            f"証券コード: {security_code}"
        )

    return security_code


# ============================================================
# 解析結果
# ============================================================

def build_empty_policy_analysis_fields(
) -> dict[str, Any]:
    """解析結果がない場合の空フィールドを作成する。"""

    return {
        field_name: None
        for field_name
        in TDNET_POLICY_ANALYSIS_RECORD_FIELDS
    }


def build_policy_analysis_fields(
    result: LatestTdnetPolicyResult,
) -> dict[str, Any]:
    """最新解析結果を候補レコード用フィールドへ変換する。"""

    return {
        "tdnet_policy_analysis_status": (
            result.analysis_status
        ),
        "tdnet_policy_classification": (
            result.policy_classification
        ),
        "tdnet_policy_matched_phrase": (
            result.matched_phrase
        ),
        "tdnet_policy_evidence_text": (
            result.evidence_text
        ),
        "tdnet_policy_evidence_page_number": (
            result.evidence_page_number
        ),
        "tdnet_policy_analyzer_version": (
            result.analyzer_version
        ),
    }


def normalize_policy_results(
    results: Mapping[
        str,
        LatestTdnetPolicyResult,
    ],
) -> dict[str, LatestTdnetPolicyResult]:
    """解析結果の辞書キーと証券コードを検証する。"""

    normalized_results: dict[
        str,
        LatestTdnetPolicyResult,
    ] = {}

    for raw_security_code, result in results.items():
        security_code = (
            normalize_candidate_security_code(
                raw_security_code
            )
        )

        if not isinstance(
            result,
            LatestTdnetPolicyResult,
        ):
            raise TypeError(
                "TDnet PDF本文解析結果の型が不正です。"
                f"証券コード: {security_code}"
            )

        if result.security_code != security_code:
            raise RuntimeError(
                "TDnet PDF本文解析結果の"
                "証券コードが辞書キーと一致しません。"
                f"辞書キー: {security_code}, "
                f"解析結果: {result.security_code}"
            )

        if security_code in normalized_results:
            raise RuntimeError(
                "TDnet PDF本文解析結果の"
                "証券コードが重複しています。"
                f"証券コード: {security_code}"
            )

        normalized_results[security_code] = result

    return normalized_results


# ============================================================
# 候補レコード結合
# ============================================================

def enrich_candidates_with_tdnet_policy_results(
    records: Iterable[dict[str, Any]],
    results: Mapping[
        str,
        LatestTdnetPolicyResult,
    ],
) -> list[dict[str, Any]]:
    """
    累進配当候補へ最新TDnet PDF本文解析結果を追加する。

    候補側にTDnet方針PDF URLがある場合は、
    解析結果のPDF URLと一致するときだけ本文判定を追加する。
    URLが一致しない場合は、異なる開示の判定を誤表示しないため
    本文判定欄を空欄にする。

    候補側にTDnet方針PDF URLがない場合は、
    解析結果に保存された開示情報を候補へ追加する。
    """

    normalized_results = normalize_policy_results(
        results
    )
    enriched_records: list[dict[str, Any]] = []
    processed_codes: set[str] = set()

    for record in records:
        security_code = (
            normalize_candidate_security_code(
                record.get("security_code")
            )
        )

        if security_code in processed_codes:
            raise RuntimeError(
                "累進配当候補の証券コードが"
                "重複しています。"
                f"証券コード: {security_code}"
            )

        processed_codes.add(security_code)

        enriched_record = dict(record)
        enriched_record.update(
            build_empty_policy_analysis_fields()
        )

        result = normalized_results.get(
            security_code
        )

        if result is None:
            enriched_records.append(
                enriched_record
            )
            continue

        existing_pdf_url = str(
            record.get("tdnet_policy_url") or ""
        ).strip()

        if (
            existing_pdf_url
            and existing_pdf_url != result.pdf_url
        ):
            enriched_records.append(
                enriched_record
            )
            continue

        if not existing_pdf_url:
            enriched_record[
                "tdnet_policy_candidate"
            ] = True
            enriched_record[
                "tdnet_policy_date"
            ] = result.published_date
            enriched_record[
                "tdnet_policy_title"
            ] = result.title
            enriched_record[
                "tdnet_policy_url"
            ] = result.pdf_url

        enriched_record.update(
            build_policy_analysis_fields(
                result
            )
        )

        enriched_records.append(
            enriched_record
        )

    return enriched_records
