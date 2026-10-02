"""JPX日報のHTML/JSONリンク検出を検証する。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from update_stock_prices import find_latest_stock_quotation_pdf


def response(*, html="", data=None):
    result = Mock()
    result.content = html.encode("utf-8")
    result.json.return_value = data
    return result


class QuotationDiscoveryTests(unittest.TestCase):
    def test_legacy_html_uses_latest_link(self):
        session = Mock()
        session.get.return_value = response(html=(
            '<a href="/files/stq_20260828.pdf">PDF</a>'
            '<a href="/files/stq_20260831.pdf">PDF</a>'
        ))
        self.assertEqual(find_latest_stock_quotation_pdf(session), (
            "https://www.jpx.co.jp/files/stq_20260831.pdf", "2026-08-31",
        ))
        session.get.assert_called_once()

    def test_json_month_rollover_falls_back_to_previous_month(self):
        session = Mock()
        session.get.side_effect = [
            response(html="<table></table>"),
            response(data={"TableDatas": [{"Month": "202609"}, {"Month": "202610"}]}),
            response(data={"TableDatas": [{"TradeDate": "20261001", "Stocks": "-"}]}),
            response(data={"TableDatas": [
                {"TradeDate": "20260929", "Stocks": "/files/stq_20260929.pdf"},
                {"TradeDate": "20260930", "Stocks": "/files/stq_20260930.pdf"},
            ]}),
        ]
        self.assertEqual(find_latest_stock_quotation_pdf(session), (
            "https://www.jpx.co.jp/files/stq_20260930.pdf", "2026-09-30",
        ))

    def test_mismatched_json_date_is_rejected(self):
        session = Mock()
        session.get.side_effect = [
            response(),
            response(data={"TableDatas": [{"Month": "202610"}]}),
            response(data={"TableDatas": [
                {"TradeDate": "20261002", "Stocks": "/files/stq_20261001.pdf"},
            ]}),
        ]
        with self.assertRaisesRegex(RuntimeError, "日付とPDF名"):
            find_latest_stock_quotation_pdf(session)
