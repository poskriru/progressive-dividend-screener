"""月切替時に不足する権利処理月のバックナンバー取得を検証する。"""

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import update_jpx_current_rights as rights


class BacknumberTests(unittest.TestCase):
    def test_previous_month_is_downloaded_after_page_rollover(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value.content = (
            '<html><select class="backnumber">'
            '<option value="/markets/equities/rights/index.html">2026年10月</option>'
            '<option value="/markets/equities/rights/archives-01.html">2026年9月</option>'
            '</select></html>'
        ).encode("utf-8")
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "latest_trading_date": date(2026, 10, 1),
            "covered_to": date(2026, 8, 31),
        }
        current = SimpleNamespace(coverage_month=date(2026, 10, 1))
        previous = SimpleNamespace(coverage_month=date(2026, 9, 1))
        with patch.object(rights, "create_jpx_http_session", return_value=session), \
             patch.object(rights, "create_database_connection", return_value=connection), \
             patch.object(rights, "download_and_parse_current_rights", side_effect=[current, previous]) as download, \
             patch.object(rights, "save_current_rights") as save:
            self.assertIs(rights.run_jpx_current_rights_update(), current)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args.kwargs["page_url"],
                         "https://www.jpx.co.jp/markets/equities/rights/archives-01.html")
        self.assertEqual(save.call_count, 2)

    def test_archive_url_validation_remains_bounded(self):
        self.assertEqual(rights.validate_jpx_current_rights_page_url(
            "https://www.jpx.co.jp/markets/equities/rights/archives-11.html"
        ), "https://www.jpx.co.jp/markets/equities/rights/archives-11.html")
        with self.assertRaises(RuntimeError):
            rights.validate_jpx_current_rights_page_url(
                "https://www.jpx.co.jp/markets/equities/rights/archives-99.html"
            )
