"""
PostgreSQLへ保存されたデータの鮮度と整合を確認する。

株価・EDINET書類・年次財務・TDnet開示の最新日付、
Discord候補検索キャッシュの整合、TDnet未完了解析を集計し、
しきい値を超えた項目はDiscordへ警告する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import os
import sys
import traceback
from datetime import date, datetime, timezone
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from update_stock_prices import send_discord_notification


# ============================================================
# 定数
# ============================================================

MAX_PRICE_AGE_DAYS_ENV = (
    "HEALTH_CHECK_MAX_PRICE_AGE_DAYS"
)

MAX_FINANCIAL_AGE_DAYS_ENV = (
    "HEALTH_CHECK_MAX_FINANCIAL_AGE_DAYS"
)

MAX_EDINET_AGE_DAYS_ENV = (
    "HEALTH_CHECK_MAX_EDINET_AGE_DAYS"
)

MAX_TDNET_AGE_DAYS_ENV = (
    "HEALTH_CHECK_MAX_TDNET_AGE_DAYS"
)

DEFAULT_MAX_PRICE_AGE_DAYS = 5
DEFAULT_MAX_FINANCIAL_AGE_DAYS = 400
DEFAULT_MAX_EDINET_AGE_DAYS = 4
DEFAULT_MAX_TDNET_AGE_DAYS = 5

MAX_AGE_DAYS_LIMIT = 3650

WARNING_PREFIX = "警告"
OK_PREFIX = "OK"


# ============================================================
# しきい値
# ============================================================

def get_positive_age_days(
    env_name: str,
    default_days: int,
) -> int:
    """環境変数から日数しきい値を取得する。"""

    raw_value = os.getenv(env_name, "").strip()

    if not raw_value:
        return default_days

    try:
        days = int(raw_value)
    except ValueError:
        raise RuntimeError(
            f"{env_name}は整数で指定してください。"
            f"指定値: {raw_value}"
        ) from None

    if not 1 <= days <= MAX_AGE_DAYS_LIMIT:
        raise RuntimeError(
            f"{env_name}は1〜{MAX_AGE_DAYS_LIMIT}"
            "で指定してください。"
            f"指定値: {raw_value}"
        )

    return days


# ============================================================
# 鮮度判定
# ============================================================

def count_days_since(
    reference_date: Any,
    today: date,
) -> int | None:
    """基準日からの経過日数を返す。日付が無ければNone。"""

    if reference_date is None:
        return None

    if isinstance(reference_date, datetime):
        reference = reference_date.date()
    elif isinstance(reference_date, date):
        reference = reference_date
    else:
        return None

    elapsed_days = (today - reference).days

    if elapsed_days < 0:
        return 0

    return elapsed_days


def evaluate_age_check(
    label: str,
    latest_date: Any,
    today: date,
    max_age_days: int,
) -> str:
    """1項目の鮮度を評価して結果行を返す。"""

    elapsed_days = count_days_since(
        latest_date,
        today,
    )

    if elapsed_days is None:
        return (
            f"{WARNING_PREFIX} {label}: "
            "日付が取得できませんでした。"
        )

    if elapsed_days > max_age_days:
        return (
            f"{WARNING_PREFIX} {label}: "
            f"最新日付から{elapsed_days}日経過"
            f"（しきい値{max_age_days}日）。"
            f"最新日付: {latest_date}"
        )

    return (
        f"{OK_PREFIX} {label}: "
        f"最新日付 {latest_date}"
        f"（{elapsed_days}日経過）"
    )


def evaluate_cache_consistency(
    cache_latest_trading_date: Any,
    latest_trading_date: Any,
) -> str:
    """Discord候補キャッシュと株価の整合を評価する。"""

    if latest_trading_date is None:
        return (
            f"{WARNING_PREFIX} Discordキャッシュ: "
            "株価データが存在しません。"
        )

    if cache_latest_trading_date is None:
        return (
            f"{WARNING_PREFIX} Discordキャッシュ: "
            "キャッシュが空です。"
        )

    if cache_latest_trading_date < latest_trading_date:
        return (
            f"{WARNING_PREFIX} Discordキャッシュ: "
            "株価より古い株価基準日です。"
            f"キャッシュ: {cache_latest_trading_date}, "
            f"株価: {latest_trading_date}"
        )

    return (
        f"{OK_PREFIX} Discordキャッシュ: "
        f"株価基準日 {cache_latest_trading_date}"
    )


def evaluate_unsupported_actions(
    unsupported_action_count: Any,
) -> str:
    """自動補正対象外アクションの件数を評価する。"""

    count = int(unsupported_action_count or 0)

    if count > 0:
        return (
            f"{OK_PREFIX} 自動補正対象外アクション: "
            f"{count:,}件"
            "（ライツイシュー等確認対象シートを確認）"
        )

    return (
        f"{OK_PREFIX} 自動補正対象外アクション: 0件"
    )


def evaluate_tdnet_incomplete_analyses(
    incomplete_count: Any,
) -> str:
    """TDnet PDF解析の未完了件数を評価する。"""

    count = int(incomplete_count or 0)

    if count > 0:
        return (
            f"{WARNING_PREFIX} TDnet未完了解析: "
            f"{count:,}件"
            "（pending/fetch_failed/text_extraction_failed）"
        )

    return (
        f"{OK_PREFIX} TDnet未完了解析: 0件"
    )


# ============================================================
# メトリクス取得
# ============================================================

def load_health_metrics() -> dict[str, Any]:
    """健全性チェック用のメトリクスを取得する。"""

    queries = {
        "active_security_count": (
            """
            SELECT COUNT(*) FROM screener.securities
            WHERE is_active = TRUE
            """
        ),
        "latest_trading_date": (
            """
            SELECT MAX(trading_date)
            FROM screener.daily_prices
            """
        ),
        "price_record_count": (
            """
            SELECT COUNT(*) FROM screener.daily_prices
            """
        ),
        "cache_candidate_count": (
            """
            SELECT COUNT(*)
            FROM screener.discord_candidate_search_cache
            """
        ),
        "cache_latest_trading_date": (
            """
            SELECT MAX(trading_date)
            FROM screener.discord_candidate_search_cache
            """
        ),
        "financial_record_count": (
            """
            SELECT COUNT(*) FROM screener.annual_financials
            """
        ),
        "latest_fiscal_period_end": (
            """
            SELECT MAX(fiscal_period_end)
            FROM screener.annual_financials
            """
        ),
        "edinet_document_count": (
            """
            SELECT COUNT(*) FROM screener.edinet_documents
            """
        ),
        "latest_edinet_submitted_at": (
            """
            SELECT MAX(submitted_at)
            FROM screener.edinet_documents
            """
        ),
        "latest_tdnet_published_date": (
            """
            SELECT MAX(published_date)
            FROM screener.tdnet_policy_pdf_analyses
            """
        ),
        "tdnet_incomplete_analysis_count": (
            """
            SELECT COUNT(*)
            FROM screener.tdnet_policy_pdf_analyses
            WHERE analysis_status <> 'completed'
            """
        ),
        "unsupported_action_count": (
            """
            SELECT COUNT(*)
            FROM screener.corporate_actions AS actions
            INNER JOIN screener.securities AS securities
                ON securities.security_code
                    = actions.security_code
            WHERE securities.is_active = TRUE
              AND actions.ex_right_type
                    IS DISTINCT FROM '1'
              AND actions.ex_right_type
                    IS DISTINCT FROM '2'
              AND actions.adjustment_factor <> 1
            """
        ),
    }

    metrics: dict[str, Any] = {}

    with create_database_connection(
        "data_health_check"
    ) as connection:
        with connection.cursor() as cursor:
            for name, query in queries.items():
                cursor.execute(query)
                row = cursor.fetchone()
                metrics[name] = (
                    row[0] if row else None
                )

    print(
        "健全性チェック用メトリクスを取得しました。"
        f"項目数: {len(metrics)}"
    )

    return metrics


# ============================================================
# レポート作成
# ============================================================

def build_health_report(
    metrics: dict[str, Any],
    today: date,
    *,
    max_price_age_days: int,
    max_financial_age_days: int,
    max_edinet_age_days: int,
    max_tdnet_age_days: int,
) -> tuple[list[str], bool]:
    """メトリクスから結果行と警告有無を返す。"""

    lines = [
        evaluate_age_check(
            "株価",
            metrics.get("latest_trading_date"),
            today,
            max_price_age_days,
        ),
        evaluate_cache_consistency(
            metrics.get("cache_latest_trading_date"),
            metrics.get("latest_trading_date"),
        ),
        evaluate_age_check(
            "EDINET書類",
            metrics.get("latest_edinet_submitted_at"),
            today,
            max_edinet_age_days,
        ),
        evaluate_age_check(
            "TDnet開示",
            metrics.get("latest_tdnet_published_date"),
            today,
            max_tdnet_age_days,
        ),
        evaluate_age_check(
            "年次財務",
            metrics.get("latest_fiscal_period_end"),
            today,
            max_financial_age_days,
        ),
        evaluate_unsupported_actions(
            metrics.get("unsupported_action_count")
        ),
        evaluate_tdnet_incomplete_analyses(
            metrics.get("tdnet_incomplete_analysis_count")
        ),
        (
            f"{OK_PREFIX} 有効銘柄数: "
            f"{int(metrics.get('active_security_count') or 0):,}"
        ),
        (
            f"{OK_PREFIX} 株価レコード: "
            f"{int(metrics.get('price_record_count') or 0):,}"
        ),
        (
            f"{OK_PREFIX} EDINET書類: "
            f"{int(metrics.get('edinet_document_count') or 0):,}"
        ),
        (
            f"{OK_PREFIX} 年次財務レコード: "
            f"{int(metrics.get('financial_record_count') or 0):,}"
        ),
        (
            f"{OK_PREFIX} Discordキャッシュ候補: "
            f"{int(metrics.get('cache_candidate_count') or 0):,}"
        ),
    ]

    has_warnings = any(
        line.startswith(WARNING_PREFIX)
        for line in lines
    )

    return lines, has_warnings


# ============================================================
# 実行処理
# ============================================================

def run_data_health_check() -> None:
    """健全性チェックを実行し、必要ならDiscordへ通知する。"""

    max_price_age_days = get_positive_age_days(
        MAX_PRICE_AGE_DAYS_ENV,
        DEFAULT_MAX_PRICE_AGE_DAYS,
    )
    max_financial_age_days = get_positive_age_days(
        MAX_FINANCIAL_AGE_DAYS_ENV,
        DEFAULT_MAX_FINANCIAL_AGE_DAYS,
    )
    max_edinet_age_days = get_positive_age_days(
        MAX_EDINET_AGE_DAYS_ENV,
        DEFAULT_MAX_EDINET_AGE_DAYS,
    )
    max_tdnet_age_days = get_positive_age_days(
        MAX_TDNET_AGE_DAYS_ENV,
        DEFAULT_MAX_TDNET_AGE_DAYS,
    )

    metrics = load_health_metrics()

    lines, has_warnings = build_health_report(
        metrics,
        datetime.now(timezone.utc).date(),
        max_price_age_days=max_price_age_days,
        max_financial_age_days=max_financial_age_days,
        max_edinet_age_days=max_edinet_age_days,
        max_tdnet_age_days=max_tdnet_age_days,
    )

    for line in lines:
        print(line, flush=True)

    title = (
        "データ整合性チェックで警告を検出しました"
        if has_warnings
        else "データ整合性チェックは正常です"
    )

    send_discord_notification(
        title,
        "\n".join(lines),
        success=not has_warnings,
    )


def main() -> None:
    """健全性チェックを実行する。"""

    run_data_health_check()


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "データ整合性チェックの実行中に"
            "エラーが発生しました。",
            file=sys.stderr,
        )
        traceback.print_exc()
        print(
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)