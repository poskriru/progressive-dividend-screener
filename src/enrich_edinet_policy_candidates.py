"""
累進配当候補へ最新EDINET有報配当政策本文解析結果を追加する。

TDnet方針候補（enrich_tdnet_policy_candidates）と同じ形式で、
証券コード照合により候補レコードへ付与する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from typing import Any, Iterable, Mapping


# ============================================================
# プロジェクト内モジュール
# ============================================================

from load_latest_edinet_policy_results import (
    LatestEdinetPolicyResult,
)


# ============================================================
# 定数
# ============================================================

EDINET_POLICY_ANALYSIS_RECORD_FIELDS = (
    "edinet_policy_analysis_status",
    "edinet_policy_classification",
    "edinet_policy_matched_phrase",
    "edinet_policy_evidence_text",
    "edinet_policy_evidence_page_number",
    "edinet_policy_analyzer_version",
)


# ============================================================
# 証券コード
# ============================================================

def normalize_candidate_security_code(
    value: Any,
) -> str:
    """候補レコードの証券コードを正規化する。"""

    security_code = str(value or "").strip().upper()

    if not security_code:
        raise RuntimeError(
            "累進配当候補の証券コードが空です。"
        )

    return security_code


# ============================================================
# 解析結果フィールド
# ============================================================

def build_empty_policy_analysis_fields(
) -> dict[str, Any]:
    """解析結果がない場合の空フィールドを作成する。"""

    return {
        field_name: None
        for field_name
        in EDINET_POLICY_ANALYSIS_RECORD_FIELDS
    }


def build_policy_analysis_fields(
    result: LatestEdinetPolicyResult,
) -> dict[str, Any]:
    """最新解析結果を候補レコード用フィールドへ変換する。"""

    return {
        "edinet_policy_analysis_status": (
            result.analysis_status
        ),
        "edinet_policy_classification": (
            result.policy_classification
        ),
        "edinet_policy_matched_phrase": (
            result.matched_phrase
        ),
        "edinet_policy_evidence_text": (
            result.evidence_text
        ),
        "edinet_policy_evidence_page_number": (
            result.evidence_page_number
        ),
        "edinet_policy_analyzer_version": (
            result.analyzer_version
        ),
    }


# ============================================================
# 候補への反映
# ============================================================

def enrich_candidates_with_edinet_policy_results(
    records: Iterable[dict[str, Any]],
    results: Mapping[
        str,
        LatestEdinetPolicyResult,
    ],
) -> list[dict[str, Any]]:
    """
    累進配当候補へ最新有報配当政策解析結果を追加する。

    有報は証券コードごとに原則1件の解析結果しかないため、
    TDnet方針候補と異なりURL照合は行わず、
    最新の解析結果をそのまま反映する。
    """

    normalized_results = {
        normalize_candidate_security_code(
            security_code
        ): result
        for security_code, result in results.items()
    }

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

        if result is not None:
            if result.security_code != security_code:
                raise RuntimeError(
                    "EDINET有報配当政策解析結果の"
                    "証券コードが辞書キーと一致しません。"
                    f"辞書キー: {security_code}, "
                    "解析結果: "
                    f"{result.security_code}"
                )

            enriched_record.update(
                build_policy_analysis_fields(result)
            )

        enriched_records.append(
            enriched_record
        )

    return enriched_records
