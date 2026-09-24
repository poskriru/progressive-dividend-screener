"""
Google Sheetsの列書式設定ユーティリティ。

日付をシートのシリアル値へ変換し、
列単位の表示書式（日付・数値）を適用する。
"""

# ============================================================
# 標準ライブラリ
# ============================================================

from datetime import date, datetime
from typing import Any


# ============================================================
# 定数
# ============================================================

# Google Sheetsのシリアル値の起点（1900年方式、
# Excel互換のため1899-12-30が0）。
GOOGLE_SHEETS_EPOCH_DATE = date(1899, 12, 30)

SECONDS_PER_DAY = 24 * 60 * 60


# ============================================================
# シリアル値変換
# ============================================================

def to_sheet_serial_value(
    value: Any,
) -> float | str:
    """
    日付・日時をGoogle Sheetsのシリアル値へ変換する。

    日時は時刻を日以下の小数として含める。
    NULLや変換できない値はそのまま空文字または元の値を返す。
    """

    if value is None:
        return ""

    if isinstance(value, datetime):
        return datetime_to_serial(value)

    if isinstance(value, date):
        return float(
            (value - GOOGLE_SHEETS_EPOCH_DATE).days
        )

    if isinstance(value, str):
        text = value.strip()

        if not text:
            return ""

        parsed = parse_iso_datetime_text(text)

        if isinstance(parsed, datetime):
            return datetime_to_serial(parsed)

        if isinstance(parsed, date):
            return float(
                (parsed - GOOGLE_SHEETS_EPOCH_DATE).days
            )

        return value

    return value


def datetime_to_serial(
    value: datetime,
) -> float:
    """日時をシリアル値へ変換する。"""

    naive_value = (
        value.replace(tzinfo=None)
        if value.tzinfo is not None
        else value
    )

    days = (
        naive_value.date() - GOOGLE_SHEETS_EPOCH_DATE
    ).days

    seconds = (
        naive_value.hour * 3600
        + naive_value.minute * 60
        + naive_value.second
    )

    return days + seconds / SECONDS_PER_DAY


def parse_iso_datetime_text(
    text: str,
) -> date | datetime | None:
    """ISO形式の日付・日時文字列を解析する。"""

    normalized_text = text.replace("T", " ")

    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(
                normalized_text,
                pattern,
            )

        except ValueError:
            continue

    return None


# ============================================================
# 列書式リクエスト
# ============================================================

def build_column_format_requests(
    sheet_id: int,
    column_formats: dict[int, str],
) -> list[dict[str, Any]]:
    """
    列番号→表示書式パターンの辞書から
    repeatCellリクエストを作成する。

    column_formatsのキーは0始まりの列番号。
    """

    requests: list[dict[str, Any]] = []

    for column_index in sorted(column_formats):
        pattern = column_formats[column_index]

        if not pattern:
            continue

        number_format_type = (
            "DATE_TIME"
            if " " in pattern
            or "hh" in pattern
            else "DATE"
            if "y" in pattern.lower()
            else "NUMBER"
        )

        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startColumnIndex": column_index,
                        "endColumnIndex": column_index + 1,
                        "startRowIndex": 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": number_format_type,
                                "pattern": pattern,
                            }
                        }
                    },
                    "fields": (
                        "userEnteredFormat.numberFormat"
                    ),
                }
            }
        )

    return requests


def apply_column_formats(
    service,
    spreadsheet_id: str,
    sheet_id: int,
    column_formats: dict[int, str],
) -> None:
    """指定シートの列へ表示書式を適用する。"""

    requests = build_column_format_requests(
        sheet_id,
        column_formats,
    )

    if not requests:
        return

    (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": requests,
            },
        )
        .execute()
    )

    print(
        "列の表示書式を適用しました。"
        f"列数: {len(requests)}"
    )