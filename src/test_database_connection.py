"""
GitHub ActionsからSupabase PostgreSQLへ接続できることと、
初期データベーススキーマが存在することを確認する。

このプログラムはデータベースへの書き込みを行わない。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys


# ============================================================
# 外部ライブラリ
# ============================================================

import psycopg


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import (
    DATABASE_SCHEMA,
    create_database_connection,
    get_database_information,
    verify_required_tables,
)


# ============================================================
# 定数
# ============================================================

APPLICATION_NAME = (
    "progressive-dividend-screener-db-test"
)

REQUIRED_TABLES = {
    "schema_migrations",
    "securities",
    "daily_prices",
    "edinet_documents",
    "annual_financials",
}


# ============================================================
# PostgreSQL接続テスト
# ============================================================

def test_database_connection() -> None:
    """
    PostgreSQLへ接続し、初期スキーマを確認する。
    """

    print("PostgreSQLへの接続テストを開始します。")

    with create_database_connection(
        APPLICATION_NAME
    ) as connection:
        database_information = (
            get_database_information(connection)
        )

        connection_test = (
            database_information[
                "connection_test"
            ]
        )

        if connection_test != 1:
            raise RuntimeError(
                "PostgreSQLの接続確認値が不正です。"
            )

        existing_tables = verify_required_tables(
            connection,
            REQUIRED_TABLES,
        )

    print("PostgreSQLへの接続に成功しました。")

    print(
        "データベース名: "
        f"{database_information['database_name']}"
    )

    print(
        "接続ユーザー: "
        f"{database_information['database_user']}"
    )

    print(
        "PostgreSQLバージョン: "
        f"{database_information['server_version']}"
    )

    print(
        "専用スキーマ: "
        f"{DATABASE_SCHEMA}"
    )

    for table_name in sorted(existing_tables):
        print(
            "確認済みテーブル: "
            f"{DATABASE_SCHEMA}.{table_name}"
        )

    print(
        "共通データベース接続処理の確認が"
        "正常に完了しました。"
    )


# ============================================================
# エントリーポイント
# ============================================================

def main() -> None:
    """
    接続テストを実行する。
    """

    test_database_connection()


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            "PostgreSQLへの接続テストに"
            "失敗しました。",
            file=sys.stderr,
        )

        print(
            f"エラー種別: {type(error).__name__}",
            file=sys.stderr,
        )

        if isinstance(error, psycopg.Error):
            # psycopgの接続例外本文には接続先情報が
            # 含まれる場合があるため、本文は出力しない。
            print(
                "PostgreSQLとの通信中に"
                "エラーが発生しました。",
                file=sys.stderr,
            )
        else:
            print(
                f"エラー内容: {error}",
                file=sys.stderr,
            )

        # DATABASE_URLそのものは出力しない。
        sys.exit(1)
