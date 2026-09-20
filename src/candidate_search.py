"""
Discord条件検索で使用する累進配当候補の検索コア。

Discordとの通信処理には依存せず、
検索条件の検証、既存候補検索の呼び出し、
Discordへ返せる文字数制限付きメッセージの作成を担当する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from export_progressive_dividend_candidates import (
    CandidateCriteria,
    enrich_candidate_records_with_latest_tdnet_policy_results,
    load_progressive_dividend_candidates,
)


# ============================================================
# 定数
# ============================================================

DEFAULT_SEARCH_MIN_DIVIDEND_YIELD_PERCENT = Decimal("3.0")
DEFAULT_SEARCH_MAX_PAYOUT_RATIO_PERCENT = Decimal("70.0")
DEFAULT_SEARCH_MAX_PER_RATIO = Decimal("25.0")
DEFAULT_SEARCH_MAX_PBR_RATIO = Decimal("3.0")
DEFAULT_SEARCH_MIN_ROE_PERCENT = Decimal("8.0")
DEFAULT_SEARCH_REQUIRE_POSITIVE_FREE_CASH_FLOW = True
DEFAULT_SEARCH_MAX_RESULTS = 10

MIN_SEARCH_RESULTS = 1
MAX_SEARCH_RESULTS = 20

DEFAULT_DISCORD_MESSAGE_MAX_CHARS = 1900
DISCORD_MESSAGE_HARD_LIMIT = 2000


# ============================================================
# 検索条件
# ============================================================

@dataclass(frozen=True)
class CandidateSearchRequest:
    """Discord条件検索用の検証済み検索条件。"""

    min_dividend_yield_percent: Decimal = (
        DEFAULT_SEARCH_MIN_DIVIDEND_YIELD_PERCENT
    )
    max_payout_ratio_percent: Decimal = (
        DEFAULT_SEARCH_MAX_PAYOUT_RATIO_PERCENT
    )
    max_per_ratio: Decimal = DEFAULT_SEARCH_MAX_PER_RATIO
    max_pbr_ratio: Decimal = DEFAULT_SEARCH_MAX_PBR_RATIO
    min_roe_percent: Decimal = DEFAULT_SEARCH_MIN_ROE_PERCENT
    require_positive_free_cash_flow: bool = (
        DEFAULT_SEARCH_REQUIRE_POSITIVE_FREE_CASH_FLOW
    )
    max_results: int = DEFAULT_SEARCH_MAX_RESULTS

    def __post_init__(self) -> None:
        """直接生成された場合も検索条件を検証する。"""

        decimal_fields = (
            (
                "min_dividend_yield_percent",
                self.min_dividend_yield_percent,
            ),
            (
                "max_payout_ratio_percent",
                self.max_payout_ratio_percent,
            ),
            (
                "max_per_ratio",
                self.max_per_ratio,
            ),
            (
                "max_pbr_ratio",
                self.max_pbr_ratio,
            ),
            (
                "min_roe_percent",
                self.min_roe_percent,
            ),
        )

        for field_name, value in decimal_fields:
            if not isinstance(value, Decimal):
                raise TypeError(
                    f"{field_name}はDecimalで指定してください。"
                )

            if not value.is_finite():
                raise ValueError(
                    f"{field_name}には有限値を指定してください。"
                )

        if self.min_dividend_yield_percent < 0:
            raise ValueError(
                "min_dividend_yield_percentは"
                "0以上で指定してください。"
            )

        if self.max_payout_ratio_percent <= 0:
            raise ValueError(
                "max_payout_ratio_percentは"
                "0より大きい値で指定してください。"
            )

        if self.max_per_ratio <= 0:
            raise ValueError(
                "max_per_ratioは"
                "0より大きい値で指定してください。"
            )

        if self.max_pbr_ratio <= 0:
            raise ValueError(
                "max_pbr_ratioは"
                "0より大きい値で指定してください。"
            )

        if not isinstance(
            self.require_positive_free_cash_flow,
            bool,
        ):
            raise TypeError(
                "require_positive_free_cash_flowは"
                "booleanで指定してください。"
            )

        if (
            isinstance(self.max_results, bool)
            or not isinstance(self.max_results, int)
        ):
            raise TypeError(
                "max_resultsは整数で指定してください。"
            )

        if not (
            MIN_SEARCH_RESULTS
            <= self.max_results
            <= MAX_SEARCH_RESULTS
        ):
            raise ValueError(
                "max_resultsは"
                f"{MIN_SEARCH_RESULTS}〜{MAX_SEARCH_RESULTS}"
                "で指定してください。"
            )

    def to_candidate_criteria(self) -> CandidateCriteria:
        """既存の候補検索条件へ変換する。"""

        return CandidateCriteria(
            min_dividend_yield_percent=(
                self.min_dividend_yield_percent
            ),
            max_payout_ratio_percent=(
                self.max_payout_ratio_percent
            ),
            max_per_ratio=self.max_per_ratio,
            max_pbr_ratio=self.max_pbr_ratio,
            min_roe_percent=self.min_roe_percent,
            require_positive_free_cash_flow=(
                self.require_positive_free_cash_flow
            ),
            max_candidates=self.max_results,
        )


# ============================================================
# 入力値
# ============================================================

def get_search_option(
    options: Mapping[str, Any],
    name: str,
    default: Any,
) -> Any:
    """未指定または空文字の検索条件へ初期値を適用する。"""

    if name not in options:
        return default

    value = options[name]

    if value is None:
        return default

    if isinstance(value, str) and not value.strip():
        return default

    return value


def parse_decimal_search_option(
    option_name: str,
    raw_value: Any,
    *,
    minimum: Decimal | None = None,
    minimum_inclusive: bool = True,
) -> Decimal:
    """検索条件の数値を有限なDecimalへ変換する。"""

    if isinstance(raw_value, bool):
        raise ValueError(
            f"{option_name}は数値で指定してください。"
        )

    value_text = str(raw_value).strip()

    try:
        value = Decimal(value_text)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(
            f"{option_name}は数値で指定してください。"
            f"指定値: {value_text}"
        ) from error

    if not value.is_finite():
        raise ValueError(
            f"{option_name}には有限値を指定してください。"
            f"指定値: {value_text}"
        )

    if minimum is not None:
        below_minimum = value < minimum
        equal_to_exclusive_minimum = (
            not minimum_inclusive
            and value == minimum
        )

        if below_minimum or equal_to_exclusive_minimum:
            operator = ">=" if minimum_inclusive else ">"
            raise ValueError(
                f"{option_name}は{operator}{minimum}"
                "で指定してください。"
                f"指定値: {value_text}"
            )

    return value


def parse_boolean_search_option(
    option_name: str,
    raw_value: Any,
) -> bool:
    """検索条件のboolean値を検証する。"""

    if isinstance(raw_value, bool):
        return raw_value

    if isinstance(raw_value, str):
        normalized_value = raw_value.strip().lower()

        if normalized_value == "true":
            return True

        if normalized_value == "false":
            return False

    raise ValueError(
        f"{option_name}はtrueまたはfalseで指定してください。"
        f"指定値: {raw_value}"
    )


def parse_search_result_limit(
    raw_value: Any,
) -> int:
    """検索結果件数を1〜20の整数へ変換する。"""

    if isinstance(raw_value, bool):
        raise ValueError(
            "limitは整数で指定してください。"
        )

    value_text = str(raw_value).strip()

    try:
        value = int(value_text)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "limitは整数で指定してください。"
            f"指定値: {value_text}"
        ) from error

    if not MIN_SEARCH_RESULTS <= value <= MAX_SEARCH_RESULTS:
        raise ValueError(
            "limitは"
            f"{MIN_SEARCH_RESULTS}〜{MAX_SEARCH_RESULTS}"
            "で指定してください。"
            f"指定値: {value}"
        )

    return value


def parse_candidate_search_request(
    options: Mapping[str, Any] | None = None,
) -> CandidateSearchRequest:
    """Discordコマンド相当の入力を検証して検索条件を作成する。"""

    if options is None:
        options = {}

    if not isinstance(options, Mapping):
        raise TypeError(
            "検索条件はMappingで指定してください。"
        )

    min_yield = get_search_option(
        options,
        "min_yield",
        DEFAULT_SEARCH_MIN_DIVIDEND_YIELD_PERCENT,
    )
    max_payout = get_search_option(
        options,
        "max_payout",
        DEFAULT_SEARCH_MAX_PAYOUT_RATIO_PERCENT,
    )
    max_per = get_search_option(
        options,
        "max_per",
        DEFAULT_SEARCH_MAX_PER_RATIO,
    )
    max_pbr = get_search_option(
        options,
        "max_pbr",
        DEFAULT_SEARCH_MAX_PBR_RATIO,
    )
    min_roe = get_search_option(
        options,
        "min_roe",
        DEFAULT_SEARCH_MIN_ROE_PERCENT,
    )
    positive_fcf = get_search_option(
        options,
        "positive_fcf",
        DEFAULT_SEARCH_REQUIRE_POSITIVE_FREE_CASH_FLOW,
    )
    limit = get_search_option(
        options,
        "limit",
        DEFAULT_SEARCH_MAX_RESULTS,
    )

    return CandidateSearchRequest(
        min_dividend_yield_percent=(
            parse_decimal_search_option(
                "min_yield",
                min_yield,
                minimum=Decimal("0"),
            )
        ),
        max_payout_ratio_percent=(
            parse_decimal_search_option(
                "max_payout",
                max_payout,
                minimum=Decimal("0"),
                minimum_inclusive=False,
            )
        ),
        max_per_ratio=parse_decimal_search_option(
            "max_per",
            max_per,
            minimum=Decimal("0"),
            minimum_inclusive=False,
        ),
        max_pbr_ratio=parse_decimal_search_option(
            "max_pbr",
            max_pbr,
            minimum=Decimal("0"),
            minimum_inclusive=False,
        ),
        min_roe_percent=parse_decimal_search_option(
            "min_roe",
            min_roe,
        ),
        require_positive_free_cash_flow=(
            parse_boolean_search_option(
                "positive_fcf",
                positive_fcf,
            )
        ),
        max_results=parse_search_result_limit(
            limit
        ),
    )


# ============================================================
# 候補検索
# ============================================================

def search_progressive_dividend_candidates(
    request: CandidateSearchRequest,
) -> list[dict[str, Any]]:
    """検証済み条件で候補と最新TDnet本文判定を取得する。"""

    if not isinstance(request, CandidateSearchRequest):
        raise TypeError(
            "requestはCandidateSearchRequestで"
            "指定してください。"
        )

    records = load_progressive_dividend_candidates(
        request.to_candidate_criteria()
    )

    return (
        enrich_candidate_records_with_latest_tdnet_policy_results(
            records
        )
    )


# ============================================================
# Discordメッセージ
# ============================================================

def normalize_inline_text(
    value: Any,
    *,
    max_length: int = 80,
) -> str:
    """改行と連続空白を除去し、Discordの1行表示へ正規化する。"""

    normalized_value = " ".join(
        str(value or "").split()
    )

    if len(normalized_value) <= max_length:
        return normalized_value

    return normalized_value[: max_length - 1] + "…"


def format_search_decimal(
    value: Any,
    *,
    suffix: str = "",
) -> str:
    """検索結果の数値を小数第2位まで表示する。"""

    if value is None or isinstance(value, bool):
        return "-"

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "-"

    if not number.is_finite():
        return "-"

    return f"{number:.2f}{suffix}"


def build_candidate_search_condition_line(
    request: CandidateSearchRequest,
) -> str:
    """検索結果へ表示する条件行を作成する。"""

    free_cash_flow_condition = (
        "プラス"
        if request.require_positive_free_cash_flow
        else "指定なし"
    )

    return (
        "条件: "
        f"配当利回り{request.min_dividend_yield_percent}%以上"
        " / "
        f"配当性向{request.max_payout_ratio_percent}%以下"
        " / "
        f"PER{request.max_per_ratio}倍以下"
        " / "
        f"PBR{request.max_pbr_ratio}倍以下"
        " / "
        f"ROE{request.min_roe_percent}%以上"
        " / "
        f"フリーCF{free_cash_flow_condition}"
        " / "
        f"最大{request.max_results}件"
    )


def build_candidate_search_result_line(
    rank: int,
    record: Mapping[str, Any],
) -> str:
    """候補1件をDiscord表示用の1行へ変換する。"""

    security_code = normalize_inline_text(
        record.get("security_code"),
        max_length=16,
    )
    company_name = normalize_inline_text(
        record.get("company_name"),
        max_length=80,
    )

    decision_label = (
        "adjusted"
        if record.get(
            "is_adjustment_coverage_complete"
        ) is True
        else "raw"
    )

    markers = [decision_label]

    if (
        record.get("tdnet_policy_classification")
        == "confirmed"
    ):
        markers.append("TDnet confirmed")

    marker_text = " ".join(
        f"[{marker}]"
        for marker in markers
    )

    dividend_yield = format_search_decimal(
        record.get("dividend_yield_percent"),
        suffix="%",
    )
    per_ratio = format_search_decimal(
        record.get("per_ratio")
    )
    pbr_ratio = format_search_decimal(
        record.get("pbr_ratio")
    )
    roe_percent = format_search_decimal(
        record.get("roe_percent"),
        suffix="%",
    )

    return (
        f"{rank}. `{security_code}` {company_name} "
        f"{marker_text} — "
        f"利回り {dividend_yield}"
        f" / PER {per_ratio}"
        f" / PBR {pbr_ratio}"
        f" / ROE {roe_percent}"
    )


def build_candidate_search_message(
    records: Sequence[Mapping[str, Any]],
    request: CandidateSearchRequest,
    *,
    max_chars: int = DEFAULT_DISCORD_MESSAGE_MAX_CHARS,
) -> str:
    """検索結果をDiscordの文字数制限内で整形する。"""

    if not isinstance(request, CandidateSearchRequest):
        raise TypeError(
            "requestはCandidateSearchRequestで"
            "指定してください。"
        )

    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
    ):
        raise TypeError(
            "max_charsは整数で指定してください。"
        )

    if not 100 <= max_chars <= DISCORD_MESSAGE_HARD_LIMIT:
        raise ValueError(
            "max_charsは100〜2000で指定してください。"
        )

    lines = [
        "累進配当候補検索",
        build_candidate_search_condition_line(
            request
        ),
        "",
    ]

    if not records:
        lines.append(
            "条件に一致する銘柄はありません。"
        )
        return "\n".join(lines)

    total_records = len(records)

    for rank, record in enumerate(
        records,
        start=1,
    ):
        result_line = build_candidate_search_result_line(
            rank,
            record,
        )
        remaining_after_add = total_records - rank

        candidate_lines = [
            *lines,
            result_line,
        ]

        if remaining_after_add > 0:
            candidate_lines.append(
                f"… 残り{remaining_after_add}件"
                "は省略される場合があります。"
            )

        if (
            len("\n".join(candidate_lines))
            <= max_chars
        ):
            lines.append(result_line)
            continue

        omitted_count = total_records - rank + 1
        omitted_line = (
            f"… 残り{omitted_count}件を省略しました。"
        )

        if (
            len("\n".join([*lines, omitted_line]))
            <= max_chars
        ):
            lines.append(omitted_line)

        break

    message = "\n".join(lines)

    if len(message) > max_chars:
        raise RuntimeError(
            "Discord検索結果が文字数上限を超えました。"
        )

    return message
