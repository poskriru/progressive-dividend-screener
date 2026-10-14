"""並び順のSQL適用とSQS Workerへの受け渡しを確認する。"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from candidate_search import parse_candidate_search_request
from discord_candidate_repository import (
    CandidateCriteria,
    load_progressive_dividend_candidates,
)
from discord_candidate_search_worker import (
    CandidateSearchJob,
    process_candidate_search_job,
    validate_search_options,
)


class CandidateSortTests(unittest.TestCase):
    @patch("discord_candidate_repository.create_database_connection")
    def test_sort_is_applied_in_sql_before_limit(self, connect):
        cursor = connect.return_value.__enter__.return_value.cursor.return_value
        cursor = cursor.__enter__.return_value
        cursor.fetchall.return_value = []

        for sort_by, columns in (
            ("yield", ["dividend_yield_percent", "dividend_cagr_5y_adjusted_percent", "roe_percent"]),
            ("cagr", ["dividend_cagr_5y_adjusted_percent", "dividend_yield_percent", "roe_percent"]),
            ("roe", ["roe_percent", "dividend_yield_percent", "dividend_cagr_5y_adjusted_percent"]),
        ):
            with self.subTest(sort_by=sort_by):
                criteria = parse_candidate_search_request(
                    {"sort_by": sort_by, "limit": 5}
                ).to_candidate_criteria(include_overflow=True)
                load_progressive_dividend_candidates(criteria)
                query, parameters = cursor.execute.call_args.args
                order_clause = query.split("ORDER BY", 1)[1]
                expected = ", ".join(
                    f"{column} DESC NULLS LAST" for column in columns
                )
                self.assertEqual(
                    " ".join(order_clause.split()),
                    expected + ", security_code LIMIT %s;",
                )
                self.assertEqual(parameters, (
                    Decimal("3.0"), Decimal("70.0"), Decimal("25.0"),
                    Decimal("3.0"), Decimal("8.0"), True, 6,
                ))

    def test_repository_rejects_untrusted_sort(self):
        with self.assertRaisesRegex(ValueError, "sort_by"):
            CandidateCriteria(
                Decimal("3"), Decimal("70"), Decimal("25"),
                Decimal("3"), Decimal("8"), True, 10,
                sort_by="roe DESC; DROP TABLE x",
            )

    def test_worker_uses_sort_and_returns_label(self):
        options = validate_search_options({"sort_by": "roe", "limit": 5})
        search = Mock(return_value=[])
        edit = Mock()
        job = CandidateSearchJob("123", "456", "test-token", options)
        process_candidate_search_job(
            job, search_candidates=search, edit_response=edit,
        )
        self.assertEqual(search.call_args.args[0].sort_by, "roe")
        self.assertIn("並び順: ROEの高い順", edit.call_args.args[1])

    def test_worker_rejects_invalid_sort_before_search(self):
        search = Mock()
        edit = Mock()
        job = CandidateSearchJob("123", "456", "test-token", {"sort_by": "bad"})
        process_candidate_search_job(
            job, search_candidates=search, edit_response=edit,
        )
        search.assert_not_called()
        self.assertIn("検索条件が不正", edit.call_args.args[1])
