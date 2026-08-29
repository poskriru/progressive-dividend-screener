"""
Googleスプレッドシートの既存EDINET財務データを、
PostgreSQLのscreener.annual_financialsへ移行する。

金額列はGoogleスプレッドシート上では百万円単位のため、
PostgreSQLへ保存するときに円単位へ変換する。

書類管理番号（doc_id）を一意キーとしてUPSERTし、
繰り返し実行しても重複しない。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import json
import math
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


# ============================================================
# 外部ライブラリ
# ============================================================

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from database import create_database_connection


# ============================================================
# 定数
# ============================================================

FINANCIAL_SHEET_NAME = "EDINET財務"

GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

JST = timezone(
    timedelta(hours=9)
)

DATABASE_SOURCE_NAME = "EDINET"

MILLION = Decimal("1000000")

REQUIRED_HEADERS = [
    "取得日時",
    "提出日時",
    "対象期間開始日",
    "対象期間終了日",
    "証券コード",
    "書類管理番号",
    "会計基準",
    "売上高（百万円）",
    "営業利益（百万円）",
    "経常利益（百万円）",
    "親会社株主帰属利益（百万円）",
    "総資産（百万円）",
    "純資産（百万円）",
    "自己資本（百万円）",
    "営業CF（百万円）",
    "投資CF（百万円）",
    "財務CF（百万円）",
    "現金及び現金同等物（百万円）",
    "EPS（円）",
    "BPS（円）",
    "1株配当（円）",
    "発行済株式数",
    "抽出項目数",
    "抽出状態",
    "抽出エラー",
    "売上高要素ID",
    "営業利益要素ID",
    "純利益要素ID",
    "配当要素ID",
    "EDINET閲覧URL",
]


# ============================================================
# 環境変数
# ============================================================

def get_required_environment_variable(
    name: str,
) -> str:
    """
    必須環境変数を取得する。
    """

    value = os.getenv(
        name,
        "",
    ).strip()

    if not value:
        raise RuntimeError(
            f"必須環境変数が設定されていません: {name}"
        )

    return value


# ============================================================
# 値の正規化
# ============================================================

def normalize_text(
    value: Any,
) -> str:
    """
    値を前後空白のない文字列へ変換する。
    """

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


def normalize_security_code(
    value: Any,
) -> str:
    """
    証券コードを4桁形式へ正規化する。
    """

    text = normalize_text(
        value
    ).upper()

    if text.endswith(".0"):
        text = text[:-2]

    text = "".join(
        character
        for character in text
        if character.isalnum()
    )

    if len(text) == 5 and text.endswith("0"):
        text = text[:4]

    return text


def parse_decimal(
    value: Any,
) -> Decimal | None:
    """
    数値をDecimalへ変換する。
    """

    text = normalize_text(
        value
    )

    if not text:
        return None

    if text in {
        "-",
        "－",
        "―",
        "—",
        "–",
        "N/A",
        "NA",
        "nan",
        "NaN",
        "null",
        "None",
    }:
        return None

    negative = False

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    normalized_text = (
        text
        .replace(",", "")
        .replace("△", "-")
        .replace("▲", "-")
        .replace("−", "-")
        .replace("－", "-")
        .replace("円", "")
        .replace("株", "")
        .replace("%", "")
        .strip()
    )

    try:
        number = Decimal(
            normalized_text
        )

    except InvalidOperation as error:
        raise RuntimeError(
            "数値を解析できませんでした。"
            f"値: {text}"
        ) from error

    if not number.is_finite():
        return None

    if negative and number > 0:
        number = -number

    return number


def parse_million_yen(
    value: Any,
) -> Decimal | None:
    """
    百万円単位の値を円単位へ変換する。
    """

    number = parse_decimal(
        value
    )

    if number is None:
        return None

    return number * MILLION


def parse_integer(
    value: Any,
) -> int | None:
    """
    整数値を取得する。
    """

    number = parse_decimal(
        value
    )

    if number is None:
        return None

    if number != number.to_integral_value():
        raise RuntimeError(
            "整数であるべき値に小数があります。"
            f"値: {value}"
        )

    integer_value = int(number)

    if integer_value < 0:
        raise RuntimeError(
            "負の整数は保存できません。"
            f"値: {value}"
        )

    return integer_value


def parse_optional_date(
    value: Any,
) -> date | None:
    """
    日付を解析する。
    """

    text = normalize_text(
        value
    )

    if not text:
        return None

    normalized_text = (
        text[:10]
        .replace("/", "-")
        .replace(".", "-")
    )

    try:
        return date.fromisoformat(
            normalized_text
        )

    except ValueError as error:
        raise RuntimeError(
            "日付を解析できませんでした。"
            f"値: {text}"
        ) from error


def parse_required_date(
    value: Any,
    *,
    doc_id: str,
) -> date:
    """
    必須の日付を解析する。
    """

    parsed_value = parse_optional_date(
        value
    )

    if parsed_value is None:
        raise RuntimeError(
            "対象期間終了日が空です。"
            f"doc_id: {doc_id}"
        )

    return parsed_value


def parse_datetime(
    value: Any,
) -> datetime | None:
    """
    日時を解析する。

    タイムゾーンのない日時は日本時間として扱う。
    """

    text = normalize_text(
        value
    )

    if not text:
        return None

    normalized_text = (
        text
        .replace("/", "-")
        .replace("Z", "+00:00")
    )

    try:
        parsed_value = datetime.fromisoformat(
            normalized_text
        )

    except ValueError as error:
        raise RuntimeError(
            "日時を解析できませんでした。"
            f"値: {text}"
        ) from error

    if parsed_value.tzinfo is None:
        parsed_value = parsed_value.replace(
            tzinfo=JST
        )

    return parsed_value


# ============================================================
# Google Sheets
# ============================================================

def create_google_sheets_service(
    service_account_json: str,
):
    """
    Google Sheets APIクライアントを作成する。
    """

    try:
        service_account_info = json.loads(
            service_account_json
        )

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSONを"
            "JSONとして解析できませんでした。"
        ) from error

    credentials = (
        Credentials.from_service_account_info(
            service_account_info,
            scopes=GOOGLE_SHEETS_SCOPES,
        )
    )

    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )


def read_financial_sheet(
    service,
    spreadsheet_id: str,
) -> list[dict[str, Any]]:
    """
    EDINET財務シートを辞書形式で読み込む。
    """

    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{FINANCIAL_SHEET_NAME}'",
        )
        .execute()
    )

    values = response.get(
        "values",
        [],
    )

    if len(values) < 2:
        raise RuntimeError(
            "EDINET財務シートに"
            "移行対象データがありません。"
        )

    headers = [
        normalize_text(header)
        for header in values[0]
    ]

    missing_headers = [
        header
        for header in REQUIRED_HEADERS
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            "EDINET財務シートに必要な列が"
            "ありません。"
            f"不足列: {missing_headers}"
        )

    rows: list[dict[str, Any]] = []

    for sheet_row_number, original_row in enumerate(
        values[1:],
        start=2,
    ):
        if not original_row:
            continue

        padded_row = list(
            original_row
        )

        if len(padded_row) < len(headers):
            padded_row.extend(
                [""] * (
                    len(headers)
                    - len(padded_row)
                )
            )

        row = dict(
            zip(
                headers,
                padded_row,
            )
        )

        row["_sheet_row_number"] = (
            sheet_row_number
        )

        rows.append(row)

    if not rows:
        raise RuntimeError(
            "EDINET財務シートに"
            "有効なデータ行がありません。"
        )

    print(
        "EDINET財務シートを読み込みました。"
        f"件数: {len(rows):,}"
    )

    return rows


# ============================================================
# PostgreSQLレコード作成
# ============================================================

def build_financial_database_records(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    シート行をannual_financials保存用レコードへ変換する。
    """

    records: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()

    for row in rows:
        sheet_row_number = int(
            row["_sheet_row_number"]
        )

        doc_id = normalize_text(
            row.get(
                "書類管理番号"
            )
        )

        if not doc_id:
            raise RuntimeError(
                "書類管理番号が空です。"
                f"シート行: {sheet_row_number}"
            )

        if doc_id in seen_doc_ids:
            raise RuntimeError(
                "書類管理番号が重複しています。"
                f"doc_id: {doc_id}, "
                f"シート行: {sheet_row_number}"
            )

        seen_doc_ids.add(
            doc_id
        )

        security_code = normalize_security_code(
            row.get(
                "証券コード"
            )
        )

        if not security_code:
            raise RuntimeError(
                "証券コードが空です。"
                f"doc_id: {doc_id}"
            )

        extracted_item_count = parse_integer(
            row.get(
                "抽出項目数"
            )
        )

        extraction_status = normalize_text(
            row.get(
                "抽出状態"
            )
        ) or "不明"

        extraction_error = normalize_text(
            row.get(
                "抽出エラー"
            )
        )

        processed_at = (
            parse_datetime(
                row.get(
                    "取得日時"
                )
            )
            or datetime.now(JST)
        )

        processing_status = "完了"

        if (
            extracted_item_count in {
                None,
                0,
            }
            and extraction_error
        ):
            processing_status = "失敗"

        records.append(
            {
                "security_code": security_code,
                "doc_id": doc_id,
                "fiscal_period_start": (
                    parse_optional_date(
                        row.get(
                            "対象期間開始日"
                        )
                    )
                ),
                "fiscal_period_end": (
                    parse_required_date(
                        row.get(
                            "対象期間終了日"
                        ),
                        doc_id=doc_id,
                    )
                ),
                "accounting_standard": (
                    normalize_text(
                        row.get(
                            "会計基準"
                        )
                    )
                    or None
                ),
                "is_consolidated": None,
                "revenue_jpy": parse_million_yen(
                    row.get(
                        "売上高（百万円）"
                    )
                ),
                "operating_profit_jpy": (
                    parse_million_yen(
                        row.get(
                            "営業利益（百万円）"
                        )
                    )
                ),
                "ordinary_profit_jpy": (
                    parse_million_yen(
                        row.get(
                            "経常利益（百万円）"
                        )
                    )
                ),
                "net_income_jpy": (
                    parse_million_yen(
                        row.get(
                            "親会社株主帰属利益（百万円）"
                        )
                    )
                ),
                "total_assets_jpy": (
                    parse_million_yen(
                        row.get(
                            "総資産（百万円）"
                        )
                    )
                ),
                "net_assets_jpy": (
                    parse_million_yen(
                        row.get(
                            "純資産（百万円）"
                        )
                    )
                ),
                "equity_jpy": parse_million_yen(
                    row.get(
                        "自己資本（百万円）"
                    )
                ),
                "operating_cash_flow_jpy": (
                    parse_million_yen(
                        row.get(
                            "営業CF（百万円）"
                        )
                    )
                ),
                "investing_cash_flow_jpy": (
                    parse_million_yen(
                        row.get(
                            "投資CF（百万円）"
                        )
                    )
                ),
                "financing_cash_flow_jpy": (
                    parse_million_yen(
                        row.get(
                            "財務CF（百万円）"
                        )
                    )
                ),
                "cash_and_equivalents_jpy": (
                    parse_million_yen(
                        row.get(
                            "現金及び現金同等物（百万円）"
                        )
                    )
                ),
                "eps_yen": parse_decimal(
                    row.get(
                        "EPS（円）"
                    )
                ),
                "bps_yen": parse_decimal(
                    row.get(
                        "BPS（円）"
                    )
                ),
                "annual_dividend_yen": (
                    parse_decimal(
                        row.get(
                            "1株配当（円）"
                        )
                    )
                ),
                "issued_shares": parse_integer(
                    row.get(
                        "発行済株式数"
                    )
                ),
                "revenue_element_id": (
                    normalize_text(
                        row.get(
                            "売上高要素ID"
                        )
                    )
                    or None
                ),
                "operating_profit_element_id": (
                    normalize_text(
                        row.get(
                            "営業利益要素ID"
                        )
                    )
                    or None
                ),
                "net_income_element_id": (
                    normalize_text(
                        row.get(
                            "純利益要素ID"
                        )
                    )
                    or None
                ),
                "dividend_element_id": (
                    normalize_text(
                        row.get(
                            "配当要素ID"
                        )
                    )
                    or None
                ),
                "extracted_item_count": (
                    extracted_item_count
                ),
                "extraction_status": (
                    extraction_status
                ),
                "extraction_error": (
                    extraction_error
                    or None
                ),
                "source": DATABASE_SOURCE_NAME,
                "source_file": None,
                "source_url": (
                    normalize_text(
                        row.get(
                            "EDINET閲覧URL"
                        )
                    )
                    or None
                ),
                "extracted_at": processed_at,
                "processing_status": (
                    processing_status
                ),
            }
        )

    print(
        "PostgreSQL保存用レコードを"
        "作成しました。"
        f"件数: {len(records):,}"
    )

    return records


# ============================================================
# PostgreSQL事前検証
# ============================================================

def verify_document_foreign_keys(
    cursor,
    records: list[dict[str, Any]],
) -> None:
    """
    annual_financialsの外部キーとなる
    edinet_documentsがすべて存在するか確認する。
    """

    doc_ids = [
        str(record["doc_id"])
        for record in records
    ]

    cursor.execute(
        """
        SELECT doc_id
        FROM screener.edinet_documents
        WHERE doc_id = ANY(%s);
        """,
        (doc_ids,),
    )

    existing_doc_ids = {
        str(row["doc_id"])
        for row in cursor.fetchall()
    }

    missing_doc_ids = sorted(
        set(doc_ids)
        - existing_doc_ids
    )

    if missing_doc_ids:
        preview = ", ".join(
            missing_doc_ids[:10]
        )

        raise RuntimeError(
            "edinet_documentsに存在しない"
            "書類管理番号があります。"
            f"不足件数: {len(missing_doc_ids):,}, "
            f"例: {preview}"
        )


# ============================================================
# PostgreSQL保存
# ============================================================

def save_financials_to_database(
    records: list[dict[str, Any]],
) -> int:
    """
    年次財務情報をPostgreSQLへUPSERTする。
    """

    if not records:
        raise RuntimeError(
            "PostgreSQLへ保存する"
            "財務情報がありません。"
        )

    upsert_sql = """
        INSERT INTO screener.annual_financials (
            security_code,
            doc_id,
            fiscal_period_start,
            fiscal_period_end,
            accounting_standard,
            is_consolidated,
            revenue_jpy,
            operating_profit_jpy,
            ordinary_profit_jpy,
            net_income_jpy,
            total_assets_jpy,
            net_assets_jpy,
            equity_jpy,
            operating_cash_flow_jpy,
            investing_cash_flow_jpy,
            financing_cash_flow_jpy,
            cash_and_equivalents_jpy,
            eps_yen,
            bps_yen,
            annual_dividend_yen,
            issued_shares,
            revenue_element_id,
            operating_profit_element_id,
            net_income_element_id,
            dividend_element_id,
            extracted_item_count,
            extraction_status,
            extraction_error,
            source,
            source_file,
            source_url,
            extracted_at
        )
        VALUES (
            %(security_code)s,
            %(doc_id)s,
            %(fiscal_period_start)s,
            %(fiscal_period_end)s,
            %(accounting_standard)s,
            %(is_consolidated)s,
            %(revenue_jpy)s,
            %(operating_profit_jpy)s,
            %(ordinary_profit_jpy)s,
            %(net_income_jpy)s,
            %(total_assets_jpy)s,
            %(net_assets_jpy)s,
            %(equity_jpy)s,
            %(operating_cash_flow_jpy)s,
            %(investing_cash_flow_jpy)s,
            %(financing_cash_flow_jpy)s,
            %(cash_and_equivalents_jpy)s,
            %(eps_yen)s,
            %(bps_yen)s,
            %(annual_dividend_yen)s,
            %(issued_shares)s,
            %(revenue_element_id)s,
            %(operating_profit_element_id)s,
            %(net_income_element_id)s,
            %(dividend_element_id)s,
            %(extracted_item_count)s,
            %(extraction_status)s,
            %(extraction_error)s,
            %(source)s,
            %(source_file)s,
            %(source_url)s,
            %(extracted_at)s
        )
        ON CONFLICT (doc_id)
        DO UPDATE SET
            security_code =
                EXCLUDED.security_code,
            fiscal_period_start =
                EXCLUDED.fiscal_period_start,
            fiscal_period_end =
                EXCLUDED.fiscal_period_end,
            accounting_standard =
                COALESCE(
                    EXCLUDED.accounting_standard,
                    screener.annual_financials
                        .accounting_standard
                ),
            is_consolidated =
                COALESCE(
                    EXCLUDED.is_consolidated,
                    screener.annual_financials
                        .is_consolidated
                ),
            revenue_jpy =
                COALESCE(
                    EXCLUDED.revenue_jpy,
                    screener.annual_financials
                        .revenue_jpy
                ),
            operating_profit_jpy =
                COALESCE(
                    EXCLUDED.operating_profit_jpy,
                    screener.annual_financials
                        .operating_profit_jpy
                ),
            ordinary_profit_jpy =
                COALESCE(
                    EXCLUDED.ordinary_profit_jpy,
                    screener.annual_financials
                        .ordinary_profit_jpy
                ),
            net_income_jpy =
                COALESCE(
                    EXCLUDED.net_income_jpy,
                    screener.annual_financials
                        .net_income_jpy
                ),
            total_assets_jpy =
                COALESCE(
                    EXCLUDED.total_assets_jpy,
                    screener.annual_financials
                        .total_assets_jpy
                ),
            net_assets_jpy =
                COALESCE(
                    EXCLUDED.net_assets_jpy,
                    screener.annual_financials
                        .net_assets_jpy
                ),
            equity_jpy =
                COALESCE(
                    EXCLUDED.equity_jpy,
                    screener.annual_financials
                        .equity_jpy
                ),
            operating_cash_flow_jpy =
                COALESCE(
                    EXCLUDED.operating_cash_flow_jpy,
                    screener.annual_financials
                        .operating_cash_flow_jpy
                ),
            investing_cash_flow_jpy =
                COALESCE(
                    EXCLUDED.investing_cash_flow_jpy,
                    screener.annual_financials
                        .investing_cash_flow_jpy
                ),
            financing_cash_flow_jpy =
                COALESCE(
                    EXCLUDED.financing_cash_flow_jpy,
                    screener.annual_financials
                        .financing_cash_flow_jpy
                ),
            cash_and_equivalents_jpy =
                COALESCE(
                    EXCLUDED.cash_and_equivalents_jpy,
                    screener.annual_financials
                        .cash_and_equivalents_jpy
                ),
            eps_yen =
                COALESCE(
                    EXCLUDED.eps_yen,
                    screener.annual_financials
                        .eps_yen
                ),
            bps_yen =
                COALESCE(
                    EXCLUDED.bps_yen,
                    screener.annual_financials
                        .bps_yen
                ),
            annual_dividend_yen =
                COALESCE(
                    EXCLUDED.annual_dividend_yen,
                    screener.annual_financials
                        .annual_dividend_yen
                ),
            issued_shares =
                COALESCE(
                    EXCLUDED.issued_shares,
                    screener.annual_financials
                        .issued_shares
                ),
            revenue_element_id =
                COALESCE(
                    EXCLUDED.revenue_element_id,
                    screener.annual_financials
                        .revenue_element_id
                ),
            operating_profit_element_id =
                COALESCE(
                    EXCLUDED.operating_profit_element_id,
                    screener.annual_financials
                        .operating_profit_element_id
                ),
            net_income_element_id =
                COALESCE(
                    EXCLUDED.net_income_element_id,
                    screener.annual_financials
                        .net_income_element_id
                ),
            dividend_element_id =
                COALESCE(
                    EXCLUDED.dividend_element_id,
                    screener.annual_financials
                        .dividend_element_id
                ),
            extracted_item_count =
                EXCLUDED.extracted_item_count,
            extraction_status =
                EXCLUDED.extraction_status,
            extraction_error =
                EXCLUDED.extraction_error,
            source =
                EXCLUDED.source,
            source_url =
                COALESCE(
                    EXCLUDED.source_url,
                    screener.annual_financials
                        .source_url
                ),
            extracted_at =
                EXCLUDED.extracted_at;
    """

    update_document_sql = """
        UPDATE screener.edinet_documents
        SET
            accounting_standard =
                COALESCE(
                    %(accounting_standard)s,
                    accounting_standard
                ),
            processing_status =
                %(processing_status)s,
            processed_at =
                %(extracted_at)s,
            error_message =
                %(extraction_error)s
        WHERE doc_id = %(doc_id)s;
    """

    doc_ids = [
        str(record["doc_id"])
        for record in records
    ]

    with create_database_connection(
        "migrate_edinet_financials"
    ) as connection:
        with connection.cursor() as cursor:
            verify_document_foreign_keys(
                cursor,
                records,
            )

            cursor.executemany(
                upsert_sql,
                records,
            )

            cursor.executemany(
                update_document_sql,
                records,
            )

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS saved_count
                FROM screener.annual_financials
                WHERE doc_id = ANY(%s);
                """,
                (doc_ids,),
            )

            result = cursor.fetchone()

            if result is None:
                saved_count = 0

            else:
                saved_count = int(
                    result["saved_count"]
                )

            expected_count = len(records)

            if saved_count != expected_count:
                raise RuntimeError(
                    "PostgreSQL保存後の件数が"
                    "一致しません。"
                    f"期待件数: {expected_count:,}, "
                    f"保存件数: {saved_count:,}"
                )

    print(
        "EDINET財務をPostgreSQLへ"
        "保存しました。"
        f"保存確認件数: {saved_count:,}"
    )

    return saved_count


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    既存EDINET財務をPostgreSQLへ移行する。
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

    sheet_rows = read_financial_sheet(
        sheets_service,
        spreadsheet_id,
    )

    records = build_financial_database_records(
        sheet_rows
    )

    saved_count = save_financials_to_database(
        records
    )

    if saved_count != len(sheet_rows):
        raise RuntimeError(
            "シート件数とDB保存件数が"
            "一致しません。"
            f"シート件数: {len(sheet_rows):,}, "
            f"DB件数: {saved_count:,}"
        )

    print(
        "既存EDINET財務の移行が"
        "完了しました。"
        f"移行件数: {saved_count:,}"
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            "既存EDINET財務の移行中に"
            "エラーが発生しました。",
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
