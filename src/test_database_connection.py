"""
GitHub ActionsからSupabase PostgreSQLへ接続できることを
確認するためのテストプログラム。

このプログラムはデータベースへの書き込みを行わず、
SELECT文だけを実行する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import os
import sys


# ============================================================
# 外部ライブラリ
# ============================================================

import psycopg


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
# PostgreSQL接続テスト
# ============================================================

def test_database_connection() -> None:
    """
    Supabase PostgreSQLへ接続し、
    読み取り専用の確認クエリを実行する。
    """

    database_url = get_required_environment_variable(
        "DATABASE_URL"
    )

    print("PostgreSQLへの接続テストを開始します。")

    with psycopg.connect(
        database_url,
        connect_timeout=20,
        sslmode="require",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    1 AS connection_test,
                    current_database() AS database_name,
                    current_user AS database_user,
                    current_setting('server_version') AS server_version
                """
            )

            result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "PostgreSQLからテスト結果を取得できませんでした。"
        )

    connection_test = result[0]
    database_name = result[1]
    database_user = result[2]
    server_version = result[3]

    if connection_test != 1:
        raise RuntimeError(
            "PostgreSQLの接続確認値が不正です。"
        )

    print("PostgreSQLへの接続に成功しました。")
    print(f"データベース名: {database_name}")
    print(f"接続ユーザー: {database_user}")
    print(f"PostgreSQLバージョン: {server_version}")
    print("読み取り専用テストが正常に完了しました。")


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
            "PostgreSQLへの接続テストに失敗しました。",
            file=sys.stderr,
        )

        print(
            f"エラー種別: {type(error).__name__}",
            file=sys.stderr,
        )

        print(
            "DATABASE_URL、データベースパスワード、"
            "Session poolerの接続情報を確認してください。",
            file=sys.stderr,
        )

        # 接続文字列やパスワードがログへ含まれる可能性を
        # 避けるため、例外本文とトレースバックは出力しない。
        sys.exit(1)
