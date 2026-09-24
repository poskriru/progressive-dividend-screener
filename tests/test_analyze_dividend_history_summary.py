"""年間配当履歴診断のDiscordサマリー作成テスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import types
import unittest


# ============================================================
# テスト対象の読込
# ============================================================

# 純粋関数のテストではDB接続とWebhook送信を使わない。
# database依存だけを差し替えて読み込む。
database_stub = types.ModuleType("database")
database_stub.create_database_connection = None
database_stub.verify_required_tables = None
sys.modules.setdefault("database", database_stub)

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from analyze_dividend_history import (  # noqa: E402
    build_diagnosis_discord_summary,
)


# ============================================================
# テストデータ
# ============================================================

def build_summary(
    **overrides,
) -> dict[str, object]:
    summary = {
        "annual_financial_count": 30305,
        "dividend_record_count": 28000,
        "zero_dividend_count": 1200,
        "negative_dividend_count": 0,
    }
    summary.update(overrides)
    return summary


def build_duplicate_summary(
    **overrides,
) -> dict[str, object]:
    duplicate_summary = {
        "duplicate_period_count": 0,
        "duplicate_record_count": 0,
        "affected_security_count": 0,
    }
    duplicate_summary.update(overrides)
    return duplicate_summary


# ============================================================
# テスト
# ============================================================

class DiagnosisSummaryTest(unittest.TestCase):
    """診断サマリーの作成を確認する。"""

    def test_healthy_data_succeeds(self) -> None:
        title, description, success = (
            build_diagnosis_discord_summary(
                build_summary(),
                build_duplicate_summary(),
                [],
                1684,
            )
        )

        self.assertEqual(
            title,
            "配当履歴診断は正常です",
        )
        self.assertTrue(success)
        self.assertIn(
            "年次財務レコード: 30,305件",
            description,
        )
        self.assertIn(
            "5期累進配当候補: 1,684件"
            "（出力上限50件）",
            description,
        )

    def test_negative_dividend_warns(self) -> None:
        title, _, success = (
            build_diagnosis_discord_summary(
                build_summary(
                    negative_dividend_count=2,
                ),
                build_duplicate_summary(),
                [],
                1684,
            )
        )

        self.assertEqual(
            title,
            "配当履歴診断で問題を検出しました",
        )
        self.assertFalse(success)

    def test_duplicate_periods_warn(self) -> None:
        title, description, success = (
            build_diagnosis_discord_summary(
                build_summary(),
                build_duplicate_summary(
                    duplicate_period_count=3,
                    affected_security_count=2,
                ),
                [],
                1684,
            )
        )

        self.assertEqual(
            title,
            "配当履歴診断で問題を検出しました",
        )
        self.assertFalse(success)
        self.assertIn(
            "同一決算期の重複: 3件（2銘柄）",
            description,
        )

    def test_coverage_distribution_is_appended(
        self,
    ) -> None:
        history_coverage = [
            {
                "dividend_period_count": 9,
                "security_count": 2587,
            },
            {
                "dividend_period_count": 11,
                "security_count": 12,
            },
        ]

        _, description, _ = (
            build_diagnosis_discord_summary(
                build_summary(),
                build_duplicate_summary(),
                history_coverage,
                1684,
            )
        )

        self.assertIn(
            "配当履歴年数分布: 9期:2,587銘柄, "
            "11期:12銘柄",
            description,
        )

    def test_missing_counts_are_treated_as_zero(
        self,
    ) -> None:
        title, description, success = (
            build_diagnosis_discord_summary(
                {},
                {},
                [],
                0,
            )
        )

        self.assertEqual(
            title,
            "配当履歴診断は正常です",
        )
        self.assertTrue(success)
        self.assertIn(
            "年次財務レコード: 0件",
            description,
        )


if __name__ == "__main__":
    unittest.main()