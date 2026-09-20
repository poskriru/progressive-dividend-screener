"""Discord累進配当候補検索のSQS Worker。

SQSからDiscord検索要求を受け取り、
既存の候補検索コアでPostgreSQLを検索して、
DiscordのDeferred Responseを更新する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import json
import logging
from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from typing import Any


# ============================================================
# 外部ライブラリ
# ============================================================

import requests


# ============================================================
# プロジェクト内モジュール
# ============================================================

from candidate_search import (
    CandidateSearchRequest,
    build_candidate_search_message,
    parse_candidate_search_request,
    search_progressive_dividend_candidates,
)


# ============================================================
# ロガー
# ============================================================

LOGGER = logging.getLogger(__name__)


# ============================================================
# 定数
# ============================================================

DISCORD_API_BASE_URL = "https://discord.com/api/v10"
DISCORD_HTTP_TIMEOUT_SECONDS = 10

SUPPORTED_SEARCH_OPTION_NAMES = frozenset(
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

INVALID_SEARCH_MESSAGE = (
    "検索条件が不正です。"
    "条件を確認して、もう一度実行してください。"
)

SEARCH_FAILURE_MESSAGE = (
    "累進配当候補の検索中にエラーが発生しました。"
    "しばらくしてから、もう一度実行してください。"
)


# ============================================================
# 例外
# ============================================================

class RetryableWorkerError(RuntimeError):
    """SQSで再試行すべき一時的なエラー。"""


class PermanentWorkerError(RuntimeError):
    """再試行しても解消しない恒久的なエラー。"""


# ============================================================
# SQS検索要求
# ============================================================

@dataclass(frozen=True)
class CandidateSearchJob:
    """SQSから読み込んだDiscord検索要求。"""

    application_id: str
    interaction_id: str
    interaction_token: str
    options: Mapping[str, Any]


def require_non_empty_string(
    payload: Mapping[str, Any],
    field_name: str,
) -> str:
    """必須文字列を取得する。"""

    value = payload.get(field_name)

    if not isinstance(value, str):
        raise PermanentWorkerError(
            f"{field_name}は文字列で指定してください。"
        )

    normalized_value = value.strip()

    if not normalized_value:
        raise PermanentWorkerError(
            f"{field_name}が空です。"
        )

    return normalized_value


def validate_snowflake(
    value: str,
    field_name: str,
) -> str:
    """Discord Snowflake形式のIDを検証する。"""

    if not value.isdecimal():
        raise PermanentWorkerError(
            f"{field_name}は数字で指定してください。"
        )

    return value


def validate_search_options(
    raw_options: Any,
) -> dict[str, Any]:
    """SQS内の検索条件を検証してコピーする。"""

    if not isinstance(raw_options, Mapping):
        raise PermanentWorkerError(
            "optionsはJSONオブジェクトで"
            "指定してください。"
        )

    options: dict[str, Any] = {}

    for raw_name, value in raw_options.items():
        if not isinstance(raw_name, str):
            raise PermanentWorkerError(
                "検索条件名は文字列で"
                "指定してください。"
            )

        if raw_name not in SUPPORTED_SEARCH_OPTION_NAMES:
            raise PermanentWorkerError(
                "未対応の検索条件が指定されています。"
            )

        options[raw_name] = value

    return options


def parse_search_job_body(
    raw_body: Any,
) -> CandidateSearchJob:
    """SQSメッセージ本文を検索要求へ変換する。"""

    if not isinstance(raw_body, str):
        raise PermanentWorkerError(
            "SQSメッセージ本文がありません。"
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise PermanentWorkerError(
            "SQSメッセージ本文が不正なJSONです。"
        ) from error

    if not isinstance(payload, Mapping):
        raise PermanentWorkerError(
            "SQSメッセージ本文は"
            "JSONオブジェクトで指定してください。"
        )

    application_id = validate_snowflake(
        require_non_empty_string(
            payload,
            "application_id",
        ),
        "application_id",
    )
    interaction_id = validate_snowflake(
        require_non_empty_string(
            payload,
            "interaction_id",
        ),
        "interaction_id",
    )
    interaction_token = require_non_empty_string(
        payload,
        "interaction_token",
    )
    options = validate_search_options(
        payload.get("options")
    )

    return CandidateSearchJob(
        application_id=application_id,
        interaction_id=interaction_id,
        interaction_token=interaction_token,
        options=options,
    )


# ============================================================
# Discord応答
# ============================================================

def build_original_response_url(
    job: CandidateSearchJob,
) -> str:
    """Deferred Responseの更新URLを作成する。"""

    return (
        f"{DISCORD_API_BASE_URL}"
        f"/webhooks/{job.application_id}"
        f"/{job.interaction_token}"
        "/messages/@original"
    )


def edit_original_interaction_response(
    job: CandidateSearchJob,
    content: str,
    *,
    http_patch: Callable[..., Any] | None = None,
) -> None:
    """DiscordのDeferred Responseを検索結果へ更新する。"""

    if not isinstance(content, str) or not content:
        raise PermanentWorkerError(
            "Discordへ送信するメッセージが空です。"
        )

    if len(content) > 2000:
        raise PermanentWorkerError(
            "Discordメッセージが"
            "2000文字を超えています。"
        )

    if http_patch is None:
        http_patch = requests.patch

    try:
        response = http_patch(
            build_original_response_url(job),
            json={
                "content": content,
                "allowed_mentions": {
                    "parse": [],
                },
            },
            timeout=DISCORD_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise RetryableWorkerError(
            "Discord APIへの接続に失敗しました。"
        ) from error

    status_code = getattr(
        response,
        "status_code",
        None,
    )

    if (
        isinstance(status_code, int)
        and 200 <= status_code < 300
    ):
        return

    if status_code == 429:
        raise RetryableWorkerError(
            "Discord APIのRate Limitに到達しました。"
        )

    if (
        isinstance(status_code, int)
        and status_code >= 500
    ):
        raise RetryableWorkerError(
            "Discord APIで一時的なエラーが"
            "発生しました。"
        )

    raise PermanentWorkerError(
        "Discord APIがリクエストを拒否しました。"
        f" status={status_code}"
    )


# ============================================================
# 検索処理
# ============================================================

def process_candidate_search_job(
    job: CandidateSearchJob,
    *,
    parse_request: Callable[
        [Mapping[str, Any]],
        CandidateSearchRequest,
    ] = parse_candidate_search_request,
    search_candidates: Callable[
        [CandidateSearchRequest],
        list[dict[str, Any]],
    ] = search_progressive_dividend_candidates,
    build_message: Callable[
        [
            Sequence[Mapping[str, Any]],
            CandidateSearchRequest,
        ],
        str,
    ] = build_candidate_search_message,
    edit_response: Callable[
        [CandidateSearchJob, str],
        None,
    ] = edit_original_interaction_response,
) -> None:
    """検索要求を実行してDiscordの元メッセージを更新する。"""

    try:
        request = parse_request(job.options)
    except (TypeError, ValueError):
        edit_response(
            job,
            INVALID_SEARCH_MESSAGE,
        )
        return

    records = search_candidates(request)
    message = build_message(
        records,
        request,
    )

    edit_response(
        job,
        message,
    )


# ============================================================
# SQSイベント
# ============================================================

def get_sqs_records(
    event: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """LambdaイベントからSQSレコードを取得する。"""

    raw_records = event.get("Records")

    if not isinstance(raw_records, list):
        raise ValueError(
            "SQSイベントにRecords配列がありません。"
        )

    records: list[Mapping[str, Any]] = []

    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            raise ValueError(
                "SQSレコードが不正です。"
            )

        records.append(raw_record)

    return records


def get_sqs_message_id(
    record: Mapping[str, Any],
) -> str:
    """部分バッチ失敗応答で使用するmessageIdを取得する。"""

    message_id = record.get("messageId")

    if not isinstance(message_id, str):
        raise ValueError(
            "SQSレコードにmessageIdがありません。"
        )

    normalized_message_id = message_id.strip()

    if not normalized_message_id:
        raise ValueError(
            "SQSレコードのmessageIdが空です。"
        )

    return normalized_message_id


def process_sqs_record(
    record: Mapping[str, Any],
) -> None:
    """SQSレコード1件を処理する。"""

    job = parse_search_job_body(
        record.get("body")
    )

    LOGGER.info(
        "Discord候補検索を開始します。"
        " interaction_id=%s",
        job.interaction_id,
    )

    process_candidate_search_job(job)

    LOGGER.info(
        "Discord候補検索が完了しました。"
        " interaction_id=%s",
        job.interaction_id,
    )


# ============================================================
# AWS Lambdaエントリーポイント
# ============================================================

def lambda_handler(
    event: Mapping[str, Any],
    context: Any,
) -> dict[str, list[dict[str, str]]]:
    """SQSイベントを処理して部分バッチ失敗を返す。"""

    del context

    batch_item_failures: list[dict[str, str]] = []

    for record in get_sqs_records(event):
        message_id = get_sqs_message_id(record)

        try:
            process_sqs_record(record)
        except PermanentWorkerError as error:
            LOGGER.warning(
                "再試行しないSQSメッセージを破棄します。"
                " message_id=%s error=%s",
                message_id,
                error,
            )
        except Exception:
            LOGGER.exception(
                "SQSメッセージの処理に失敗しました。"
                " message_id=%s",
                message_id,
            )
            batch_item_failures.append(
                {
                    "itemIdentifier": message_id,
                }
            )

    return {
        "batchItemFailures": batch_item_failures,
    }
