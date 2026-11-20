"""自己株式数抽出のテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from update_edinet_financials import (  # noqa: E402
    METRIC_DEFINITIONS,
    extract_metric,
)


# ============================================================
# テスト
# ============================================================

def build_fact(
    element_id: str,
    label: str,
    value: str,
    unit_id: str = "shares",
    unit: str = "",
    relative_year: str = "当期末",
    context_id: str = "CurrentYearInstant",
    consolidated: str = "",
) -> dict[str, str]:
    return {
        "要素ID": element_id,
        "項目名": label,
        "コンテキストID": context_id,
        "相対年度": relative_year,
        "連結・個別": consolidated,
        "期間・時点": "時点",
        "ユニットID": unit_id,
        "単位": unit,
        "値": value,
    }


def extract_treasury_shares(
    facts: list[dict[str, str]],
) -> dict | None:
    return extract_metric(
        facts,
        "treasury_shares",
        METRIC_DEFINITIONS["treasury_shares"],
        None,
        "Japan GAAP",
    )


class TreasurySharesDefinitionTests(unittest.TestCase):
    """自己株式数の定義内容を確認する。"""

    def test_definition_requires_unit_match(self) -> None:
        self.assertTrue(
            METRIC_DEFINITIONS["treasury_shares"][
                "require_unit_match"
            ]
        )

    def test_issued_shares_do_not_require_unit_match(
        self,
    ) -> None:
        self.assertFalse(
            METRIC_DEFINITIONS["shares_issued"].get(
                "require_unit_match", False
            )
        )

    def test_share_count_elements_are_registered(
        self,
    ) -> None:
        element_ids = (
            METRIC_DEFINITIONS["treasury_shares"][
                "element_ids"
            ]
        )

        self.assertIn(
            "TotalNumberOfSharesHeldTreasurySharesEtc",
            element_ids,
        )
        self.assertIn(
            "NumberOfSharesHeldInOwnNameTreasurySharesEtc",
            element_ids,
        )
        self.assertIn(
            "NumberOfSharesHeldInOthersNamesTreasurySharesEtc",
            element_ids,
        )


class TreasurySharesExtractionTests(unittest.TestCase):
    """自己株式数の抽出動作を確認する。"""

    def test_share_count_element_is_selected(
        self,
    ) -> None:
        fact = build_fact(
            "jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc",
            "所有株式数の合計（株）、自己株式等",
            "981600",
        )

        result = extract_treasury_shares([fact])

        self.assertEqual(result["value"], 981600)
        self.assertEqual(
            result["element_id"],
            "jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc",
        )

    def test_balance_sheet_yen_fact_is_rejected(
        self,
    ) -> None:
        fact = build_fact(
            "jppfs_cor:TreasuryStock",
            "自己株式",
            "-123456789",
            unit_id="JPY",
            unit="円",
        )

        result = extract_treasury_shares([fact])

        self.assertIsNone(result)

    def test_zero_yen_fact_is_rejected(
        self,
    ) -> None:
        fact = build_fact(
            "jppfs_cor:TreasuryStock",
            "自己株式",
            "0",
            unit_id="JPY",
            unit="円",
        )

        result = extract_treasury_shares([fact])

        self.assertIsNone(result)

    def test_positive_yen_fact_is_rejected_as_share_count(
        self,
    ) -> None:
        fact = build_fact(
            "jppfs_cor:TreasuryStock",
            "自己株式",
            "500000000",
            unit_id="JPY",
            unit="円",
        )

        result = extract_treasury_shares([fact])

        self.assertIsNone(result)

    def test_total_is_preferred_over_components(
        self,
    ) -> None:
        facts = [
            build_fact(
                "jpcorp_cor:TotalNumberOfSharesHeldTreasurySharesEtc".replace(
                    "jpcorp",
                    "jpcrp",
                ),
                "所有株式数の合計（株）、自己株式等",
                "981600",
            ),
            build_fact(
                "jpcrp_cor:NumberOfSharesHeldInOwnNameTreasurySharesEtc",
                "所有株式数の内書（名義書換中）（株）、自己株式等",
                "900000",
            ),
            build_fact(
                "jpcrp_cor:NumberOfSharesHeldInOthersNamesTreasurySharesEtc",
                "所有株式数の内書（名義書換外）（株）、自己株式等",
                "81600",
            ),
        ]

        result = extract_treasury_shares(facts)

        self.assertEqual(result["value"], 981600)

    def test_component_is_used_when_total_missing(
        self,
    ) -> None:
        fact = build_fact(
            "jpcrp_cor:NumberOfSharesHeldInOwnNameTreasurySharesEtc",
            "所有株式数の内書（名義書換中）（株）、自己株式等",
            "900000",
        )

        result = extract_treasury_shares([fact])

        self.assertEqual(result["value"], 900000)

    def test_current_year_is_preferred_over_prior_year(
        self,
    ) -> None:
        current_fact = build_fact(
            "jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc",
            "所有株式数の合計（株）、自己株式等",
            "981600",
        )
        prior_fact = build_fact(
            "jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc",
            "所有株式数の合計（株）、自己株式等",
            "500000",
            relative_year="前期末",
            context_id="PriorYearInstant",
        )

        result = extract_treasury_shares(
            [prior_fact, current_fact]
        )

        self.assertEqual(result["value"], 981600)

    def test_label_fallback_with_unit_id_shares(
        self,
    ) -> None:
        fact = build_fact(
            "jpcrp_cor:UnknownTreasurySharesElement",
            "所有株式数の合計（株）、自己株式等",
            "12345",
        )

        result = extract_treasury_shares([fact])

        self.assertEqual(result["value"], 12345)


class TreasurySharesMigrationClampTests(unittest.TestCase):
    """自己株式比率クランプのマイグレーション内容を確認する。"""

    def read_migration(self, filename: str) -> str:
        return (
            Path(__file__).resolve().parent.parent
            / "database"
            / "migrations"
            / filename
        ).read_text(encoding="utf-8")

    def test_clamp_condition_appears_in_both_metrics(
        self,
    ) -> None:
        sql = self.read_migration(
            "017_clamp_treasury_shares_ratio.sql"
        )

        self.assertEqual(
            sql.count(
                "treasury_shares\n                          <= issued_shares"
            )
            + sql.count(
                "AND treasury_shares <= issued_shares"
            ),
            2,
        )

    def test_ratio_falls_back_to_zero(self) -> None:
        sql = self.read_migration(
            "017_clamp_treasury_shares_ratio.sql"
        )

        self.assertIn(
            "ELSE 0::NUMERIC\n    END AS treasury_shares_ratio_percent",
            sql,
        )

    def test_previous_migrations_are_not_modified(
        self,
    ) -> None:
        sql16 = self.read_migration(
            "016_screener_valuation_efficiency_metrics.sql"
        )

        self.assertIn(
            "AND treasury_shares IS NOT NULL\n        THEN treasury_shares::NUMERIC",
            sql16,
        )


if __name__ == "__main__":
    unittest.main()
