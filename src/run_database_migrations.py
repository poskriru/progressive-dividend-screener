"""
PostgreSQLのデータベースマイグレーションを実行する。

database/migrationsディレクトリ内のSQLファイルを
ファイル名順に実行し、適用済みファイルとSHA-256を
screener.schema_migrationsへ記録する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import hashlib
import os
import re
import sys
from pathlib import Path


# ============================================================
# 外部ライブラリ
# ============================================================

import psycopg


# ============================================================
# 定数
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MIGRATIONS_DIRECTORY = (
    PROJECT_ROOT
    / "database"
    / "migrations"
)

MIGRATION_FILE_PATTERN = re.compile(
    r"^\d{3}_[a-z0-9_]+\.sql$"
)

EXPECTED_TABLES = {
    "schema_migrations",
    "securities",
    "daily_prices",
    "edinet_documents",
    "annual_financials",
}


# ============================================================
# 環境変数
# ============================================================

def get_required_environment_variable(name: str) -> str:
    """
    必須環境変数を取得する。

    値そのものはログへ出力しない。
    """

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"必須環境変数が設定されていません: {name}"
        )

    return value


# ============================================================
# マイグレーションファイル
# ============================================================

def get_migration_files() -> list[Path]:
    """
    マイグレーションSQLをファイル名順に取得する。
    """

    if not MIGRATIONS_DIRECTORY.exists():
        raise RuntimeError(
            "マイグレーションディレクトリがありません: "
            f"{MIGRATIONS_DIRECTORY}"
        )

    migration_files = sorted(
        path
        for path in MIGRATIONS_DIRECTORY.iterdir()
        if (
            path.is_file()
            and MIGRATION_FILE_PATTERN.fullmatch(path.name)
        )
    )

    if not migration_files:
        raise RuntimeError(
            "マイグレーションSQLが見つかりません。"
        )

    return migration_files


def calculate_sha256(file_path: Path) -> str:
    """
    SQLファイルのSHA-256を計算する。
    """

    return hashlib.sha256(
        file_path.read_bytes()
    ).hexdigest()


# ============================================================
# マイグレーション管理テーブル
# ============================================================

def initialize_migration_table(
    connection: psycopg.Connection,
) -> None:
    """
    専用スキーマとマイグレーション管理テーブルを作成する。
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE SCHEMA IF NOT EXISTS screener
            """
        )

        cursor.execute(
            """
            REVOKE ALL ON SCHEMA screener FROM PUBLIC
            """
        )

        cursor.execute(
            """
            REVOKE ALL ON SCHEMA screener FROM anon
            """
        )

        cursor.execute(
            """
            REVOKE ALL ON SCHEMA screener FROM authenticated
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS
                screener.schema_migrations
            (
                migration_name text PRIMARY KEY,
                sha256 text NOT NULL,
                applied_at timestamptz
                    NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                CONSTRAINT schema_migrations_name_not_blank
                    CHECK (
                        btrim(migration_name) <> ''
                    ),

                CONSTRAINT schema_migrations_sha256_format
                    CHECK (
                        sha256 ~ '^[0-9a-f]{64}$'
                    )
            )
            """
        )

        cursor.execute(
            """
            REVOKE ALL
            ON screener.schema_migrations
            FROM anon, authenticated
            """
        )

    connection.commit()


# ============================================================
# マイグレーション実行
# ============================================================

def apply_migration(
    connection: psycopg.Connection,
    migration_file: Path,
) -> str:
    """
    1つのマイグレーションを適用する。

    戻り値:
    - applied: 新規適用
    - skipped: 適用済み
    """

    migration_name = migration_file.name
    migration_sha256 = calculate_sha256(
        migration_file
    )

    sql_text = migration_file.read_text(
        encoding="utf-8"
    )

    if not sql_text.strip():
        raise RuntimeError(
            f"SQLファイルが空です: {migration_name}"
        )

    with connection.transaction():
        with connection.cursor() as cursor:
            # 同じマイグレーションが同時実行されないよう、
            # トランザクション単位のロックを取得する。
            cursor.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext(
                        'progressive-dividend-screener'
                    )
                )
                """
            )

            cursor.execute(
                """
                SELECT sha256
                FROM screener.schema_migrations
                WHERE migration_name = %s
                """,
                (migration_name,),
            )

            existing_record = cursor.fetchone()

            if existing_record is not None:
                existing_sha256 = existing_record[0]

                if existing_sha256 != migration_sha256:
                    raise RuntimeError(
                        "適用済みマイグレーションが"
                        "変更されています。"
                        f"ファイル: {migration_name}"
                    )

                print(
                    "適用済みのためスキップします: "
                    f"{migration_name}"
                )

                return "skipped"

            print(
                "マイグレーションを適用します: "
                f"{migration_name}"
            )

            cursor.execute(sql_text)

            cursor.execute(
                """
                INSERT INTO screener.schema_migrations (
                    migration_name,
                    sha256
                )
                VALUES (%s, %s)
                """,
                (
                    migration_name,
                    migration_sha256,
                ),
            )

    print(
        "マイグレーションを適用しました: "
        f"{migration_name}"
    )

    return "applied"


# ============================================================
# 作成結果検証
# ============================================================

def verify_database_schema(
    connection: psycopg.Connection,
) -> None:
    """
    必要なテーブルが作成されていることを確認する。
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'screener'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )

        actual_tables = {
            row[0]
            for row in cursor.fetchall()
        }

    missing_tables = (
        EXPECTED_TABLES - actual_tables
    )

    if missing_tables:
        raise RuntimeError(
            "必要なテーブルが作成されていません。"
            f"不足テーブル: {sorted(missing_tables)}"
        )

    print("データベーススキーマを確認しました。")

    for table_name in sorted(actual_tables):
        print(f"作成済みテーブル: screener.{table_name}")


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    全マイグレーションを実行する。
    """

    database_url = get_required_environment_variable(
        "DATABASE_URL"
    )

    migration_files = get_migration_files()

    print(
        "データベースマイグレーションを開始します。"
    )

    print(
        "検出したマイグレーション数: "
        f"{len(migration_files)}"
    )

    applied_count = 0
    skipped_count = 0

    with psycopg.connect(
        database_url,
        connect_timeout=20,
        sslmode="require",
    ) as connection:
        initialize_migration_table(connection)

        for migration_file in migration_files:
            result = apply_migration(
                connection,
                migration_file,
            )

            if result == "applied":
                applied_count += 1
            else:
                skipped_count += 1

        verify_database_schema(connection)

    print("データベースマイグレーション完了")
    print(f"新規適用: {applied_count}件")
    print(f"適用済み: {skipped_count}件")


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            "データベースマイグレーションに"
            "失敗しました。",
            file=sys.stderr,
        )

        print(
            f"エラー種別: {type(error).__name__}",
            file=sys.stderr,
        )

        print(
            f"エラー内容: {error}",
            file=sys.stderr,
        )

        # DATABASE_URLそのものは出力しない。
        sys.exit(1)
