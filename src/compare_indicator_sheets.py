"""
既存の「株式指標」と、
PostgreSQLから出力した「株式指標_DB比較」を比較する。

更新日時は比較対象から除外する。
数値列は0.01以下の差を許容する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

import sys
import traceback
from decimal import Decimal, InvalidOperation
from typing import Any


# ============================================================
# プロジェクト内モジュール
# ============================================================

from update_edinet_financials import (
    INDICATOR_HEADERS,
    INDICATOR_SHEET_NAME,
    create_google_sheets_service,
    get_required_environment_variable,
    normalize_security_code,
    normalize_text,
    read_sheet,
)


# ============================================================
# 定数
# ============================================================

DATABASE_INDICATOR_SHEET_NAME = "株式指標_DB比較"

IGNORED_HEADERS = {
    "更新日時",
}

NUMERIC_HEADERS = {
    "終値",
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
}

DATE_HEADERS = {
    "株価基準日",
    "決算期末日",
}

NUMERIC_TOLERANCE = Decimal("0.01")

MAX_DIFFERENCE_PREVIEW = 100


# ============================================================
# シート行の辞書化
# ============================================================

def build_sheet_records(
    values: list[list[Any]],
    *,
    sheet_name: str,
) -> dict[str, dict[str, Any]]:
    """
    シート内容を証券コードをキーとする辞書へ変換する。
    """

    if not values:
        raise RuntimeError(
            f"{sheet_name}シートが空です。"
        )

    headers = [
        normalize_text(header)
        for header in values[0]
    ]

    missing_headers = [
        header
        for header in INDICATOR_HEADERS
        if header not in headers
    ]

    if missing_headers:
        raise RuntimeError(
            f"{sheet_name}シートに必要な列がありません。"
            f"不足列: {missing_headers}"
        )

    records: dict[
        str,
        dict[str, Any],
    ] = {}

    for sheet_row_number, original_row in enumerate(
        values[1:],
        start=2,
    ):
        if not original_row:
            continue

        row = list(
            original_row
        )

        if len(row) < len(headers):
            row.extend(
                [""] * (
                    len(headers)
                    - len(row)
                )
            )

        record = dict(
            zip(
                headers,
                row,
            )
        )

        security_code = normalize_security_code(
            record.get(
                "証券コード"
            )
        )

        if not security_code:
            raise RuntimeError(
                f"{sheet_name}シートの"
                "証券コードが空です。"
                f"行: {sheet_row_number}"
            )

        if security_code in records:
            raise RuntimeError(
                f"{sheet_name}シートに"
                "証券コードの重複があります。"
                f"証券コード: {security_code}"
            )

        record["_sheet_row_number"] = (
            sheet_row_number
        )

        records[security_code] = record

    return records


# ============================================================
# 値の比較用正規化
# ============================================================

def parse_comparison_decimal(
    value: Any,
) -> Decimal | None:
    """
    シートの数値を比較用Decimalへ変換する。
    """

    text = normalize_text(
        value
    )

    if not text:
        return None

    normalized_value = (
        text
        .replace(",", "")
        .replace("△", "-")
        .replace("▲", "-")
        .replace("−", "-")
        .replace("－", "-")
        .replace("%", "")
        .replace("円", "")
        .replace("株", "")
        .strip()
    )

    try:
        number = Decimal(
            normalized_value
        )

    except InvalidOperation as error:
        raise RuntimeError(
            "比較対象の数値を解析できませんでした。"
            f"値: {text}"
        ) from error

    if not number.is_finite():
        return None

    return number


def normalize_date_text(
    value: Any,
) -> str:
    """
    日付表記をYYYY-MM-DD形式へ近づける。
    """

    text = normalize_text(
        value
    )

    if not text:
        return ""

    return (
        text[:10]
        .replace("/", "-")
        .replace(".", "-")
    )


def values_are_equal(
    header: str,
    existing_value: Any,
    database_value: Any,
) -> bool:
    """
    列の種類に応じて値を比較する。
    """

    if header in NUMERIC_HEADERS:
        existing_number = (
            parse_comparison_decimal(
                existing_value
            )
        )

        database_number = (
            parse_comparison_decimal(
                database_value
            )
        )

        if (
            existing_number is None
            and database_number is None
        ):
            return True

        if (
            existing_number is None
            or database_number is None
        ):
            return False

        return (
            abs(
                existing_number
                - database_number
            )
            <= NUMERIC_TOLERANCE
        )

    if header in DATE_HEADERS:
        return (
            normalize_date_text(
                existing_value
            )
            ==
            normalize_date_text(
                database_value
            )
        )

    return (
        normalize_text(
            existing_value
        )
        ==
        normalize_text(
            database_value
        )
    )


# ============================================================
# シート比較
# ============================================================

def compare_indicator_sheets(
    existing_records: dict[
        str,
        dict[str, Any],
    ],
    database_records: dict[
        str,
        dict[str, Any],
    ],
) -> None:
    """
    証券コードと各列の値を比較する。
    """

    existing_codes = set(
        existing_records
    )

    database_codes = set(
        database_records
    )

    missing_from_database = sorted(
        existing_codes
        - database_codes
    )

    missing_from_existing = sorted(
        database_codes
        - existing_codes
    )

    differences: list[str] = []

    for security_code in sorted(
        existing_codes
        & database_codes
    ):
        existing_record = (
            existing_records[
                security_code
            ]
        )

        database_record = (
            database_records[
                security_code
            ]
        )

        for header in INDICATOR_HEADERS:
            if header in IGNORED_HEADERS:
                continue

            existing_value = (
                existing_record.get(
                    header,
                    "",
                )
            )

            database_value = (
                database_record.get(
                    header,
                    "",
                )
            )

            if values_are_equal(
                header,
                existing_value,
                database_value,
            ):
                continue

            differences.append(
                (
                    f"証券コード={security_code}, "
                    f"列={header}, "
                    f"既存={existing_value!r}, "
                    f"DB={database_value!r}"
                )
            )

    print(
        "株式指標比較結果:"
    )

    print(
        "  既存シート件数: "
        f"{len(existing_records):,}"
    )

    print(
        "  DB比較シート件数: "
        f"{len(database_records):,}"
    )

    print(
        "  DB比較シート不足銘柄: "
        f"{len(missing_from_database):,}"
    )

    print(
        "  既存シート不足銘柄: "
        f"{len(missing_from_existing):,}"
    )

    print(
        "  値の相違件数: "
        f"{len(differences):,}"
    )

    if missing_from_database:
        print(
            "DB比較シート不足銘柄:"
        )

        for security_code in (
            missing_from_database[
                :MAX_DIFFERENCE_PREVIEW
            ]
        ):
            print(
                f"  {security_code}"
            )

    if missing_from_existing:
        print(
            "既存シート不足銘柄:"
        )

        for security_code in (
            missing_from_existing[
                :MAX_DIFFERENCE_PREVIEW
            ]
        ):
            print(
                f"  {security_code}"
            )

    if differences:
        print(
            "値の相違:"
        )

        for difference in (
            differences[
                :MAX_DIFFERENCE_PREVIEW
            ]
        ):
            print(
                f"  {difference}"
            )

    if (
        missing_from_database
        or missing_from_existing
        or differences
    ):
        raise RuntimeError(
            "既存の株式指標と"
            "PostgreSQL版株式指標に"
            "相違があります。"
        )

    print(
        "既存の株式指標と"
        "PostgreSQL版株式指標は一致しました。"
    )


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """
    既存シートとDB比較シートを比較する。
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

    existing_values = read_sheet(
        sheets_service,
        spreadsheet_id,
        INDICATOR_SHEET_NAME,
    )

    database_values = read_sheet(
        sheets_service,
        spreadsheet_id,
        DATABASE_INDICATOR_SHEET_NAME,
    )

    existing_records = build_sheet_records(
        existing_values,
        sheet_name=INDICATOR_SHEET_NAME,
    )

    database_records = build_sheet_records(
        database_values,
        sheet_name=(
            DATABASE_INDICATOR_SHEET_NAME
        ),
    )

    compare_indicator_sheets(
        existing_records,
        database_records,
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            "株式指標の比較中に"
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
