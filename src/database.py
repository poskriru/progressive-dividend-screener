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
    """

    database_url = get_database_url()

    validated_application_name = (
        validate_application_name(
            application_name
        )
    )

    return psycopg.connect(
        database_url,
        connect_timeout=(
            DEFAULT_CONNECT_TIMEOUT_SECONDS
        ),
        sslmode="require",
        application_name=(
            validated_application_name
        ),
        row_factory=dict_row,
    )


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
