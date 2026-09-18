"""J-Quants配当補正の向き・境界・未対応種別を検証する。"""

import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

# 純粋関数のテストではDB接続を使わない。psycopg未導入環境でも
# モジュールを読み込めるよう、database依存だけを差し替える。
database_stub = types.ModuleType("database")
database_stub.create_database_connection = None
sys.modules.setdefault("database", database_stub)

from update_jquants_corporate_actions import (  # noqa: E402
    CorporateAction,
    calculate_dividend_adjustment,
    normalize_security_code,
    parse_corporate_action,
    parse_response_page,
)


class DividendAdjustmentTest(unittest.TestCase):
    """現在株式ベース補正を検証する。"""

    def action(
        self,
        effective_date: date,
        factor: str,
        ex_right_type: str = "1",
    ) -> CorporateAction:
        return CorporateAction(
            security_code="1234",
            effective_date=effective_date,
            adjustment_factor=Decimal(factor),
            ex_right_type=ex_right_type,
        )

    def test_two_for_one_split_multiplies_old_dividend_by_half(self) -> None:
        factor, adjusted = calculate_dividend_adjustment(
            Decimal("100"),
            date(2021, 3, 31),
            date(2026, 3, 31),
            [self.action(date(2023, 10, 1), "0.5")],
        )
        self.assertEqual(factor, Decimal("0.5"))
        self.assertEqual(adjusted, Decimal("50.0"))

    def test_one_for_ten_split_multiplies_old_dividend_by_point_one(self) -> None:
        factor, adjusted = calculate_dividend_adjustment(
            Decimal("100"),
            date(2021, 3, 31),
            date(2026, 3, 31),
            [self.action(date(2023, 10, 1), "0.1")],
        )
        self.assertEqual(factor, Decimal("0.1"))
        self.assertEqual(adjusted, Decimal("10.0"))

    def test_reverse_split_uses_factor_greater_than_one(self) -> None:
        factor, adjusted = calculate_dividend_adjustment(
            Decimal("10"),
            date(2021, 3, 31),
            date(2026, 3, 31),
            [self.action(date(2023, 10, 1), "5", "2")],
        )
        self.assertEqual(factor, Decimal("5"))
        self.assertEqual(adjusted, Decimal("50"))

    def test_multiple_actions_are_multiplied(self) -> None:
        factor, adjusted = calculate_dividend_adjustment(
            Decimal("100"),
            date(2021, 3, 31),
            date(2026, 3, 31),
            [
                self.action(date(2022, 10, 1), "0.5"),
                self.action(date(2024, 10, 1), "0.2"),
            ],
        )
        self.assertEqual(factor, Decimal("0.10"))
        self.assertEqual(adjusted, Decimal("10.00"))

    def test_action_on_fiscal_period_end_does_not_apply(self) -> None:
        factor, adjusted = calculate_dividend_adjustment(
            Decimal("100"),
            date(2023, 10, 1),
            date(2026, 3, 31),
            [self.action(date(2023, 10, 1), "0.5")],
        )
        self.assertEqual(factor, Decimal("1"))
        self.assertEqual(adjusted, Decimal("100"))

    def test_action_after_fiscal_period_end_applies(self) -> None:
        factor, _ = calculate_dividend_adjustment(
            Decimal("100"),
            date(2023, 9, 30),
            date(2026, 3, 31),
            [self.action(date(2023, 10, 1), "0.5")],
        )
        self.assertEqual(factor, Decimal("0.5"))

    def test_rights_issue_is_not_automatically_adjusted(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "自動補正できません"):
            calculate_dividend_adjustment(
                Decimal("100"),
                date(2021, 3, 31),
                date(2026, 3, 31),
                [self.action(date(2023, 10, 1), "0.8", "3")],
            )

    def test_no_action_keeps_raw_value(self) -> None:
        factor, adjusted = calculate_dividend_adjustment(
            Decimal("100"),
            date(2021, 3, 31),
            date(2026, 3, 31),
            [],
        )
        self.assertEqual(factor, Decimal("1"))
        self.assertEqual(adjusted, Decimal("100"))


class JQuantsParsingTest(unittest.TestCase):
    """V2応答とコード変換を検証する。"""

    def test_five_digit_code_is_normalized(self) -> None:
        self.assertEqual(normalize_security_code("13320"), "1332")
        self.assertEqual(normalize_security_code("130A0"), "130A")

    def test_plain_record_without_action_is_ignored(self) -> None:
        self.assertIsNone(
            parse_corporate_action(
                {
                    "Date": "2026-09-17",
                    "Code": "13320",
                    "AdjFactor": 1,
                    "ExRT": None,
                }
            )
        )

    def test_response_uses_data_and_pagination_key(self) -> None:
        records, pagination_key = parse_response_page(
            {
                "data": [{"Code": "13320"}],
                "pagination_key": "next-page",
            }
        )
        self.assertEqual(records, [{"Code": "13320"}])
        self.assertEqual(pagination_key, "next-page")

    def test_response_without_data_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "data配列"):
            parse_response_page({"daily_quotes": []})


if __name__ == "__main__":
    unittest.main()
