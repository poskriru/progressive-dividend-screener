"""
自動配当補正の対象外となった企業行動を抽出し、
ライツイシュー等の影響確認用シートへ出力する。

ライツイシュー（ExRT=3）や種別不明の非1係数は、
発行条件が銘柄ごとに異なるため自動補正を行わない。
本処理は補正せず、一次資料で確認すべき銘柄を提示する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import traceback
from datetime import datetime
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from export_database_indicators import (
    to_sheet_date,
    to_sheet_number,
)

from update_edinet_financials import (
    JST,
    create_google_sheets_service,
    get_required_environment_variable,
    write_sheet,
)


# ============================================================
# 定数
# ============================================================

UNSUPPORTED_CORPORATE_ACTION_SHEET_NAME = (
    "ライツイシュー等確認対象"
)

UNSUPPORTED_CORPORATE_ACTION_HEADERS = [
    "更新日時",
    "証券コード",
    "銘柄名",
    "市場",
    "権利落ち日",
    "種別",
    "調整係数",
    "出典",
    "補正状態",
    "確認事項",
]

UNSUPPORTED_ACTION_CAUTION = (
    "自動配当補正の対象外です。ライツイシュー・第三者割当等は"
    "発行条件により1株価値への影響が異なるため、"
    "有価証券報告書・適時開示・会社IRの一次資料で"
    "配当継続性を確認してください。"
)

RIGHTS_ISSUE_EX_RIGHT_TYPE = "3"


# ============================================================
# 分類
# ============================================================

def classify_ex_right_type(
    ex_right_type: Any,
) -> str:
    """ExRTから確認対象の種別を返す。"""

    if ex_right_type == RIGHTS_ISSUE_EX_RIGHT_TYPE:
        return "ライツイシュー"

    if ex_right_type is None:
        return "種別不明"

    raise RuntimeError(
        "確認対象外のExRTです。"
        f"指定値: {ex_right_type}"
    )


def map_adjustment_status(
    dividend_adjustment_status: Any,
) -> str:
    """補正状態を表示用ラベルへ変換する。"""

    status_labels = {
        "unsupported_corporate_action": (
            "自動補正対象外"
        ),
        "adjustment_data_incomplete": (
            "補正範囲不足"
        ),
        "complete": (
            "補正完了"
        ),
    }

    if dividend_adjustment_status is None:
        return "判定データなし"

    status = str(dividend_adjustment_status)

    if status not in status_labels:
        raise RuntimeError(
            "未知の補正状態です。"
            f"指定値: {status}"
        )

    return status_labels[status]


# ============================================================
# PostgreSQLから確認対象を取得
# ============================================================

def load_unsupported_corporate_actions() -> list[
    dict[str, Any]
]:
    """自動補正対象外の企業行動を取得する。"""

    query = """
        SELECT
            actions.security_code,
            securities.company_name,
            securities.market,
            actions.effective_date,
            actions.ex_right_type,
            actions.adjustment_factor,
            actions.source,
            adjusted.dividend_adjustment_status
        FROM screener.corporate_actions AS actions
        INNER JOIN screener.securities AS securities
            ON securities.security_code
                = actions.security_code
        LEFT JOIN
            screener.company_dividend_metrics_adjusted
                AS adjusted
            ON adjusted.security_code
                = actions.security_code
        WHERE securities.is_active = TRUE
          AND actions.ex_right_type
                IS DISTINCT FROM '1'
          AND actions.ex_right_type
                IS DISTINCT FROM '2'
          AND actions.adjustment_factor <> 1
        ORDER BY
            actions.effective_date DESC,
            actions.security_code;
    """

    with create_database_connection(
        "export_unsupported_corporate_actions"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            records = [
                dict(row)
                for row in cursor.fetchall()
            ]

    print(
        "自動補正対象外の企業行動を取得しました。"
        f"件数: {len(records):,}"
    )

    return records


# ============================================================
# Google Sheets出力行作成
# ============================================================

def build_unsupported_corporate_action_rows(
    records: list[dict[str, Any]],
) -> list[list[Any]]:
    """確認対象をGoogle Sheetsの列順へ変換する。"""

    updated_at = datetime.now(JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    rows: list[list[Any]] = []

    for record in records:
        row = [
            updated_at,
            str(record.get("security_code", "")),
            str(record.get("company_name", "") or ""),
            str(record.get("market", "") or ""),
            to_sheet_date(
                record.get("effective_date")
            ),
            classify_ex_right_type(
                record.get("ex_right_type")
            ),
            to_sheet_number(
                record.get("adjustment_factor"),
                digits=6,
            ),
            str(record.get("source", "") or ""),
            map_adjustment_status(
                record.get("dividend_adjustment_status")
            ),
            UNSUPPORTED_ACTION_CAUTION,
        ]

        if len(row) != len(
            UNSUPPORTED_CORPORATE_ACTION_HEADERS
        ):
            raise RuntimeError(
                "ライツイシュー等確認対象の列数が"
                "一致しません。"
                f"証券コード: {row[1]}, "
                "期待列数: "
                f"{len(UNSUPPORTED_CORPORATE_ACTION_HEADERS)}, "
                f"実際の列数: {len(row)}"
            )

        rows.append(row)

    print(
        "Google Sheets出力用のライツイシュー等確認対象を"
        f"作成しました。件数: {len(rows):,}"
    )

    return rows


# ============================================================
# 出力処理
# ============================================================

def export_unsupported_corporate_actions(
    sheets_service,
    spreadsheet_id: str,
) -> int:
    """確認対象を専用シートへ出力して件数を返す。"""

    records = load_unsupported_corporate_actions()
    rows = build_unsupported_corporate_action_rows(
        records
    )

    write_sheet(
        sheets_service,
        spreadsheet_id,
        UNSUPPORTED_CORPORATE_ACTION_SHEET_NAME,
        UNSUPPORTED_CORPORATE_ACTION_HEADERS,
        rows,
    )

    print(
        "ライツイシュー等確認対象の出力が完了しました。"
        f"シート: {UNSUPPORTED_CORPORATE_ACTION_SHEET_NAME}, "
        f"件数: {len(rows):,}"
    )

    return len(rows)


def main() -> None:
    """認証情報を取得し、確認対象シートを更新する。"""

    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )
    service_account_json = get_required_environment_variable(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )
    sheets_service = create_google_sheets_service(
        service_account_json
    )

    export_unsupported_corporate_actions(
        sheets_service,
        spreadsheet_id,
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "ライツイシュー等確認対象の出力中に"
            "エラーが発生しました。",
            file=sys.stderr,
        )
        traceback.print_exc()
        print(
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)