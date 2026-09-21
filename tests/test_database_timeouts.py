"""PostgreSQL接続タイムアウト設定のテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ============================================================
# テスト対象の読込
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIRECTORY))

from database import (  # noqa: E402
    DATABASE_CONNECT_TIMEOUT_ENV_NAME,
    DATABASE_STATEMENT_TIMEOUT_ENV_NAME,
    DatabaseConfigurationError,
    create_database_connection,
    get_database_connect_timeout_seconds,
    get_database_statement_timeout_milliseconds,
)


# ============================================================
# 環境変数
# ============================================================

class DatabaseTimeoutEnvironmentTests(
    unittest.TestCase
):
    """DBタイムアウト環境変数の検証を確認する。"""

    def test_default_values_are_used(self) -> None:
        with patch.dict(
            os.environ,
            {},
            clear=False,
        ):
            os.environ.pop(
                DATABASE_CONNECT_TIMEOUT_ENV_NAME,
                None,
            )
            os.environ.pop(
                DATABASE_STATEMENT_TIMEOUT_ENV_NAME,
                None,
            )

            self.assertEqual(
                get_database_connect_timeout_seconds(),
                20,
            )
            self.assertEqual(
                (
                    get_database_statement_timeout_milliseconds()
                ),
                0,
            )

    def test_environment_values_are_used(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                DATABASE_CONNECT_TIMEOUT_ENV_NAME: "5",
                DATABASE_STATEMENT_TIMEOUT_ENV_NAME: (
                    "15000"
                ),
            },
            clear=False,
        ):
            self.assertEqual(
                get_database_connect_timeout_seconds(),
                5,
            )
            self.assertEqual(
                (
                    get_database_statement_timeout_milliseconds()
                ),
                15000,
            )

    def test_invalid_connect_timeout_is_rejected(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                DATABASE_CONNECT_TIMEOUT_ENV_NAME: "zero",
            },
            clear=False,
        ):
            with self.assertRaises(
                DatabaseConfigurationError
            ):
                get_database_connect_timeout_seconds()

    def test_out_of_range_statement_timeout_is_rejected(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                DATABASE_STATEMENT_TIMEOUT_ENV_NAME: (
                    "900001"
                ),
            },
            clear=False,
        ):
            with self.assertRaises(
                DatabaseConfigurationError
            ):
                get_database_statement_timeout_milliseconds()


# ============================================================
# PostgreSQL接続
# ============================================================

class DatabaseConnectionTests(unittest.TestCase):
    """DB接続時のタイムアウト適用を確認する。"""

    @patch("database.psycopg.connect")
    def test_worker_timeouts_are_applied(
        self,
        connect_mock,
    ) -> None:
        connection = MagicMock()
        cursor = MagicMock()

        connection.cursor.return_value.__enter__.return_value = (
            cursor
        )
        connect_mock.return_value = connection

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql://user:password@"
                    "example.invalid:6543/postgres"
                ),
                DATABASE_CONNECT_TIMEOUT_ENV_NAME: "5",
                DATABASE_STATEMENT_TIMEOUT_ENV_NAME: (
                    "15000"
                ),
            },
            clear=False,
        ):
            returned_connection = (
                create_database_connection(
                    "discord_candidate_search"
                )
            )

        self.assertIs(
            returned_connection,
            connection,
        )

        connect_arguments = (
            connect_mock.call_args.kwargs
        )

        self.assertEqual(
            connect_arguments["connect_timeout"],
            5,
        )
        self.assertEqual(
            connect_arguments["sslmode"],
            "require",
        )
        self.assertEqual(
            connect_arguments["application_name"],
            "discord_candidate_search",
        )

        cursor.execute.assert_called_once_with(
            """
                SELECT set_config(
                    %s,
                    %s,
                    TRUE
                )
                """,
            (
                "statement_timeout",
                "15000ms",
            ),
        )

    @patch("database.psycopg.connect")
    def test_statement_timeout_can_be_disabled(
        self,
        connect_mock,
    ) -> None:
        connection = MagicMock()
        connect_mock.return_value = connection

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql://user:password@"
                    "example.invalid:6543/postgres"
                ),
                DATABASE_CONNECT_TIMEOUT_ENV_NAME: "20",
                DATABASE_STATEMENT_TIMEOUT_ENV_NAME: "0",
            },
            clear=False,
        ):
            returned_connection = (
                create_database_connection(
                    "scheduled_export"
                )
            )

        self.assertIs(
            returned_connection,
            connection,
        )
        connection.cursor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
