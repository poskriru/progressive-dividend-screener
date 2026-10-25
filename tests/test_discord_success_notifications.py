"""正常時のDiscord通知省略のテスト。"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from analyze_dividend_history import (  # noqa: E402
    notify_diagnosis_summary,
)
from export_progressive_dividend_candidates import (  # noqa: E402
    CandidateChanges,
    notify_discord_candidates,
)
import run_data_health_check  # noqa: E402


class CriteriaStub:
    @staticmethod
    def describe() -> str:
        return "抽出条件"


class CandidateNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environ = patch.dict(
            os.environ,
            {
                "DISCORD_WEBHOOK_URL": (
                    "https://discord.com/api/webhooks/test"
                )
            },
        )
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def test_unchanged_candidates_are_not_notified(
        self,
    ) -> None:
        changes = CandidateChanges(
            comparison_id="compare-1",
            is_first_export=False,
            added_candidates=(),
            removed_candidates=(),
        )

        with patch(
            "export_progressive_dividend_candidates"
            ".send_discord_notification"
        ) as send:
            notify_discord_candidates(
                [],
                CriteriaStub(),
                changes,
                {},
            )

        send.assert_not_called()

    def test_changed_candidates_are_notified(self) -> None:
        changes = CandidateChanges(
            comparison_id="compare-1",
            is_first_export=False,
            added_candidates=(("1111", "テスト銘柄"),),
            removed_candidates=(),
        )

        with patch(
            "export_progressive_dividend_candidates"
            ".send_discord_notification"
        ) as send:
            notify_discord_candidates(
                [],
                CriteriaStub(),
                changes,
                {},
            )

        send.assert_called_once()

    def test_first_export_without_changes_is_notified(
        self,
    ) -> None:
        changes = CandidateChanges(
            comparison_id="compare-1",
            is_first_export=True,
            added_candidates=(),
            removed_candidates=(),
        )

        with patch(
            "export_progressive_dividend_candidates"
            ".send_discord_notification"
        ) as send:
            notify_discord_candidates(
                [],
                CriteriaStub(),
                changes,
                {},
            )

        send.assert_called_once()


class HealthCheckNotificationTests(unittest.TestCase):
    def test_healthy_check_is_not_notified(self) -> None:
        with patch(
            "run_data_health_check.load_health_metrics",
            return_value={},
        ), patch(
            "run_data_health_check.build_health_report",
            return_value=([], False),
        ), patch(
            "run_data_health_check"
            ".send_discord_notification"
        ) as send:
            run_data_health_check.run_data_health_check()

        send.assert_not_called()

    def test_unhealthy_check_is_notified(self) -> None:
        with patch(
            "run_data_health_check.load_health_metrics",
            return_value={},
        ), patch(
            "run_data_health_check.build_health_report",
            return_value=(["警告: 株価データが古い"], True),
        ), patch(
            "run_data_health_check"
            ".send_discord_notification"
        ) as send:
            run_data_health_check.run_data_health_check()

        send.assert_called_once()


class DiagnosisNotificationTests(unittest.TestCase):
    def test_healthy_diagnosis_is_not_notified(
        self,
    ) -> None:
        with patch(
            "analyze_dividend_history"
            ".build_diagnosis_discord_summary",
            return_value=("タイトル", "本文", True),
        ), patch(
            "analyze_dividend_history"
            ".send_discord_notification"
        ) as send:
            notify_diagnosis_summary({}, {}, [], 0)

        send.assert_not_called()

    def test_unhealthy_diagnosis_is_notified(self) -> None:
        with patch(
            "analyze_dividend_history"
            ".build_diagnosis_discord_summary",
            return_value=("タイトル", "本文", False),
        ), patch(
            "analyze_dividend_history"
            ".send_discord_notification"
        ) as send:
            notify_diagnosis_summary({}, {}, [], 0)

        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
