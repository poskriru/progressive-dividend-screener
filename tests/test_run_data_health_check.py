"""データ整合性チェックのテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import types
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


# ============================================================
# テスト対象の読込
# ============================================================

# 純粋関数のテストではDB接続とWebhook送信を使わない。
# database依存だけを差し替えて読み込む。
database_stub = types.ModuleType("database")
database_stub.create_database_connection = None
sys.modules.setdefault("database", database_stub)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from run_data_health_check import (  # noqa: E402
    DEFAULT_MAX_EDINET_AGE_DAYS,
    DEFAULT_MAX_FINANCIAL_AGE_DAYS,
    DEFAULT_MAX_PRICE_AGE_DAYS,
    DEFAULT_MAX_TDNET_AGE_DAYS,
    MAX_PRICE_AGE_DAYS_ENV,
    OK_PREFIX,
    WARNING_PREFIX,
    build_health_report,
    count_days_since,
    evaluate_age_check,
    evaluate_cache_consistency,
    evaluate_tdnet_incomplete_analyses,
    evaluate_unsupported_actions,
    get_positive_age_days,
)


# ============================================================
# しきい値
# ============================================================

class ThresholdTests(unittest.TestCase):
    """環境変数しきい値の取得を確認する。"""

    def test_default_is_used_when_unset(self) -> None:
        with patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            self.assertEqual(
                get_positive_age_days(
                    MAX_PRICE_AGE_DAYS_ENV,
                    DEFAULT_MAX_PRICE_AGE_DAYS,
                ),
                5,
            )

    def test_valid_value_is_parsed(self) -> None:
        with patch.dict(
            "os.environ",
            {MAX_PRICE_AGE_DAYS_ENV: " 7 "},
        ):
            self.assertEqual(
                get_positive_age_days(
                    MAX_PRICE_AGE_DAYS_ENV,
                    DEFAULT_MAX_PRICE_AGE_DAYS,
                ),
                7,
            )

    def test_non_integer_is_rejected(self) -> None:
        with patch.dict(
            "os.environ",
            {MAX_PRICE_AGE_DAYS_ENV: "abc"},
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "整数",
            ):
                get_positive_age_days(
                    MAX_PRICE_AGE_DAYS_ENV,
                    DEFAULT_MAX_PRICE_AGE_DAYS,
                )

    def test_out_of_range_value_is_rejected(self) -> None:
        with patch.dict(
            "os.environ",
            {MAX_PRICE_AGE_DAYS_ENV: "0"},
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "1〜3650",
            ):
                get_positive_age_days(
                    MAX_PRICE_AGE_DAYS_ENV,
                    DEFAULT_MAX_PRICE_AGE_DAYS,
                )


# ============================================================
# 経過日数
# ============================================================

class CountDaysSinceTests(unittest.TestCase):
    """経過日数の計算を確認する。"""

    def test_date_difference_is_counted(self) -> None:
        self.assertEqual(
            count_days_since(
                date(2026, 9, 20),
                date(2026, 9, 24),
            ),
            4,
        )

    def test_datetime_is_converted_to_date(self) -> None:
        submitted_at = datetime(
            2026,
            9,
            23,
            15,
            0,
            tzinfo=timezone.utc,
        )

        self.assertEqual(
            count_days_since(
                submitted_at,
                date(2026, 9, 24),
            ),
            1,
        )

    def test_missing_date_returns_none(self) -> None:
        self.assertIsNone(
            count_days_since(None, date(2026, 9, 24))
        )

    def test_future_date_is_clamped_to_zero(self) -> None:
        self.assertEqual(
            count_days_since(
                date(2026, 9, 25),
                date(2026, 9, 24),
            ),
            0,
        )


# ============================================================
# 鮮度評価
# ============================================================

class AgeCheckTests(unittest.TestCase):
    """鮮度チェックの結果行を確認する。"""

    def test_fresh_date_is_ok(self) -> None:
        line = evaluate_age_check(
            "株価",
            date(2026, 9, 22),
            date(2026, 9, 24),
            5,
        )

        self.assertTrue(line.startswith(OK_PREFIX))
        self.assertIn("2026-09-22", line)

    def test_stale_date_warns(self) -> None:
        line = evaluate_age_check(
            "株価",
            date(2026, 9, 10),
            date(2026, 9, 24),
            5,
        )

        self.assertTrue(
            line.startswith(WARNING_PREFIX)
        )
        self.assertIn("14日経過", line)

    def test_missing_date_warns(self) -> None:
        line = evaluate_age_check(
            "株価",
            None,
            date(2026, 9, 24),
            5,
        )

        self.assertTrue(
            line.startswith(WARNING_PREFIX)
        )
        self.assertIn("取得できませんでした", line)


# ============================================================
# キャッシュ整合
# ============================================================

class CacheConsistencyTests(unittest.TestCase):
    """Discordキャッシュの整合評価を確認する。"""

    def test_same_date_is_ok(self) -> None:
        line = evaluate_cache_consistency(
            date(2026, 9, 22),
            date(2026, 9, 22),
        )

        self.assertTrue(line.startswith(OK_PREFIX))

    def test_older_cache_warns(self) -> None:
        line = evaluate_cache_consistency(
            date(2026, 9, 18),
            date(2026, 9, 22),
        )

        self.assertTrue(
            line.startswith(WARNING_PREFIX)
        )

    def test_empty_cache_warns(self) -> None:
        line = evaluate_cache_consistency(
            None,
            date(2026, 9, 22),
        )

        self.assertTrue(
            line.startswith(WARNING_PREFIX)
        )

    def test_missing_prices_warns(self) -> None:
        line = evaluate_cache_consistency(
            date(2026, 9, 22),
            None,
        )

        self.assertTrue(
            line.startswith(WARNING_PREFIX)
        )


# ============================================================
# 補正対象外アクション
# ============================================================

class UnsupportedActionTests(unittest.TestCase):
    """自動補正対象外アクションの表示を確認する。"""

    def test_zero_count_is_reported(self) -> None:
        line = evaluate_unsupported_actions(0)

        self.assertTrue(line.startswith(OK_PREFIX))
        self.assertIn("0件", line)

    def test_positive_count_is_reported(self) -> None:
        line = evaluate_unsupported_actions(3)

        self.assertTrue(line.startswith(OK_PREFIX))
        self.assertIn("3件", line)


# ============================================================
# TDnet未完了解析
# ============================================================

class TdnetIncompleteAnalysisTests(unittest.TestCase):
    """TDnet未完了解析の表示を確認する。"""

    def test_zero_count_is_ok(self) -> None:
        line = evaluate_tdnet_incomplete_analyses(0)

        self.assertTrue(line.startswith(OK_PREFIX))
        self.assertIn("0件", line)

    def test_positive_count_warns(self) -> None:
        line = evaluate_tdnet_incomplete_analyses(4)

        self.assertTrue(
            line.startswith(WARNING_PREFIX)
        )
        self.assertIn("4件", line)

    def test_missing_count_is_ok(self) -> None:
        line = evaluate_tdnet_incomplete_analyses(
            None
        )

        self.assertTrue(line.startswith(OK_PREFIX))


# ============================================================
# レポート全体
# ============================================================

class BuildReportTests(unittest.TestCase):
    """レポート全体の組み立てを確認する。"""

    def build_metrics(self) -> dict[str, object]:
        return {
            "active_security_count": 3707,
            "latest_trading_date": date(2026, 9, 22),
            "price_record_count": 3693,
            "cache_candidate_count": 1684,
            "cache_latest_trading_date": date(
                2026,
                9,
                22,
            ),
            "financial_record_count": 30305,
            "latest_fiscal_period_end": date(
                2026,
                6,
                30,
            ),
            "edinet_document_count": 43083,
            "latest_edinet_submitted_at": datetime(
                2026,
                9,
                23,
                0,
                0,
                tzinfo=timezone.utc,
            ),
            "unsupported_action_count": 2,
            "latest_tdnet_published_date": date(
                2026,
                9,
                23,
            ),
            "tdnet_incomplete_analysis_count": 0,
        }

    def test_healthy_metrics_have_no_warnings(self) -> None:
        lines, has_warnings = build_health_report(
            self.build_metrics(),
            date(2026, 9, 24),
            max_price_age_days=(
                DEFAULT_MAX_PRICE_AGE_DAYS
            ),
            max_financial_age_days=(
                DEFAULT_MAX_FINANCIAL_AGE_DAYS
            ),
            max_edinet_age_days=(
                DEFAULT_MAX_EDINET_AGE_DAYS
            ),
            max_tdnet_age_days=(
                DEFAULT_MAX_TDNET_AGE_DAYS
            ),
        )

        self.assertFalse(has_warnings)
        self.assertEqual(len(lines), 12)

    def test_stale_price_causes_warning(self) -> None:
        metrics = self.build_metrics()
        metrics["latest_trading_date"] = date(
            2026,
            9,
            10,
        )
        metrics["cache_latest_trading_date"] = date(
            2026,
            9,
            10,
        )

        lines, has_warnings = build_health_report(
            metrics,
            date(2026, 9, 24),
            max_price_age_days=(
                DEFAULT_MAX_PRICE_AGE_DAYS
            ),
            max_financial_age_days=(
                DEFAULT_MAX_FINANCIAL_AGE_DAYS
            ),
            max_edinet_age_days=(
                DEFAULT_MAX_EDINET_AGE_DAYS
            ),
            max_tdnet_age_days=(
                DEFAULT_MAX_TDNET_AGE_DAYS
            ),
        )

        self.assertTrue(has_warnings)
        warning_lines = [
            line
            for line in lines
            if line.startswith(WARNING_PREFIX)
        ]
        self.assertTrue(warning_lines)


if __name__ == "__main__":
    unittest.main()