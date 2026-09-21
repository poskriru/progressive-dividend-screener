"""Discord候補検索用Materialized Viewを更新する。"""

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

from database import get_database_url


# ============================================================
# 定数
# ============================================================

CACHE_NAME = (
    "screener.discord_candidate_search_cache"
)

ADVISORY_LOCK_NAME = (
    "refresh-discord-candidate-search-cache"
)


# ============================================================
# キャッシュ更新
# ============================================================

def refresh_candidate_search_cache() -> None:
    """Discord候補検索キャッシュを更新する。"""

    database_url = get_database_url()

    print(
        "Discord候補検索キャッシュの更新を"
        "開始します。",
        flush=True,
    )

    with psycopg.connect(
        database_url,
        connect_timeout=20,
        sslmode="require",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_advisory_lock(
                    hashtext(%s)
                )
                """,
                (ADVISORY_LOCK_NAME,),
            )

            try:
                cursor.execute(
                    f"""
                    REFRESH MATERIALIZED VIEW
                    CONCURRENTLY {CACHE_NAME}
                    """
                )

                cursor.execute(
                    f"""
                    SELECT
                        COUNT(*) AS candidate_count,
                        MAX(trading_date)
                            AS latest_trading_date
                    FROM {CACHE_NAME}
                    """
                )

                result = cursor.fetchone()

                if result is None:
                    raise RuntimeError(
                        "キャッシュ更新結果を"
                        "確認できませんでした。"
                    )

                candidate_count = int(
                    result[0]
                )
                latest_trading_date = result[1]

                print(
                    "Discord候補検索キャッシュを"
                    "更新しました。"
                    f"件数: {candidate_count:,}, "
                    "最新取引日: "
                    f"{latest_trading_date}",
                    flush=True,
                )
            finally:
                cursor.execute(
                    """
                    SELECT pg_advisory_unlock(
                        hashtext(%s)
                    )
                    """,
                    (ADVISORY_LOCK_NAME,),
                )


# ============================================================
# エントリーポイント
# ============================================================

def main() -> None:
    """キャッシュ更新を実行する。"""

    refresh_candidate_search_cache()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "Discord候補検索キャッシュの更新に"
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
        sys.exit(1)
