"""Discord Interactions受信処理。

Discordから送信されたHTTPリクエストの署名を検証し、
累進配当候補検索をSQSへ登録してDeferred Responseを返す。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import base64
import binascii
import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any


# ============================================================
# 外部ライブラリ
# ============================================================

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


# ============================================================
# ロガー
# ============================================================

LOGGER = logging.getLogger(__name__)


# ============================================================
# Discord定数
# ============================================================

DISCORD_INTERACTION_TYPE_PING = 1
DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND = 2

DISCORD_RESPONSE_TYPE_PONG = 1
DISCORD_RESPONSE_TYPE_CHANNEL_MESSAGE = 4
DISCORD_RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE = 5

DISCORD_MESSAGE_FLAG_EPHEMERAL = 64

SUPPORTED_COMMAND_NAME = "dividend-search"

SIGNATURE_HEADER_NAME = "x-signature-ed25519"
TIMESTAMP_HEADER_NAME = "x-signature-timestamp"

DISCORD_PUBLIC_KEY_ENV_NAME = "DISCORD_PUBLIC_KEY"
DISCORD_SEARCH_QUEUE_URL_ENV_NAME = (
    "DISCORD_SEARCH_QUEUE_URL"
)

SUPPORTED_OPTION_NAMES = frozenset(
    {
        "min_yield",
        "max_payout",
        "max_per",
        "max_pbr",
        "min_roe",
        "positive_fcf",
        "limit",
    }
)


# ============================================================
# HTTPレスポンス
# ============================================================

def build_http_response(
    status_code: int,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Lambda Function URL形式のHTTPレスポンスを作成する。"""

    return {
        "statusCode": status_code,
        "headers": {
            "content-type": (
                "application/json; charset=utf-8"
            ),
        },
        "body": json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def build_error_response(
    status_code: int,
    message: str,
) -> dict[str, Any]:
    """外部へ詳細を公開しすぎないエラーレスポンスを作成する。"""

    return build_http_response(
        status_code,
        {
            "error": message,
        },
    )


def build_ephemeral_message_response(
    message: str,
) -> dict[str, Any]:
    """Discord上で実行者だけに表示する応答を作成する。"""

    return build_http_response(
        200,
        {
            "type": (
                DISCORD_RESPONSE_TYPE_CHANNEL_MESSAGE
            ),
            "data": {
                "content": message,
                "flags": DISCORD_MESSAGE_FLAG_EPHEMERAL,
            },
        },
    )


def build_deferred_response() -> dict[str, Any]:
    """検索完了までDiscordへ待機状態を返す。"""

    return build_http_response(
        200,
        {
            "type": (
                DISCORD_RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE
            ),
            "data": {
                "flags": DISCORD_MESSAGE_FLAG_EPHEMERAL,
            },
        },
    )


# ============================================================
# Lambda Function URL入力
# ============================================================

def normalize_headers(
    headers: Mapping[str, Any] | None,
) -> dict[str, str]:
    """HTTPヘッダー名を小文字へ統一する。"""

    if not isinstance(headers, Mapping):
        return {}

    normalized_headers: dict[str, str] = {}

    for name, value in headers.items():
        if value is None:
            continue

        normalized_headers[str(name).lower()] = str(value)

    return normalized_headers


def decode_request_body(
    event: Mapping[str, Any],
) -> bytes:
    """Lambda Function URLイベントから生の本文を取得する。"""

    body = event.get("body")

    if not isinstance(body, str):
        raise ValueError(
            "HTTPリクエスト本文がありません。"
        )

    if event.get("isBase64Encoded") is True:
        try:
            return base64.b64decode(
                body,
                validate=True,
            )
        except (
            binascii.Error,
            ValueError,
        ) as error:
            raise ValueError(
                "Base64形式の本文が不正です。"
            ) from error

    return body.encode("utf-8")


def get_request_method(
    event: Mapping[str, Any],
) -> str:
    """Lambda Function URLイベントからHTTPメソッドを取得する。"""

    request_context = event.get("requestContext")

    if not isinstance(request_context, Mapping):
        return ""

    http_context = request_context.get("http")

    if not isinstance(http_context, Mapping):
        return ""

    method = http_context.get("method")

    if not isinstance(method, str):
        return ""

    return method.upper()


# ============================================================
# Discord署名検証
# ============================================================

def verify_discord_signature(
    *,
    public_key: str,
    signature: str,
    timestamp: str,
    raw_body: bytes,
) -> bool:
    """DiscordのEd25519署名を検証する。"""

    try:
        verify_key = VerifyKey(
            bytes.fromhex(public_key.strip())
        )
        signature_bytes = bytes.fromhex(
            signature.strip()
        )
        signed_message = (
            timestamp.encode("utf-8")
            + raw_body
        )

        verify_key.verify(
            signed_message,
            signature_bytes,
        )
    except (
        BadSignatureError,
        TypeError,
        ValueError,
    ):
        return False

    return True


# ============================================================
# Slash Command入力
# ============================================================

def parse_interaction_payload(
    raw_body: bytes,
) -> Mapping[str, Any]:
    """Discord InteractionのJSON本文を読み込む。"""

    try:
        payload = json.loads(
            raw_body.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(
            "JSON形式の本文が不正です。"
        ) from error

    if not isinstance(payload, Mapping):
        raise ValueError(
            "Interaction本文はJSONオブジェクトで指定してください。"
        )

    return payload


def extract_command_options(
    interaction: Mapping[str, Any],
) -> dict[str, Any]:
    """Discordのoption配列を検索コア用の辞書へ変換する。"""

    data = interaction.get("data")

    if not isinstance(data, Mapping):
        raise ValueError(
            "Interaction dataがありません。"
        )

    raw_options = data.get("options", [])

    if raw_options is None:
        return {}

    if not isinstance(raw_options, list):
        raise ValueError(
            "Interaction optionsが不正です。"
        )

    options: dict[str, Any] = {}

    for raw_option in raw_options:
        if not isinstance(raw_option, Mapping):
            raise ValueError(
                "Interaction optionが不正です。"
            )

        name = raw_option.get("name")

        if (
            not isinstance(name, str)
            or name not in SUPPORTED_OPTION_NAMES
        ):
            raise ValueError(
                "未対応の検索条件が指定されています。"
            )

        if name in options:
            raise ValueError(
                "同じ検索条件が複数指定されています。"
            )

        if "value" not in raw_option:
            raise ValueError(
                "検索条件の値がありません。"
            )

        options[name] = raw_option["value"]

    return options


def build_search_job(
    interaction: Mapping[str, Any],
) -> dict[str, Any]:
    """検索Workerへ渡すSQSメッセージを作成する。"""

    application_id = interaction.get("application_id")
    interaction_id = interaction.get("id")
    interaction_token = interaction.get("token")

    if not isinstance(application_id, str):
        raise ValueError(
            "application_idがありません。"
        )

    if not isinstance(interaction_id, str):
        raise ValueError(
            "Interaction IDがありません。"
        )

    if not isinstance(interaction_token, str):
        raise ValueError(
            "Interaction tokenがありません。"
        )

    if not interaction_token:
        raise ValueError(
            "Interaction tokenが空です。"
        )

    return {
        "application_id": application_id,
        "interaction_id": interaction_id,
        "interaction_token": interaction_token,
        "options": extract_command_options(
            interaction
        ),
    }


# ============================================================
# SQS登録
# ============================================================

def enqueue_candidate_search(
    job: Mapping[str, Any],
) -> None:
    """累進配当候補検索をSQSへ登録する。"""

    queue_url = os.environ.get(
        DISCORD_SEARCH_QUEUE_URL_ENV_NAME,
        "",
    ).strip()

    if not queue_url:
        raise RuntimeError(
            f"{DISCORD_SEARCH_QUEUE_URL_ENV_NAME}"
            "が設定されていません。"
        )

    # Lambda Pythonランタイムに含まれるboto3を使用する。
    # ローカルテストではこの関数をモックするため、
    # モジュール読込時にはboto3を要求しない。
    import boto3

    sqs_client = boto3.client("sqs")
    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            dict(job),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


# ============================================================
# Discord Interaction処理
# ============================================================

def handle_discord_interaction(
    event: Mapping[str, Any],
    *,
    public_key: str,
    enqueue_search: Callable[
        [Mapping[str, Any]],
        None,
    ],
) -> dict[str, Any]:
    """署名検証済みInteractionを処理する。"""

    if get_request_method(event) != "POST":
        return build_error_response(
            405,
            "Method Not Allowed",
        )

    headers = normalize_headers(
        event.get("headers")
    )
    signature = headers.get(
        SIGNATURE_HEADER_NAME,
        "",
    )
    timestamp = headers.get(
        TIMESTAMP_HEADER_NAME,
        "",
    )

    if not public_key.strip():
        LOGGER.error(
            "%sが設定されていません。",
            DISCORD_PUBLIC_KEY_ENV_NAME,
        )
        return build_error_response(
            500,
            "Server configuration error",
        )

    if not signature or not timestamp:
        return build_error_response(
            401,
            "Invalid request signature",
        )

    try:
        raw_body = decode_request_body(event)
    except ValueError:
        return build_error_response(
            400,
            "Invalid request body",
        )

    if not verify_discord_signature(
        public_key=public_key,
        signature=signature,
        timestamp=timestamp,
        raw_body=raw_body,
    ):
        return build_error_response(
            401,
            "Invalid request signature",
        )

    try:
        interaction = parse_interaction_payload(
            raw_body
        )
    except ValueError:
        return build_error_response(
            400,
            "Invalid interaction payload",
        )

    interaction_type = interaction.get("type")

    if (
        interaction_type
        == DISCORD_INTERACTION_TYPE_PING
    ):
        return build_http_response(
            200,
            {
                "type": DISCORD_RESPONSE_TYPE_PONG,
            },
        )

    if (
        interaction_type
        != DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND
    ):
        return build_ephemeral_message_response(
            "このInteraction形式には対応していません。"
        )

    data = interaction.get("data")

    if not isinstance(data, Mapping):
        return build_ephemeral_message_response(
            "コマンド情報を確認できませんでした。"
        )

    if data.get("name") != SUPPORTED_COMMAND_NAME:
        return build_ephemeral_message_response(
            "未対応のコマンドです。"
        )

    try:
        search_job = build_search_job(
            interaction
        )
        enqueue_search(search_job)
    except ValueError as error:
        return build_ephemeral_message_response(
            f"検索条件が不正です: {error}"
        )
    except Exception:
        LOGGER.exception(
            "候補検索のSQS登録に失敗しました。"
        )
        return build_ephemeral_message_response(
            "検索を受け付けられませんでした。"
            "しばらくしてから再実行してください。"
        )

    return build_deferred_response()


# ============================================================
# AWS Lambdaエントリーポイント
# ============================================================

def lambda_handler(
    event: Mapping[str, Any],
    context: Any,
) -> dict[str, Any]:
    """AWS Lambda Function URLのエントリーポイント。"""

    del context

    public_key = os.environ.get(
        DISCORD_PUBLIC_KEY_ENV_NAME,
        "",
    )

    return handle_discord_interaction(
        event,
        public_key=public_key,
        enqueue_search=enqueue_candidate_search,
    )
