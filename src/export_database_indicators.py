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
import os
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
    JST,
    create_google_sheets_service,
    get_or_create_sheet,
    get_required_environment_variable,
    write_sheet,
)

from sheets_column_formatting import (
    apply_column_formats,
    to_sheet_serial_value,
)


# ============================================================
# 定数
# ============================================================

DEFAULT_DATABASE_INDICATOR_SHEET_NAME = "株式指標_DB比較"
PRODUCTION_INDICATOR_SHEET_NAME = "株式指標"

INDICATOR_OUTPUT_SHEET_NAME_ENV = (
    "INDICATOR_OUTPUT_SHEET_NAME"
)

ALLOWED_INDICATOR_OUTPUT_SHEET_NAMES = {
    DEFAULT_DATABASE_INDICATOR_SHEET_NAME,
    PRODUCTION_INDICATOR_SHEET_NAME,
}

MILLION = Decimal("1000000")

DATABASE_INDICATOR_HEADERS = [
    "更新日時",
    "株価基準日",
    "証券コード",
    "銘柄名",
    "市場",
    "終値",
    "決算期末日",
    "会計基準",
    "売上高（百万円）",
    "営業利益（百万円）",
    "純利益（百万円）",
    "総資産（百万円）",
    "純資産（百万円）",
    "自己資本（百万円）",
    "EPS（円）",
    "BPS（円）",
    "1株配当（円）",
    "発行済株式数",
    "時価総額（百万円）",
    "PER（倍）",
    "PBR（倍）",
    "ROE（%）",
    "ROA（%）",
    "自己資本比率（%）",
    "営業利益率（%）",
    "純利益率（%）",
    "配当利回り（%）",
    "配当性向（%）",
    "営業CF（百万円）",
    "投資CF（百万円）",
    "フリーCF（百万円）",
    "財務CF（百万円）",
    "書類管理番号",
    "EDINET閲覧URL",
    "配当履歴期数",
    "判定対象配当期数",
    "最新年間配当（円）",
    "前期年間配当（円）",
    "5期最古年間配当（円）",
    "5期増配回数",
    "5期据え置き回数",
    "5期減配回数",
    "連続非減配期数",
    "連続増配期数",
    "5期配当CAGR（%）",
    "5期累進配当判定",
    "累進配当判定状態",
    "5期配当履歴",
    "株式分割等未調整",
    "配当補正状態",
    "補正データ範囲充足",
    "補正データ開始日",
    "補正データ終了日",
    "最新累積補正係数",
    "5期最古累積補正係数",
    "最新調整済み年間配当（円）",
    "5期最古調整済み年間配当（円）",
    "5期調整済み配当CAGR（%）",
    "5期調整済み累進配当判定",
    "調整済み累進配当判定状態",
    "5期調整済み配当履歴",
]

# 列名→表示書式パターン。
# 日付・日時はシリアル値で書き込み、
# シート側でこれらの書式により表示する。
INDICATOR_COLUMN_FORMATS_BY_HEADER: dict[str, str] = {
    "更新日時": "yyyy-mm-dd hh:mm:ss",
    "株価基準日": "yyyy-mm-dd",
    "決算期末日": "yyyy-mm-dd",
    "補正データ開始日": "yyyy-mm-dd",
    "補正データ終了日": "yyyy-mm-dd",
    "終値": "0.##",
    "売上高（百万円）": "#,##0.0",
    "営業利益（百万円）": "#,##0.0",
    "純利益（百万円）": "#,##0.0",
    "総資産（百万円）": "#,##0.0",
    "純資産（百万円）": "#,##0.0",
    "自己資本（百万円）": "#,##0.0",
    "EPS（円）": "0.##",
    "BPS（円）": "0.##",
    "1株配当（円）": "0.##",
    "発行済株式数": "#,##0",
    "時価総額（百万円）": "#,##0.0",
    "PER（倍）": "0.##",
    "PBR（倍）": "0.##",
    "ROE（%）": "0.00",
    "ROA（%）": "0.00",
    "自己資本比率（%）": "0.00",
    "営業利益率（%）": "0.00",
    "純利益率（%）": "0.00",
    "配当利回り（%）": "0.00",
    "配当性向（%）": "0.00",
    "営業CF（百万円）": "#,##0.0",
    "投資CF（百万円）": "#,##0.0",
    "フリーCF（百万円）": "#,##0.0",
    "財務CF（百万円）": "#,##0.0",
    "配当履歴期数": "#,##0",
    "判定対象配当期数": "#,##0",
    "最新年間配当（円）": "0.##",
    "前期年間配当（円）": "0.##",
    "5期最古年間配当（円）": "0.##",
    "5期増配回数": "#,##0",
    "5期据え置き回数": "#,##0",
    "5期減配回数": "#,##0",
    "連続非減配期数": "#,##0",
    "連続増配期数": "#,##0",
    "5期配当CAGR（%）": "0.00",
    "最新累積補正係数": "0.########",
    "5期最古累積補正係数": "0.########",
    "最新調整済み年間配当（円）": "0.##",
    "5期最古調整済み年間配当（円）": "0.##",
    "5期調整済み配当CAGR（%）": "0.00",
}


def build_indicator_column_formats() -> (
    dict[int, str]
):
    """ヘッダー定義から列番号→表示書式の辞書を作る。"""

    return {
        index: format_pattern
        for index, header in enumerate(
            DATABASE_INDICATOR_HEADERS
        )
        if (
            format_pattern
            := INDICATOR_COLUMN_FORMATS_BY_HEADER.get(
                header
            )
        )
    }


# ============================================================
# 出力先シート名
# ============================================================

def get_indicator_output_sheet_name() -> str:
    """
    PostgreSQL版株式指標の出力先シート名を取得する。

    環境変数が未設定の場合は、既存シートを保護するため
    比較用シートへ出力する。
    """

    sheet_name = os.getenv(
        INDICATOR_OUTPUT_SHEET_NAME_ENV,
        DEFAULT_DATABASE_INDICATOR_SHEET_NAME,
    ).strip()

    if not sheet_name:
        sheet_name = (
            DEFAULT_DATABASE_INDICATOR_SHEET_NAME
        )

    if (
        sheet_name
        not in ALLOWED_INDICATOR_OUTPUT_SHEET_NAMES
    ):
        raise RuntimeError(
            "株式指標の出力先シート名が不正です。"
            f"指定値: {sheet_name}, "
            "許可値: "
            f"{sorted(ALLOWED_INDICATOR_OUTPUT_SHEET_NAMES)}"
        )

    return sheet_name


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
# 配当履歴変換
# ============================================================

def to_sheet_boolean(
    value: Any,
) -> bool | str:
    """
    PostgreSQLのbooleanをGoogle Sheets用に変換する。

    NULLは空文字にし、判定不能とFALSEを区別する。
    """

    if value is None:
        return ""

    return bool(value)


def format_dividend_history(
    fiscal_periods: Any,
    annual_dividends: Any,
) -> str:
    """
    決算期末日と年間配当の配列を表示用文字列へ変換する。
    """

    if not isinstance(
        fiscal_periods,
        (list, tuple),
    ):
        return ""

    if not isinstance(
        annual_dividends,
        (list, tuple),
    ):
        return ""

    if len(fiscal_periods) != len(
        annual_dividends
    ):
        raise RuntimeError(
            "配当履歴の日付数と配当数が一致しません。"
            f"日付数: {len(fiscal_periods)}, "
            f"配当数: {len(annual_dividends)}"
        )

    history_items: list[str] = []

    for fiscal_period, annual_dividend in zip(
        fiscal_periods,
        annual_dividends,
        strict=True,
    ):
        fiscal_period_text = to_sheet_date(
            fiscal_period
        )

        dividend_value = to_sheet_number(
            annual_dividend
        )

        if dividend_value == "":
            dividend_text = "欠損"
        else:
            dividend_text = str(
                dividend_value
            )

        history_items.append(
            f"{fiscal_period_text}:{dividend_text}"
        )

    return " / ".join(
        history_items
    )


# ============================================================
# PostgreSQLから株式指標を取得
# ============================================================

def load_database_indicators() -> list[dict[str, Any]]:
    """
    company_screener_with_dividendsから、
    株価・財務情報・累進配当指標を取得する。
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
            financial_source_url,
            available_history_period_count,
            dividend_period_count,
            dividend_latest_annual_dividend_yen,
            previous_annual_dividend_yen,
            oldest_annual_dividend_yen_5y,
            dividend_increase_count_5y,
            dividend_unchanged_count_5y,
            dividend_cut_count_5y,
            consecutive_non_decrease_periods,
            consecutive_increase_periods,
            dividend_cagr_5y_percent,
            is_progressive_dividend_5y_raw,
            progressive_dividend_status_5y,
            fiscal_periods_5y,
            annual_dividends_yen_5y,
            dividend_adjustment_status,
            is_adjustment_coverage_complete,
            adjustment_covered_from,
            adjustment_covered_to,
            latest_cumulative_adjustment_factor,
            oldest_cumulative_adjustment_factor_5y,
            latest_adjusted_annual_dividend_yen,
            oldest_adjusted_annual_dividend_yen_5y,
            dividend_cagr_5y_adjusted_percent,
            is_progressive_dividend_5y_adjusted,
            progressive_dividend_status_5y_adjusted,
            adjusted_fiscal_periods_5y,
            adjusted_annual_dividends_yen_5y
        FROM screener.company_screener_with_dividends
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
            "company_screener_with_dividendsに"
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
            "company_screener_with_dividendsに"
            "証券コードの重複があります。"
        )

    print(
        "PostgreSQLから株式指標と"
        "累進配当指標を取得しました。"
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
    Google Sheets出力用の列順へ変換する。
    """

    updated_at = to_sheet_serial_value(
        datetime.now(JST)
    )

    rows: list[list[Any]] = []

    for record in records:
        rows.append(
            [
                updated_at,
                to_sheet_serial_value(
                    record.get("trading_date")
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
                    record.get("close_price")
                ),
                to_sheet_serial_value(
                    record.get("fiscal_period_end")
                ),
                str(
                    record.get(
                        "accounting_standard",
                        "",
                    )
                    or ""
                ),
                yen_to_sheet_million(
                    record.get("revenue_jpy")
                ),
                yen_to_sheet_million(
                    record.get(
                        "operating_profit_jpy"
                    )
                ),
                yen_to_sheet_million(
                    record.get("net_income_jpy")
                ),
                yen_to_sheet_million(
                    record.get("total_assets_jpy")
                ),
                yen_to_sheet_million(
                    record.get("net_assets_jpy")
                ),
                yen_to_sheet_million(
                    record.get("equity_jpy")
                ),
                to_sheet_number(
                    record.get("eps_yen")
                ),
                to_sheet_number(
                    record.get("bps_yen")
                ),
                to_sheet_number(
                    record.get(
                        "annual_dividend_yen"
                    )
                ),
                to_sheet_integer(
                    record.get("issued_shares")
                ),
                to_sheet_number(
                    record.get(
                        "market_capitalization_million_yen"
                    )
                ),
                to_sheet_number(
                    record.get("per_ratio")
                ),
                to_sheet_number(
                    record.get("pbr_ratio")
                ),
                to_sheet_number(
                    record.get("roe_percent")
                ),
                to_sheet_number(
                    record.get("roa_percent")
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
                    record.get("free_cash_flow_jpy")
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
                to_sheet_integer(
                    record.get(
                        "available_history_period_count"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "dividend_period_count"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "dividend_latest_annual_dividend_yen"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "previous_annual_dividend_yen"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "oldest_annual_dividend_yen_5y"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "dividend_increase_count_5y"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "dividend_unchanged_count_5y"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "dividend_cut_count_5y"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "consecutive_non_decrease_periods"
                    )
                ),
                to_sheet_integer(
                    record.get(
                        "consecutive_increase_periods"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "dividend_cagr_5y_percent"
                    )
                ),
                to_sheet_boolean(
                    record.get(
                        "is_progressive_dividend_5y_raw"
                    )
                ),
                str(
                    record.get(
                        "progressive_dividend_status_5y",
                        "",
                    )
                    or ""
                ),
                format_dividend_history(
                    record.get("fiscal_periods_5y"),
                    record.get(
                        "annual_dividends_yen_5y"
                    ),
                ),
                "未調整",
                str(
                    record.get(
                        "dividend_adjustment_status",
                        "",
                    )
                    or ""
                ),
                to_sheet_boolean(
                    record.get(
                        "is_adjustment_coverage_complete"
                    )
                ),
                to_sheet_serial_value(
                    record.get("adjustment_covered_from")
                ),
                to_sheet_serial_value(
                    record.get("adjustment_covered_to")
                ),
                to_sheet_number(
                    record.get(
                        "latest_cumulative_adjustment_factor"
                    ),
                    digits=10,
                ),
                to_sheet_number(
                    record.get(
                        "oldest_cumulative_adjustment_factor_5y"
                    ),
                    digits=10,
                ),
                to_sheet_number(
                    record.get(
                        "latest_adjusted_annual_dividend_yen"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "oldest_adjusted_annual_dividend_yen_5y"
                    )
                ),
                to_sheet_number(
                    record.get(
                        "dividend_cagr_5y_adjusted_percent"
                    )
                ),
                to_sheet_boolean(
                    record.get(
                        "is_progressive_dividend_5y_adjusted"
                    )
                ),
                str(
                    record.get(
                        "progressive_dividend_status_5y_adjusted",
                        "",
                    )
                    or ""
                ),
                format_dividend_history(
                    record.get("adjusted_fiscal_periods_5y"),
                    record.get(
                        "adjusted_annual_dividends_yen_5y"
                    ),
                ),
            ]
        )

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        if len(row) != len(
            DATABASE_INDICATOR_HEADERS
        ):
            raise RuntimeError(
                "株式指標の列数が一致しません。"
                f"行: {row_number}, "
                "期待列数: "
                f"{len(DATABASE_INDICATOR_HEADERS)}, "
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
    PostgreSQLの株式指標を指定されたシートへ出力する。
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

    output_sheet_name = (
        get_indicator_output_sheet_name()
    )

    print(
        "株式指標の出力先を確認しました。"
        f"シート: {output_sheet_name}"
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
        output_sheet_name,
        DATABASE_INDICATOR_HEADERS,
        indicator_rows,
    )

    sheet_id = get_or_create_sheet(
        sheets_service,
        spreadsheet_id,
        output_sheet_name,
    )

    apply_column_formats(
        sheets_service,
        spreadsheet_id,
        sheet_id,
        build_indicator_column_formats(),
    )

    print(
        "PostgreSQL版株式指標の"
        "出力が完了しました。"
        f"シート: {output_sheet_name}, "
        f"件数: {len(indicator_rows):,}"
    )

    if output_sheet_name == PRODUCTION_INDICATOR_SHEET_NAME:
        # 本番株式指標と同じデータ更新タイミングで、
        # 条件に合う候補シートも一貫して更新する。
        from export_progressive_dividend_candidates import (
            main as export_progressive_dividend_candidates,
        )

        export_progressive_dividend_candidates()


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
