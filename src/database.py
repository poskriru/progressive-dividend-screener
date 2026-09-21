"""
PostgreSQL接続の共通処理。

各データ取得プログラムは、このモジュールを経由して
Supabase PostgreSQLへ接続する。

DATABASE_URLそのものはログへ出力しない。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import os
import re
from typing import Any, Iterable


# ============================================================
# 外部ライブラリ
# ============================================================

import psycopg
from psycopg.rows import dict_row


# ============================================================
# 定数
# ============================================================

DATABASE_SCHEMA = "screener"

DEFAULT_CONNECT_TIMEOUT_SECONDS = 20
DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS = 0

DATABASE_CONNECT_TIMEOUT_ENV_NAME = (
    "DATABASE_CONNECT_TIMEOUT_SECONDS"
)
DATABASE_STATEMENT_TIMEOUT_ENV_NAME = (
    "DATABASE_STATEMENT_TIMEOUT_MILLISECONDS"
)

MIN_CONNECT_TIMEOUT_SECONDS = 1
MAX_CONNECT_TIMEOUT_SECONDS = 120

MIN_STATEMENT_TIMEOUT_MILLISECONDS = 0
MAX_STATEMENT_TIMEOUT_MILLISECONDS = 900_000

APPLICATION_NAME_PATTERN = re.compile(
    r"^[a-zA-Z0-9_.-]+$"
)


# ============================================================
# 独自例外
# ============================================================

class DatabaseConfigurationError(RuntimeError):
    """
    データベース設定に問題がある場合の例外。
    """


class DatabaseSchemaError(RuntimeError):
    """
    必要なスキーマやテーブルが存在しない場合の例外。
    """


# ============================================================
# 環境変数
# ============================================================

def get_database_url() -> str:
    """
    DATABASE_URLを取得する。

    接続文字列はログへ出力しない。
    """

    database_url = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not database_url:
        raise DatabaseConfigurationError(
            "必須環境変数DATABASE_URLが"
            "設定されていません。"
        )

    if not database_url.startswith(
        (
            "postgresql://",
            "postgres://",
        )
    ):
        raise DatabaseConfigurationError(
            "DATABASE_URLがPostgreSQLの"
            "接続文字列ではありません。"
        )

    return database_url


def get_integer_environment_variable(
    environment_name: str,
    *,
    default_value: int,
    minimum_value: int,
    maximum_value: int,
) -> int:
    """
    整数の環境変数を取得して範囲を検証する。
    """

    raw_value = os.getenv(
        environment_name,
        "",
    ).strip()

    if not raw_value:
        return default_value

    try:
        parsed_value = int(raw_value)
    except ValueError as error:
        raise DatabaseConfigurationError(
            f"{environment_name}は整数で"
            "指定してください。"
        ) from error

    if not (
        minimum_value
        <= parsed_value
        <= maximum_value
    ):
        raise DatabaseConfigurationError(
            f"{environment_name}は"
            f"{minimum_value}以上"
            f"{maximum_value}以下で"
            "指定してください。"
        )

    return parsed_value


def get_database_connect_timeout_seconds() -> int:
    """
    PostgreSQL接続タイムアウト秒数を取得する。
    """

    return get_integer_environment_variable(
        DATABASE_CONNECT_TIMEOUT_ENV_NAME,
        default_value=(
            DEFAULT_CONNECT_TIMEOUT_SECONDS
        ),
        minimum_value=(
            MIN_CONNECT_TIMEOUT_SECONDS
        ),
        maximum_value=(
            MAX_CONNECT_TIMEOUT_SECONDS
        ),
    )


def get_database_statement_timeout_milliseconds() -> int:
    """
    PostgreSQL SQL実行タイムアウトをミリ秒で取得する。

    0の場合はstatement_timeoutを設定しない。
    """

    return get_integer_environment_variable(
        DATABASE_STATEMENT_TIMEOUT_ENV_NAME,
        default_value=(
            DEFAULT_STATEMENT_TIMEOUT_MILLISECONDS
        ),
        minimum_value=(
            MIN_STATEMENT_TIMEOUT_MILLISECONDS
        ),
        maximum_value=(
            MAX_STATEMENT_TIMEOUT_MILLISECONDS
        ),
    )


# ============================================================
# アプリケーション名
# ============================================================

def validate_application_name(
    application_name: str,
) -> str:
    """
    PostgreSQL接続に設定するアプリケーション名を検証する。
    """

    normalized_name = str(
        application_name
    ).strip()

    if not normalized_name:
        raise DatabaseConfigurationError(
            "データベース接続のapplication_nameが"
            "空です。"
        )

    if len(normalized_name) > 60:
        raise DatabaseConfigurationError(
            "データベース接続のapplication_nameが"
            "長すぎます。"
        )

    if not APPLICATION_NAME_PATTERN.fullmatch(
        normalized_name
    ):
        raise DatabaseConfigurationError(
            "データベース接続のapplication_nameに"
            "使用できない文字が含まれています。"
        )

    return normalized_name


# ============================================================
# PostgreSQL接続
# ============================================================

def create_database_connection(
    application_name: str,
) -> psycopg.Connection:
    """
    PostgreSQL接続を作成する。

    呼び出し側ではwith文を使用し、処理終了時に
    接続を確実に閉じること。

    DATABASE_URLそのものやホスト名、パスワードは
    ログへ出力しない。
    """

    database_url = get_database_url()

    validated_application_name = (
        validate_application_name(
            application_name
        )
    )

    connect_timeout_seconds = (
        get_database_connect_timeout_seconds()
    )
    statement_timeout_milliseconds = (
        get_database_statement_timeout_milliseconds()
    )

    print(
        "PostgreSQL接続を開始します。"
        f" application_name={validated_application_name}"
        f" connect_timeout_seconds="
        f"{connect_timeout_seconds}"
        f" statement_timeout_milliseconds="
        f"{statement_timeout_milliseconds}",
        flush=True,
    )

    connection = psycopg.connect(
        database_url,
        connect_timeout=connect_timeout_seconds,
        sslmode="require",
        application_name=(
            validated_application_name
        ),
        row_factory=dict_row,
    )

    print(
        "PostgreSQL接続に成功しました。"
        f" application_name={validated_application_name}",
        flush=True,
    )

    if statement_timeout_milliseconds <= 0:
        return connection

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT set_config(
                    %s,
                    %s,
                    TRUE
                )
                """,
                (
                    "statement_timeout",
                    (
                        f"{statement_timeout_milliseconds}"
                        "ms"
                    ),
                ),
            )
    except Exception:
        connection.close()
        raise

    print(
        "PostgreSQLのstatement_timeoutを"
        "設定しました。"
        f" application_name={validated_application_name}"
        f" statement_timeout_milliseconds="
        f"{statement_timeout_milliseconds}",
        flush=True,
    )

    return connection


# ============================================================
# 接続情報取得
# ============================================================

def get_database_information(
    connection: psycopg.Connection,
) -> dict[str, Any]:
    """
    接続先データベースの安全な基本情報を取得する。

    パスワード、ホスト名、接続文字列は取得しない。
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                1 AS connection_test,
                current_database() AS database_name,
                current_user AS database_user,
                current_setting(
                    'server_version'
                ) AS server_version,
                current_schema() AS current_schema
            """
        )

        result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "PostgreSQLから接続情報を"
            "取得できませんでした。"
        )

    return dict(result)


# ============================================================
# スキーマ確認
# ============================================================

def get_existing_tables(
    connection: psycopg.Connection,
    *,
    schema_name: str = DATABASE_SCHEMA,
) -> set[str]:
    """
    指定スキーマに存在するテーブル名を取得する。
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (schema_name,),
        )

        return {
            str(row["table_name"])
            for row in cursor.fetchall()
        }


def verify_required_tables(
    connection: psycopg.Connection,
    required_tables: Iterable[str],
    *,
    schema_name: str = DATABASE_SCHEMA,
) -> set[str]:
    """
    必要なテーブルが存在することを確認する。

    戻り値は、指定スキーマに存在する全テーブル名。
    """

    normalized_required_tables = {
        str(table_name).strip()
        for table_name in required_tables
        if str(table_name).strip()
    }

    if not normalized_required_tables:
        raise DatabaseSchemaError(
            "確認対象のテーブルが指定されていません。"
        )

    existing_tables = get_existing_tables(
        connection,
        schema_name=schema_name,
    )

    missing_tables = (
        normalized_required_tables
        - existing_tables
    )

    if missing_tables:
        raise DatabaseSchemaError(
            "必要なデータベーステーブルが"
            "存在しません。"
            f"スキーマ: {schema_name}, "
            f"不足テーブル: {sorted(missing_tables)}"
        )

    return existing_tables
