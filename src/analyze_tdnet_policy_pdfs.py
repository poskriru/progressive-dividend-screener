"""
TDnet PDFから抽出した本文を解析し、
累進配当方針の明示状況を判定する。

このモジュールでは、表題だけではなくPDF本文中の
明示的な方針表現を使用する。

PDF取得、PDF本文抽出、PostgreSQL保存は別の処理として
段階的に接続する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


# ============================================================
# 定数
# ============================================================

ANALYZER_VERSION = "v1"

POLICY_CLASSIFICATION_CONFIRMED = "confirmed"
POLICY_CLASSIFICATION_NOT_CONFIRMED = "not_confirmed"
POLICY_CLASSIFICATION_MANUAL_REVIEW = "manual_review"

MAXIMUM_EVIDENCE_LENGTH = 500

POLICY_TARGET_TERMS = (
    "累進配当",
    "減配を行わない",
    "減配は行わない",
)

CONFIRMED_POLICY_PATTERNS = (
    re.compile(
        r"累進配当(?:方針|政策)?を"
        r"(?:導入|採用|実施|継続)"
    ),
    re.compile(
        r"累進配当を"
        r"(?:基本方針|基本|原則)"
        r"(?:と|に)"
    ),
    re.compile(
        r"(?:配当方針|配当政策|株主還元方針)"
        r"(?:は|として|に).*?累進配当"
    ),
    re.compile(
        r"原則として減配を行わない"
    ),
    re.compile(
        r"原則として減配は行わない"
    ),
)

MANUAL_REVIEW_MARKERS = (
    "検討",
    "目指",
    "めざし",
    "志向",
    "予定",
    "可能性",
    "選択肢",
    "考えて",
    "考慮",
    "将来的",
    "今後",
    "導入しない",
    "採用しない",
    "実施しない",
    "継続しない",
    "廃止",
    "撤回",
    "終了",
    "取りやめ",
    "見直し",
    "変更",
)

SENTENCE_SEPARATOR_PATTERN = re.compile(
    r"(?<=[。！？!?])|[\r\n]+"
)

WHITESPACE_PATTERN = re.compile(r"\s+")


# ============================================================
# データモデル
# ============================================================

@dataclass(frozen=True)
class PolicyEvidence:
    """本文判定の根拠。"""

    page_number: int
    matched_phrase: str
    evidence_text: str


@dataclass(frozen=True)
class PolicyAnalysis:
    """PDF本文の累進配当方針判定結果。"""

    classification: str
    evidence: PolicyEvidence | None
    analyzer_version: str = ANALYZER_VERSION


# ============================================================
# 文字列正規化
# ============================================================

def normalize_text(value: object) -> str:
    """全半角と連続空白を正規化する。"""

    normalized = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )
    return WHITESPACE_PATTERN.sub(
        " ",
        normalized,
    ).strip()


def compact_text(value: object) -> str:
    """
    PDF抽出時に文字間へ空白が入った場合に備え、
    判定用として空白をすべて除去する。
    """

    normalized = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )
    return WHITESPACE_PATTERN.sub(
        "",
        normalized,
    )


def split_page_sentences(page_text: object) -> tuple[str, ...]:
    """ページ本文を判定用の短い文章へ分割する。"""

    normalized = unicodedata.normalize(
        "NFKC",
        str(page_text or ""),
    )
    sentences = []

    for part in SENTENCE_SEPARATOR_PATTERN.split(
        normalized
    ):
        sentence = normalize_text(part)

        if sentence:
            sentences.append(sentence)

    return tuple(sentences)


def truncate_evidence(
    text: str,
    matched_phrase: str,
) -> str:
    """判定語付近の文章を保存上限内へ切り詰める。"""

    normalized = normalize_text(text)

    if len(normalized) <= MAXIMUM_EVIDENCE_LENGTH:
        return normalized

    compact_phrase = compact_text(matched_phrase)
    compact_normalized = compact_text(normalized)
    match_position = compact_normalized.find(
        compact_phrase
    )

    if match_position < 0:
        return normalized[
            :MAXIMUM_EVIDENCE_LENGTH - 1
        ] + "…"

    approximate_position = min(
        match_position,
        len(normalized) - 1,
    )
    half_length = MAXIMUM_EVIDENCE_LENGTH // 2
    start = max(
        0,
        approximate_position - half_length,
    )
    end = min(
        len(normalized),
        start + MAXIMUM_EVIDENCE_LENGTH,
    )
    excerpt = normalized[start:end]

    if start > 0:
        excerpt = "…" + excerpt[1:]

    if end < len(normalized):
        excerpt = excerpt[:-1] + "…"

    return excerpt


# ============================================================
# 方針表現の判定
# ============================================================

def contains_policy_target(
    compact_sentence: str,
) -> bool:
    """累進配当方針に関係する主要語があるか確認する。"""

    return any(
        term in compact_sentence
        for term in POLICY_TARGET_TERMS
    )


def contains_manual_review_marker(
    compact_sentence: str,
) -> bool:
    """将来表現、否定、撤回などの要確認表現を検出する。"""

    return any(
        marker in compact_sentence
        for marker in MANUAL_REVIEW_MARKERS
    )


def find_confirmed_phrase(
    compact_sentence: str,
) -> str:
    """明示的な累進配当方針表現を返す。"""

    for pattern in CONFIRMED_POLICY_PATTERNS:
        match = pattern.search(compact_sentence)

        if match:
            return match.group(0)

    return ""


def build_evidence(
    *,
    page_number: int,
    sentence: str,
    matched_phrase: str,
) -> PolicyEvidence:
    """判定根拠を作成する。"""

    return PolicyEvidence(
        page_number=page_number,
        matched_phrase=matched_phrase,
        evidence_text=truncate_evidence(
            sentence,
            matched_phrase,
        ),
    )


def classify_policy_pages(
    page_texts: Iterable[str],
) -> PolicyAnalysis:
    """
    ページ単位のPDF本文から累進配当方針を判定する。

    判定優先順位:
    1. 明示的な方針表現
    2. 曖昧、将来予定、撤回、否定などの要確認表現
    3. 方針を確認できない

    同じ文章に要確認表現がある場合は、
    明示パターンへ一致してもmanual_reviewとする。
    ただし、別の文章に明確な現行方針がある場合は
    confirmedを優先する。
    """

    confirmed_evidences: list[PolicyEvidence] = []
    manual_review_evidences: list[PolicyEvidence] = []

    for page_number, page_text in enumerate(
        page_texts,
        start=1,
    ):
        for sentence in split_page_sentences(
            page_text
        ):
            compact_sentence = compact_text(sentence)

            if not contains_policy_target(
                compact_sentence
            ):
                continue

            confirmed_phrase = find_confirmed_phrase(
                compact_sentence
            )
            requires_manual_review = (
                contains_manual_review_marker(
                    compact_sentence
                )
            )

            if confirmed_phrase and not requires_manual_review:
                confirmed_evidences.append(
                    build_evidence(
                        page_number=page_number,
                        sentence=sentence,
                        matched_phrase=confirmed_phrase,
                    )
                )
                continue

            matched_phrase = (
                confirmed_phrase
                or next(
                    (
                        term
                        for term in POLICY_TARGET_TERMS
                        if term in compact_sentence
                    ),
                    "累進配当",
                )
            )
            manual_review_evidences.append(
                build_evidence(
                    page_number=page_number,
                    sentence=sentence,
                    matched_phrase=matched_phrase,
                )
            )

    if confirmed_evidences:
        return PolicyAnalysis(
            classification=(
                POLICY_CLASSIFICATION_CONFIRMED
            ),
            evidence=confirmed_evidences[0],
        )

    if manual_review_evidences:
        return PolicyAnalysis(
            classification=(
                POLICY_CLASSIFICATION_MANUAL_REVIEW
            ),
            evidence=manual_review_evidences[0],
        )

    return PolicyAnalysis(
        classification=(
            POLICY_CLASSIFICATION_NOT_CONFIRMED
        ),
        evidence=None,
    )
