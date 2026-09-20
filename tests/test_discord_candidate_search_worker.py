"""Discord累進配当候補検索Workerのテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch


# ============================================================
# 外部ライブラリ
# ============================================================

import requests


# ============================================================
# テスト対象の読込
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from candidate_search import (  # noqa: E402
    CandidateSearchRequest,
)
from discord_candidate_search_worker import (  # noqa: E402
    DISCORD_HTTP_TIMEOUT_SECONDS,
    INVALID_SEARCH_MESSAGE,
    CandidateSearchJob,
    PermanentWorkerError,
    RetryableWorkerError,
    build_original_response_url,
    edit_original_interaction_response,
    lambda_handler,
    parse_search_job_body,
    process_candidate_search_job,
    validate_search_options,
)


# ============================================================
# テスト補助
# ============================================================

def build_job_payload(
    *,
    options=None,
):
    """有効なSQS検索要求を作成する。"""

    if options is None:
        options = {
            "min_yield": 4.0,
            "positive_fcf": True,
            "limit": 5,
        }

    return {
        "application_id": "987654321098765432",
        "interaction_id": "123456789012345678",
        "interaction_token": "interaction-token",
        "options": options,
    }


def build_job(
    *,
    options=None,
):
    """有効な検索Jobを作成する。"""

    return CandidateSearchJob(
        application_id="987654321098765432",
        interaction_id="123456789012345678",
        interaction_token="interaction-token",
        options=(
            {
                "min_yield": 4.0,
                "positive_fcf": True,
                "limit": 5,
            }
            if options is None
            else options
        ),
    )


def build_sqs_record(
    message_id,
    payload,
):
    """SQSレコードを作成する。"""

    return {
        "messageId": message_id,
        "body": json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


# ============================================================
# SQSメッセージ検証
# ============================================================

class CandidateSearchJobTests(unittest.TestCase):
    """SQS検索要求の検証を確認する。"""

    def test_valid_job_is_parsed(self) -> None:
        payload = build_job_payload()

        job = parse_search_job_body(
            json.dumps(payload)
        )

        self.assertEqual(
            job.application_id,
            "987654321098765432",
        )
        self.assertEqual(
            job.interaction_id,
            "123456789012345678",
        )
        self.assertEqual(
            job.interaction_token,
            "interaction-token",
        )
        self.assertEqual(
            job.options,
            {
                "min_yield": 4.0,
                "positive_fcf": True,
                "limit": 5,
            },
        )

    def test_empty_options_are_allowed(self) -> None:
        job = parse_search_job_body(
            json.dumps(
                build_job_payload(
                    options={},
                )
            )
        )

        self.assertEqual(
            job.options,
            {},
        )

    def test_invalid_json_is_permanent_error(
        self,
    ) -> None:
        with self.assertRaises(
            PermanentWorkerError
        ):
            parse_search_job_body(
                "{invalid-json"
            )

    def test_missing_required_field_is_rejected(
        self,
    ) -> None:
        for field_name in (
            "application_id",
            "interaction_id",
            "interaction_token",
            "options",
        ):
            with self.subTest(
                field_name=field_name
            ):
                payload = build_job_payload()
                del payload[field_name]

                with self.assertRaises(
                    PermanentWorkerError
                ):
                    parse_search_job_body(
                        json.dumps(payload)
                    )

    def test_non_numeric_discord_ids_are_rejected(
        self,
    ) -> None:
        payload = build_job_payload()
        payload["application_id"] = "not-a-number"

        with self.assertRaisesRegex(
            PermanentWorkerError,
            "数字",
        ):
            parse_search_job_body(
                json.dumps(payload)
            )

    def test_unknown_option_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            PermanentWorkerError,
            "未対応",
        ):
            validate_search_options(
                {
                    "unknown": "value",
                }
            )

    def test_options_are_copied(self) -> None:
        source_options = {
            "limit": 5,
        }

        validated_options = validate_search_options(
            source_options
        )
        source_options["limit"] = 10

        self.assertEqual(
            validated_options,
            {
                "limit": 5,
            },
        )


# ============================================================
# Discord応答
# ============================================================

class DiscordResponseTests(unittest.TestCase):
    """Discord Deferred Responseの更新を確認する。"""

    def setUp(self) -> None:
        self.job = build_job()

    def test_original_response_url_is_built(
        self,
    ) -> None:
        url = build_original_response_url(
            self.job
        )

        self.assertEqual(
            url,
            (
                "https://discord.com/api/v10"
                "/webhooks/987654321098765432"
                "/interaction-token"
                "/messages/@original"
            ),
        )

    def test_successful_response_is_accepted(
        self,
    ) -> None:
        response = Mock(
            status_code=200
        )
        http_patch = Mock(
            return_value=response
        )

        edit_original_interaction_response(
            self.job,
            "検索結果",
            http_patch=http_patch,
        )

        http_patch.assert_called_once_with(
            build_original_response_url(
                self.job
            ),
            json={
                "content": "検索結果",
                "allowed_mentions": {
                    "parse": [],
                },
            },
            timeout=DISCORD_HTTP_TIMEOUT_SECONDS,
        )

    def test_no_content_response_is_accepted(
        self,
    ) -> None:
        response = Mock(
            status_code=204
        )

        edit_original_interaction_response(
            self.job,
            "検索結果",
            http_patch=Mock(
                return_value=response
            ),
        )

    def test_rate_limit_is_retryable(
        self,
    ) -> None:
        response = Mock(
            status_code=429
        )

        with self.assertRaises(
            RetryableWorkerError
        ):
            edit_original_interaction_response(
                self.job,
                "検索結果",
                http_patch=Mock(
                    return_value=response
                ),
            )

    def test_server_error_is_retryable(
        self,
    ) -> None:
        response = Mock(
            status_code=503
        )

        with self.assertRaises(
            RetryableWorkerError
        ):
            edit_original_interaction_response(
                self.job,
                "検索結果",
                http_patch=Mock(
                    return_value=response
                ),
            )

    def test_client_error_is_permanent(
        self,
    ) -> None:
        response = Mock(
            status_code=404
        )

        with self.assertRaises(
            PermanentWorkerError
        ):
            edit_original_interaction_response(
                self.job,
                "検索結果",
                http_patch=Mock(
                    return_value=response
                ),
            )

    def test_network_error_is_retryable(
        self,
    ) -> None:
        http_patch = Mock(
            side_effect=requests.Timeout(
                "timeout"
            )
        )

        with self.assertRaises(
            RetryableWorkerError
        ):
            edit_original_interaction_response(
                self.job,
                "検索結果",
                http_patch=http_patch,
            )

    def test_empty_message_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            PermanentWorkerError
        ):
            edit_original_interaction_response(
                self.job,
                "",
                http_patch=Mock(),
            )

    def test_message_over_discord_limit_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            PermanentWorkerError
        ):
            edit_original_interaction_response(
                self.job,
                "x" * 2001,
                http_patch=Mock(),
            )


# ============================================================
# 候補検索
# ============================================================

class CandidateSearchProcessingTests(
    unittest.TestCase
):
    """検索コアとの接続を確認する。"""

    def test_search_result_updates_deferred_response(
        self,
    ) -> None:
        job = build_job()
        request = CandidateSearchRequest(
            min_dividend_yield_percent=Decimal("4"),
            max_payout_ratio_percent=Decimal("70"),
            max_per_ratio=Decimal("25"),
            max_pbr_ratio=Decimal("3"),
            min_roe_percent=Decimal("8"),
            require_positive_free_cash_flow=True,
            max_results=5,
        )
        records = [
            {
                "security_code": "8057",
                "company_name": "内田洋行",
            },
        ]
        parse_request = Mock(
            return_value=request
        )
        search_candidates = Mock(
            return_value=records
        )
        build_message = Mock(
            return_value="検索結果"
        )
        edit_response = Mock()

        process_candidate_search_job(
            job,
            parse_request=parse_request,
            search_candidates=search_candidates,
            build_message=build_message,
            edit_response=edit_response,
        )

        parse_request.assert_called_once_with(
            job.options
        )
        search_candidates.assert_called_once_with(
            request
        )
        build_message.assert_called_once_with(
            records,
            request,
        )
        edit_response.assert_called_once_with(
            job,
            "検索結果",
        )

    def test_invalid_options_update_error_message(
        self,
    ) -> None:
        job = build_job(
            options={
                "limit": 21,
            }
        )
        search_candidates = Mock()
        build_message = Mock()
        edit_response = Mock()

        process_candidate_search_job(
            job,
            search_candidates=search_candidates,
            build_message=build_message,
            edit_response=edit_response,
        )

        search_candidates.assert_not_called()
        build_message.assert_not_called()
        edit_response.assert_called_once_with(
            job,
            INVALID_SEARCH_MESSAGE,
        )

    def test_search_failure_is_propagated(
        self,
    ) -> None:
        job = build_job()
        search_error = RuntimeError(
            "database unavailable"
        )

        with self.assertRaises(
            RuntimeError
        ):
            process_candidate_search_job(
                job,
                search_candidates=Mock(
                    side_effect=search_error
                ),
                edit_response=Mock(),
            )


# ============================================================
# SQS部分バッチ応答
# ============================================================

class SqsLambdaHandlerTests(unittest.TestCase):
    """SQSの部分バッチ失敗応答を確認する。"""

    @patch(
        "discord_candidate_search_worker."
        "process_sqs_record"
    )
    def test_all_success_returns_empty_failures(
        self,
        process_record_mock,
    ) -> None:
        event = {
            "Records": [
                build_sqs_record(
                    "message-1",
                    build_job_payload(),
                ),
                build_sqs_record(
                    "message-2",
                    build_job_payload(),
                ),
            ],
        }

        response = lambda_handler(
            event,
            None,
        )

        self.assertEqual(
            response,
            {
                "batchItemFailures": [],
            },
        )
        self.assertEqual(
            process_record_mock.call_count,
            2,
        )

    @patch(
        "discord_candidate_search_worker."
        "process_sqs_record"
    )
    def test_retryable_failure_is_returned(
        self,
        process_record_mock,
    ) -> None:
        process_record_mock.side_effect = [
            None,
            RetryableWorkerError(
                "temporary failure"
            ),
        ]
        event = {
            "Records": [
                build_sqs_record(
                    "message-1",
                    build_job_payload(),
                ),
                build_sqs_record(
                    "message-2",
                    build_job_payload(),
                ),
            ],
        }

        response = lambda_handler(
            event,
            None,
        )

        self.assertEqual(
            response,
            {
                "batchItemFailures": [
                    {
                        "itemIdentifier": (
                            "message-2"
                        ),
                    },
                ],
            },
        )

    @patch(
        "discord_candidate_search_worker."
        "process_sqs_record"
    )
    def test_unexpected_failure_is_returned(
        self,
        process_record_mock,
    ) -> None:
        process_record_mock.side_effect = RuntimeError(
            "unexpected"
        )
        event = {
            "Records": [
                build_sqs_record(
                    "message-1",
                    build_job_payload(),
                ),
            ],
        }

        response = lambda_handler(
            event,
            None,
        )

        self.assertEqual(
            response,
            {
                "batchItemFailures": [
                    {
                        "itemIdentifier": (
                            "message-1"
                        ),
                    },
                ],
            },
        )

    @patch(
        "discord_candidate_search_worker."
        "process_sqs_record"
    )
    def test_permanent_failure_is_not_retried(
        self,
        process_record_mock,
    ) -> None:
        process_record_mock.side_effect = (
            PermanentWorkerError(
                "invalid message"
            )
        )
        event = {
            "Records": [
                build_sqs_record(
                    "message-1",
                    build_job_payload(),
                ),
            ],
        }

        response = lambda_handler(
            event,
            None,
        )

        self.assertEqual(
            response,
            {
                "batchItemFailures": [],
            },
        )

    def test_invalid_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Records",
        ):
            lambda_handler(
                {},
                None,
            )


if __name__ == "__main__":
    unittest.main()
