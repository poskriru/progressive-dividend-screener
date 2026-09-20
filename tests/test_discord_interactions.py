"""Discord Interactions受信処理のテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


# ============================================================
# 外部ライブラリ
# ============================================================

from nacl.signing import SigningKey


# ============================================================
# テスト対象の読込
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from discord_interactions import (  # noqa: E402
    DISCORD_MESSAGE_FLAG_EPHEMERAL,
    build_search_job,
    extract_command_options,
    handle_discord_interaction,
)


# ============================================================
# テスト補助
# ============================================================

class DiscordInteractionTestCase(unittest.TestCase):
    """署名済みDiscordリクエストを作成する。"""

    def setUp(self) -> None:
        self.signing_key = SigningKey.generate()
        self.public_key = (
            self.signing_key.verify_key.encode().hex()
        )
        self.timestamp = "1789923000"

    def build_event(
        self,
        payload,
        *,
        base64_encoded: bool = False,
        method: str = "POST",
    ):
        raw_body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        signature = self.signing_key.sign(
            self.timestamp.encode("utf-8")
            + raw_body
        ).signature.hex()

        if base64_encoded:
            body = base64.b64encode(
                raw_body
            ).decode("ascii")
        else:
            body = raw_body.decode("utf-8")

        return {
            "requestContext": {
                "http": {
                    "method": method,
                },
            },
            "headers": {
                "X-Signature-Ed25519": signature,
                "X-Signature-Timestamp": (
                    self.timestamp
                ),
            },
            "body": body,
            "isBase64Encoded": base64_encoded,
        }

    @staticmethod
    def read_response_body(response):
        return json.loads(response["body"])


# ============================================================
# 署名検証・PING
# ============================================================

class DiscordSignatureTests(
    DiscordInteractionTestCase
):
    """Discord署名とPING応答を確認する。"""

    def test_valid_ping_returns_pong(self) -> None:
        enqueue_mock = Mock()
        event = self.build_event(
            {
                "type": 1,
            }
        )

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=enqueue_mock,
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )
        self.assertEqual(
            self.read_response_body(response),
            {
                "type": 1,
            },
        )
        enqueue_mock.assert_not_called()

    def test_base64_encoded_ping_returns_pong(
        self,
    ) -> None:
        event = self.build_event(
            {
                "type": 1,
            },
            base64_encoded=True,
        )

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=Mock(),
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )
        self.assertEqual(
            self.read_response_body(response),
            {
                "type": 1,
            },
        )

    def test_invalid_signature_is_rejected(
        self,
    ) -> None:
        event = self.build_event(
            {
                "type": 1,
            }
        )
        event["headers"]["X-Signature-Ed25519"] = (
            "00" * 64
        )
        enqueue_mock = Mock()

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=enqueue_mock,
        )

        self.assertEqual(
            response["statusCode"],
            401,
        )
        enqueue_mock.assert_not_called()

    def test_missing_signature_is_rejected(
        self,
    ) -> None:
        event = self.build_event(
            {
                "type": 1,
            }
        )
        del event["headers"]["X-Signature-Ed25519"]

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=Mock(),
        )

        self.assertEqual(
            response["statusCode"],
            401,
        )

    def test_missing_public_key_returns_server_error(
        self,
    ) -> None:
        event = self.build_event(
            {
                "type": 1,
            }
        )

        response = handle_discord_interaction(
            event,
            public_key="",
            enqueue_search=Mock(),
        )

        self.assertEqual(
            response["statusCode"],
            500,
        )

    def test_non_post_request_is_rejected(
        self,
    ) -> None:
        event = self.build_event(
            {
                "type": 1,
            },
            method="GET",
        )

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=Mock(),
        )

        self.assertEqual(
            response["statusCode"],
            405,
        )


# ============================================================
# Slash Command処理
# ============================================================

class DiscordCommandTests(
    DiscordInteractionTestCase
):
    """Slash Commandの受付処理を確認する。"""

    def build_search_interaction(self):
        return {
            "id": "123456789012345678",
            "application_id": "987654321098765432",
            "type": 2,
            "token": "interaction-token",
            "data": {
                "name": "dividend-search",
                "type": 1,
                "options": [
                    {
                        "name": "min_yield",
                        "type": 10,
                        "value": 4.25,
                    },
                    {
                        "name": "positive_fcf",
                        "type": 5,
                        "value": False,
                    },
                    {
                        "name": "limit",
                        "type": 4,
                        "value": 5,
                    },
                ],
            },
        }

    def test_search_command_is_enqueued_and_deferred(
        self,
    ) -> None:
        interaction = self.build_search_interaction()
        event = self.build_event(interaction)
        enqueue_mock = Mock()

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=enqueue_mock,
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )
        self.assertEqual(
            self.read_response_body(response),
            {
                "type": 5,
                "data": {
                    "flags": (
                        DISCORD_MESSAGE_FLAG_EPHEMERAL
                    ),
                },
            },
        )
        enqueue_mock.assert_called_once_with(
            {
                "application_id": (
                    "987654321098765432"
                ),
                "interaction_id": (
                    "123456789012345678"
                ),
                "interaction_token": (
                    "interaction-token"
                ),
                "options": {
                    "min_yield": 4.25,
                    "positive_fcf": False,
                    "limit": 5,
                },
            }
        )

    def test_command_without_options_is_allowed(
        self,
    ) -> None:
        interaction = self.build_search_interaction()
        del interaction["data"]["options"]

        job = build_search_job(interaction)

        self.assertEqual(
            job["options"],
            {},
        )

    def test_unsupported_command_is_not_enqueued(
        self,
    ) -> None:
        interaction = self.build_search_interaction()
        interaction["data"]["name"] = "unknown-command"
        event = self.build_event(interaction)
        enqueue_mock = Mock()

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=enqueue_mock,
        )

        response_body = self.read_response_body(
            response
        )

        self.assertEqual(
            response_body["type"],
            4,
        )
        self.assertEqual(
            response_body["data"]["flags"],
            DISCORD_MESSAGE_FLAG_EPHEMERAL,
        )
        enqueue_mock.assert_not_called()

    def test_unknown_option_is_rejected(
        self,
    ) -> None:
        interaction = self.build_search_interaction()
        interaction["data"]["options"].append(
            {
                "name": "unknown",
                "type": 3,
                "value": "value",
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "未対応",
        ):
            extract_command_options(interaction)

    def test_duplicate_option_is_rejected(
        self,
    ) -> None:
        interaction = self.build_search_interaction()
        interaction["data"]["options"].append(
            {
                "name": "limit",
                "type": 4,
                "value": 10,
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "複数",
        ):
            extract_command_options(interaction)

    def test_queue_failure_returns_ephemeral_error(
        self,
    ) -> None:
        event = self.build_event(
            self.build_search_interaction()
        )
        enqueue_mock = Mock(
            side_effect=RuntimeError(
                "SQS unavailable"
            )
        )

        response = handle_discord_interaction(
            event,
            public_key=self.public_key,
            enqueue_search=enqueue_mock,
        )

        response_body = self.read_response_body(
            response
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )
        self.assertEqual(
            response_body["type"],
            4,
        )
        self.assertIn(
            "検索を受け付けられませんでした",
            response_body["data"]["content"],
        )


if __name__ == "__main__":
    unittest.main()
