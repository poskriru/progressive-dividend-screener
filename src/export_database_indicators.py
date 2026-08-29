"""
PostgreSQLのcompany_screener_base VIEWから
株式指標を取得し、比較用Google Sheetsへ出力する。

既存の「株式指標」シートは変更せず、
「株式指標_DB比較」シートへ出力する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import math
import sys
import traceback
from datetime import date, datetime
from decimal import Decimal
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from database import create_database_connection

from update_edinet_financials import (
    INDICATOR_HEADERS,
    JST,
    create_google_sheets_service,
    get_required_environment_variable,
    write_sheet,
)


# ============================================================
# 定数
# ============================================================

DATABASE_INDICATOR_SHEET_NAME = "株式指標_DB比較"

MILLION = Decimal("1000000")


# ============================================================
# 数値変換
# ============================================================

def to_decimal(
    value: Any,
) -> Decimal | None:
    """
    PostgreSQLの数値をDecimalへ変換する。
    """

    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(
            str(value)
        )

    except Exception as error:
        raise RuntimeError(
            "数値をDecimalへ変換できませんでした。"
            f"値: {value}"
        ) from error


def to_sheet_number(
    value: Any,
    *,
    digits: int = 2,
) -> float | str:
    """
    数値をGoogle Sheetsへ書き込めるfloatへ変換する。
    NULL、NaN、Infinityは空文字にする。
    """

    number = to_decimal(
        value
    )

    if number is None:
        return ""

    if not number.is_finite():
        return ""

    rounded_value = round(
        float(number),
        digits,
    )

    if not math.isfinite(
        rounded_value
    ):
        return ""

    return rounded_value


def yen_to_sheet_million(
    value: Any,
    *,
    digits: int = 2,
) -> float | str:
    """
    円単位の値を百万円単位へ変換する。
    """

    number = to_decimal(
        value
    )

    if number is None:
        return ""

    return round(
        float(number / MILLION),
        digits,
    )


def to_sheet_integer(
    value: Any,
) -> int | str:
    """
    整数をGoogle Sheets用に変換する。
    """

    number = to_decimal(
        value
    )

    if number is None:
        return ""

    return int(
        number
    )


def to_sheet_date(
    value: Any,
) -> str:
    """
    日付をISO形式へ変換する。
    """

    if value is None:
        return ""

    if isinstance(
        value,
        datetime,
    ):
        return value.date().isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    return str(value)


# ============================================================
# PostgreSQLから株式指標を取得
# ============================================================

def load_database_indicators() -> list[dict[str, Any]]:
    """
    company_screener_baseから、
    株価と財務情報がそろっている銘柄を取得する。
    """

    query = """
        SELECT
            security_code,
            company_name,
            market,
            trading_date,
            close_price,
            fiscal_period_end,
            accounting_standard,
            revenue_jpy,
            operating_profit_jpy,
            net_income_jpy,
            total_assets_jpy,
            net_assets_jpy,
            equity_jpy,
            eps_yen,
            bps_yen,
            annual_dividend_yen,
            issued_shares,
            market_capitalization_million_yen,
            per_ratio,
            pbr_ratio,
            roe_percent,
            roa_percent,
            equity_ratio_percent,
            operating_profit_margin_percent,
            net_income_margin_percent,
            dividend_yield_percent,
            payout_ratio_percent,
            operating_cash_flow_jpy,
            investing_cash_flow_jpy,
            free_cash_flow_jpy,
            financing_cash_flow_jpy,
            doc_id,
            financial_source_url
        FROM screener.company_screener_base
        WHERE annual_financial_id IS NOT NULL
          AND close_price IS NOT NULL
        ORDER BY security_code;
    """

    with create_database_connection(
        "export_database_indicators"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query
            )

            records = [
                dict(row)
                for row in cursor.fetchall()
            ]

    if not records:
        raise RuntimeError(
            "company_screener_baseに"
            "出力対象データがありません。"
        )

    security_codes = [
        str(record["security_code"])
        for record in records
    ]

    if len(security_codes) != len(
        set(security_codes)
    ):
        raise RuntimeError(
            "company_screener_baseに"
            "証券コードの重複があります。"
        )

    print(
        "PostgreSQLから株式指標を"
        "取得しました。"
        f"件数: {len(records):,}"
    )

    return records


# ============================================================
# Google Sheets出力行作成
# ============================================================

def build_indicator_rows(
    records: list[dict[str, Any]],
) -> list[list[Any]]:
    """
    PostgreSQLのレコードを、
    既存の株式指標と同じ列順へ変換する。
    """

    updated_at = datetime.now(
        JST
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    rows: list[list[Any]] = []

    for record in records:
        rows.append(
            [
                updated_at,
                to_sheet_date(
                    record.get(
                        "trading_date"
                    )
                ),
                str(
                    record.get(
                        "security_code",
                        "",
                    )
                ),
                str(
                    record.get(
                        "company_name",
                        "",
                    )
                ),
                str(
                    record.get(
                        "market",
                        "",
                    )
                    or ""
                ),
                to_sheet_number(
                    record.get(
                        "close_price"
                    )
                ),
                to_sheet_date(
                    record.get(
                        "fiscal_period_end"
                    )
                ),
                str(
                    record.get(
                        "accounting_standard",
                        "",
                    )
                    or ""
                ),
                yen_to_sheet_million(
                    record.get(
                        "revenue_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "operating_profit_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "net_income_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "total_assets_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "net_assets_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "equity_jpy"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "eps_yen"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "bps_yen"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "annual_dividend_yen"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "issued_shares"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "market_capitalization_million_yen"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "per_ratio"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "pbr_ratio"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "roe_percent"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "roa_percent"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "equity_ratio_percent"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "operating_profit_margin_percent"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "net_income_margin_percent"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "dividend_yield_percent"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "payout_ratio_percent"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "operating_cash_flow_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "investing_cash_flow_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "free_cash_flow_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get(
                        "financing_cash_flow_jpy"
                    )
                ),
                str(
                    record.get(
                        "doc_id",
                        "",
                    )
                    or ""
                ),
                str(
                    record.get(
                        "financial_source_url",
                        "",
                    )
                    or ""
                ),
            ]
        )

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        if len(row) != len(
            INDICATOR_HEADERS
        ):
            raise RuntimeError(
                "株式指標の列数が一致しません。"
                f"行: {row_number}, "
                f"期待列数: {len(INDICATOR_HEADERS)}, "
                f"実際の列数: {len(row)}"
            )

    print(
        "Google Sheets出力用の"
        "株式指標を作成しました。"
        f"件数: {len(rows):,}"
    )

    return rows


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    PostgreSQLの株式指標を比較用シートへ出力する。
    """

    spreadsheet_id = (
        get_required_environment_variable(
            "GOOGLE_SPREADSHEET_ID"
        )
    )

    service_account_json = (
        get_required_environment_variable(
            "GOOGLE_SERVICE_ACCOUNT_JSON"
        )
    )

    sheets_service = (
        create_google_sheets_service(
            service_account_json
        )
    )

    records = load_database_indicators()

    indicator_rows = build_indicator_rows(
        records
    )

    write_sheet(
        sheets_service,
        spreadsheet_id,
        DATABASE_INDICATOR_SHEET_NAME,
        INDICATOR_HEADERS,
        indicator_rows,
    )

    print(
        "PostgreSQL版株式指標の"
        "比較用出力が完了しました。"
        f"シート: {DATABASE_INDICATOR_SHEET_NAME}, "
        f"件数: {len(indicator_rows):,}"
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            "PostgreSQL版株式指標の"
            "出力中にエラーが発生しました。",
            file=sys.stderr,
        )

        traceback.print_exc()

        print(
            (
                f"{type(error).__name__}: "
                f"{error}"
            ),
            file=sys.stderr,
        )

        sys.exit(1)
