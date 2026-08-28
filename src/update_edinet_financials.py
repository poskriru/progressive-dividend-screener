import csv
import io
import json
import math
import os
import re
import sys
import time
import traceback
import zipfile
from datetime import datetime, timezone, timedelta
from typing import Any

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# ============================================================
# 基本設定
# ============================================================

EDINET_DOCUMENT_API_URL = (
    "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
)

EDINET_VIEW_URL = (
    "https://disclosure2.edinet-fsa.go.jp/"
    "WZEK0040.aspx?S100={doc_id}"
)

DOCUMENT_SHEET_NAME = "EDINET書類"
FINANCIAL_SHEET_NAME = "EDINET財務"
PRICE_SHEET_NAME = "最新株価"
INDICATOR_SHEET_NAME = "株式指標"

JST = timezone(timedelta(hours=9))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

REQUEST_TIMEOUT_SECONDS = 120
REQUEST_INTERVAL_SECONDS = 0.7
MAX_RETRIES = 4


# ============================================================
# 出力列
# ============================================================

FINANCIAL_HEADERS = [
    "取得日時",
    "提出日時",
    "対象期間開始日",
    "対象期間終了日",
    "証券コード",
    "銘柄名",
    "市場",
    "提出者名",
    "EDINETコード",
    "書類管理番号",
    "書類種別",
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
    "データ出典",
    "EDINET閲覧URL",
]

INDICATOR_HEADERS = [
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
]


# ============================================================
# 財務項目定義
# ============================================================

METRIC_DEFINITIONS = {
    "accounting_standard": {
        "element_ids": [
            "AccountingStandardsDEI",
        ],
        "labels": [
            "会計基準、DEI",
            "会計基準",
        ],
        "kind": "text",
        "prefer_consolidated": False,
    },
    "revenue": {
        "element_ids": [
            "NetSales",
            "Revenue",
            "RevenueIFRS",
            "NetSalesIFRS",
            "OperatingRevenue1",
            "OperatingRevenue2",
            "OperatingRevenue",
        ],
        "labels": [
            "売上高",
            "売上収益",
            "営業収益",
            "営業収入",
            "完成工事高",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "operating_income": {
        "element_ids": [
            "OperatingIncome",
            "OperatingIncomeLoss",
            "OperatingIncomeLossSummaryOfBusinessResults",
            "OperatingProfitLoss",
            "OperatingProfitLossIFRS",
            "OperatingProfitLossIFRSSummaryOfBusinessResults",
        ],
        "labels": [
            "営業利益",
            "営業損失",
            "営業利益又は営業損失",
            "営業利益又は営業損失（△）",
            "営業利益（△損失）",
            "営業利益（△損失）（IFRS）",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "ordinary_income": {
        "element_ids": [
            "OrdinaryIncome",
            "OrdinaryIncomeLoss",
            "OrdinaryIncomeLossSummaryOfBusinessResults",
        ],
        "labels": [
            "経常利益",
            "経常損失",
            "経常利益又は経常損失",
            "経常利益又は経常損失（△）",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "net_income": {
        "element_ids": [
            "ProfitLoss",
            "ProfitLossSummaryOfBusinessResults",
            "ProfitLossIFRS",
            "ProfitLossIFRSSummaryOfBusinessResults",
            "ProfitLossAttributableToOwnersOfParent",
            "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
            "ProfitLossAttributableToOwnersOfParentIFRS",
            "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        ],
        "labels": [
            "当期純利益",
            "当期純損失",
            "当期純利益又は当期純損失",
            "親会社株主に帰属する当期純利益",
            "親会社株主に帰属する当期純損失",
            "親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失",
            "親会社の所有者に帰属する当期利益",
            "親会社の所有者に帰属する当期損失",
            "親会社の所有者に帰属する当期利益（△損失）",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "total_assets": {
        "element_ids": [
            "Assets",
            "AssetsIFRS",
            "TotalAssetsSummaryOfBusinessResults",
            "TotalAssetsIFRSSummaryOfBusinessResults",
        ],
        "labels": [
            "資産合計",
            "総資産額",
            "総資産",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "net_assets": {
        "element_ids": [
            "NetAssets",
            "NetAssetsSummaryOfBusinessResults",
        ],
        "labels": [
            "純資産合計",
            "純資産額",
            "純資産",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "equity": {
        "element_ids": [
            "Equity",
            "EquityIFRS",
            "ShareholdersEquity",
            "EquityAttributableToOwnersOfParentIFRS",
            "EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        ],
        "labels": [
            "自己資本",
            "株主資本",
            "親会社の所有者に帰属する持分",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "operating_cf": {
        "element_ids": [
            "NetCashProvidedByUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivitiesIFRS",
        ],
        "labels": [
            "営業活動によるキャッシュ・フロー",
            "営業活動によるキャッシュ・フロー（IFRS）",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "investing_cf": {
        "element_ids": [
            "NetCashProvidedByUsedInInvestingActivities",
            "CashFlowsFromUsedInInvestingActivitiesIFRS",
        ],
        "labels": [
            "投資活動によるキャッシュ・フロー",
            "投資活動によるキャッシュ・フロー（IFRS）",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "financing_cf": {
        "element_ids": [
            "NetCashProvidedByUsedInFinancingActivities",
            "CashFlowsFromUsedInFinancingActivitiesIFRS",
        ],
        "labels": [
            "財務活動によるキャッシュ・フロー",
            "財務活動によるキャッシュ・フロー（IFRS）",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "cash": {
        "element_ids": [
            "CashAndCashEquivalents",
            "CashAndCashEquivalentsIFRS",
        ],
        "labels": [
            "現金及び現金同等物の期末残高",
            "現金及び現金同等物",
        ],
        "kind": "money",
        "prefer_consolidated": True,
    },
    "eps": {
        "element_ids": [
            "BasicEarningsLossPerShare",
            "BasicEarningsPerShare",
            "BasicEarningsLossPerShareIFRS",
            "BasicEarningsPerShareIFRS",
            "BasicEarningsLossPerShareSummaryOfBusinessResults",
            "BasicEarningsPerShareSummaryOfBusinessResults",
            "BasicEarningsLossPerShareIFRSSummaryOfBusinessResults",
            "BasicEarningsPerShareIFRSSummaryOfBusinessResults",
        ],
        "labels": [
            "1株当たり当期純利益",
            "１株当たり当期純利益",
            "1株当たり当期純損失",
            "１株当たり当期純損失",
            "1株当たり当期純利益又は1株当たり当期純損失",
            "１株当たり当期純利益又は１株当たり当期純損失",
            "1株当たり当期純利益又は当期純損失",
            "１株当たり当期純利益又は当期純損失",
            "基本的1株当たり当期利益",
            "基本的１株当たり当期利益",
            "基本的1株当たり当期利益（△損失）",
            "基本的１株当たり当期利益（△損失）",
        ],
        "kind": "number",
        "expected_unit": "per_share",
        "prefer_consolidated": True,
    },
    "bps": {
        "element_ids": [
            "NetAssetsPerShare",
            "NetAssetsPerShareSummaryOfBusinessResults",
            "EquityAttributableToOwnersOfParentPerShareIFRS",
            "EquityAttributableToOwnersOfParentPerShareIFRSSummaryOfBusinessResults",
        ],
        "labels": [
            "1株当たり純資産額",
            "１株当たり純資産額",
            "1株当たり親会社所有者帰属持分",
            "１株当たり親会社所有者帰属持分",
        ],
        "kind": "number",
        "expected_unit": "per_share",
        "prefer_consolidated": True,
    },
    "dividend_per_share": {
        "element_ids": [
            "DividendPaidPerShareSummaryOfBusinessResults",
            "AnnualDividendPerShare",
            "DividendsPerShare",
        ],
        "labels": [
            "1株当たり配当額",
            "１株当たり配当額",
            "年間配当金",
            "年間配当額",
        ],
        "kind": "number",
        "expected_unit": "per_share",
        "prefer_consolidated": False,
    },
    "shares_issued": {
        "element_ids": [
            "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc",
            "TotalNumberOfIssuedShares",
            "NumberOfIssuedShares",
        ],
        "labels": [
            "発行済株式総数",
            "期末発行済株式数",
        ],
        "kind": "number",
        "expected_unit": "shares",
        "prefer_consolidated": False,
    },
}


# ============================================================
# 共通処理
# ============================================================

def get_required_environment_variable(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")

    return value


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


def normalize_security_code(value: Any) -> str:
    text = normalize_text(value).upper()

    if not text:
        return ""

    text = re.sub(r"\.0$", "", text)
    text = re.sub(r"[^0-9A-Z]", "", text)

    if len(text) == 5 and text.endswith("0"):
        return text[:4]

    return text


def parse_number(value: Any) -> float | None:
    text = normalize_text(value)

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

    text = (
        text.replace(",", "")
        .replace("△", "-")
        .replace("▲", "-")
        .replace("−", "-")
        .replace("－", "-")
        .replace("円", "")
        .replace("株", "")
        .replace("%", "")
        .strip()
    )

    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?", text)

    if not match:
        return None

    try:
        number = float(match.group(0))

        if negative and number > 0:
            number = -number

        return number
    except ValueError:
        return None


def safe_round(value: float | None, digits: int = 2) -> float | str:
    if value is None:
        return ""

    if not math.isfinite(value):
        return ""

    return round(value, digits)


def safe_divide(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    if numerator is None or denominator is None:
        return None

    if denominator == 0:
        return None

    return numerator / denominator


def yen_to_million(value: float | None) -> float | None:
    if value is None:
        return None

    return value / 1_000_000


def send_discord_notification(
    webhook_url: str,
    title: str,
    description: str,
    success: bool = True,
) -> None:
    color = 0x2ECC71 if success else 0xE74C3C

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description[:4000],
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {
                    "text": "累進配当スクリーナー",
                },
            }
        ]
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"Discord通知に失敗しました: {exc}")


# ============================================================
# Google Sheets
# ============================================================

def create_google_sheets_service(service_account_json: str):
    service_account_info = json.loads(service_account_json)

    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES,
    )

    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )


def get_spreadsheet_metadata(service, spreadsheet_id: str) -> dict:
    return (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id)
        .execute()
    )


# ============================================================
# シートの取得・新規作成
# ============================================================

def get_or_create_sheet(
    service,
    spreadsheet_id: str,
    sheet_name: str,
) -> int:
    metadata = get_spreadsheet_metadata(
        service,
        spreadsheet_id,
    )

    # ========================================================
    # 既存シートを検索
    # ========================================================

    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})

        if properties.get("title") == sheet_name:
            return properties["sheetId"]

    # ========================================================
    # シートが存在しない場合は新規作成
    #
    # frozenRowCountはproperties直下ではなく、
    # gridPropertiesの中に指定する。
    # ========================================================

    response = (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": sheet_name,
                                "gridProperties": {
                                    "frozenRowCount": 1,
                                },
                            }
                        }
                    }
                ]
            },
        )
        .execute()
    )

    return response["replies"][0]["addSheet"]["properties"]["sheetId"]


def read_sheet(
    service,
    spreadsheet_id: str,
    sheet_name: str,
) -> list[list[str]]:
    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'",
        )
        .execute()
    )

    return response.get("values", [])


def rows_to_dicts(values: list[list[str]]) -> list[dict[str, str]]:
    if not values:
        return []

    headers = [normalize_text(value) for value in values[0]]
    results = []

    for row in values[1:]:
        padded = row + [""] * max(0, len(headers) - len(row))
        results.append(dict(zip(headers, padded)))

    return results


def write_sheet(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    sheet_id = get_or_create_sheet(
        service,
        spreadsheet_id,
        sheet_name,
    )

    (
        service.spreadsheets()
        .values()
        .clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'",
            body={},
        )
        .execute()
    )

    values = [headers] + rows

    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            body={"values": values},
        )
        .execute()
    )

    (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {
                                    "frozenRowCount": 1,
                                },
                            },
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {
                                        "red": 0.12,
                                        "green": 0.29,
                                        "blue": 0.49,
                                    },
                                    "textFormat": {
                                        "foregroundColor": {
                                            "red": 1,
                                            "green": 1,
                                            "blue": 1,
                                        },
                                        "bold": True,
                                    },
                                }
                            },
                            "fields": (
                                "userEnteredFormat.backgroundColor,"
                                "userEnteredFormat.textFormat"
                            ),
                        }
                    },
                    {
                        "setBasicFilter": {
                            "filter": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": 0,
                                    "endRowIndex": max(len(values), 1),
                                    "startColumnIndex": 0,
                                    "endColumnIndex": len(headers),
                                }
                            }
                        }
                    },
                    {
                        "autoResizeDimensions": {
                            "dimensions": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": len(headers),
                            }
                        }
                    },
                ]
            },
        )
        .execute()
    )


# ============================================================
# EDINET書類の読み込み
# ============================================================

def get_first_value(
    row: dict[str, str],
    candidate_names: list[str],
) -> str:
    for name in candidate_names:
        value = normalize_text(row.get(name, ""))

        if value:
            return value

    return ""


def load_target_documents(
    service,
    spreadsheet_id: str,
    processed_doc_ids: set[str],
    max_documents: int,
) -> list[dict[str, str]]:
    values = read_sheet(
        service,
        spreadsheet_id,
        DOCUMENT_SHEET_NAME,
    )

    documents = rows_to_dicts(values)
    targets = []

    for row in documents:
        doc_id = get_first_value(
            row,
            [
                "書類管理番号",
                "docID",
                "DocID",
            ],
        )

        document_type = get_first_value(
            row,
            [
                "書類種別",
                "書類名",
                "書類概要",
            ],
        )

        document_type_code = get_first_value(
            row,
            [
                "書類種別コード",
                "docTypeCode",
            ],
        )

        csv_flag = get_first_value(
            row,
            [
                "CSV有無",
                "CSVフラグ",
                "csvFlag",
            ],
        )

        withdrawal_status = get_first_value(
            row,
            [
                "取下げ状態",
                "withdrawalStatus",
            ],
        )

        if not doc_id:
            continue

        if doc_id in processed_doc_ids:
            continue

        is_annual_report = (
            document_type_code == "120"
            or (
                "有価証券報告書" in document_type
                and "訂正" not in document_type
            )
        )

        if not is_annual_report:
            continue

        if csv_flag in {"0", "なし", "無", "False", "false"}:
            continue

        if withdrawal_status in {"1", "取下げ", "取下げ済み"}:
            continue

        row["_doc_id"] = doc_id
        targets.append(row)

    targets.sort(
        key=lambda item: get_first_value(
            item,
            ["提出日時", "提出日", "submitDateTime"],
        )
    )

    return targets[:max_documents]


# ============================================================
# EDINET CSVの取得
# ============================================================

def download_edinet_csv_zip(
    session: requests.Session,
    api_key: str,
    doc_id: str,
) -> bytes:
    url = EDINET_DOCUMENT_API_URL.format(doc_id=doc_id)

    params = {
        "type": "5",
        "Subscription-Key": api_key,
    }

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 429:
                wait_seconds = 5 * (attempt + 1)
                print(
                    f"EDINET API 429: {wait_seconds}秒待機します。"
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()

            content = response.content

            if content[:2] != b"PK":
                try:
                    error_json = response.json()
                except Exception:
                    error_json = {
                        "message": response.text[:500],
                    }

                raise RuntimeError(
                    f"CSV ZIPではない応答です: {error_json}"
                )

            return content

        except Exception as exc:
            last_error = exc

            if attempt < MAX_RETRIES - 1:
                wait_seconds = 2 ** attempt
                time.sleep(wait_seconds)

    raise RuntimeError(
        f"EDINET CSV取得に失敗しました: {last_error}"
    )


# ============================================================
# EDINET CSV ZIPの解析
# ============================================================

def parse_edinet_csv_zip(zip_bytes: bytes) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        # ====================================================
        # ZIP内の財務CSVをすべて対象にする
        #
        # jpcrp:
        #   有価証券報告書固有項目・経営指標等
        #
        # jppfs:
        #   日本基準の財務諸表
        #
        # jpigp:
        #   IFRS関連
        #
        # jpaud:
        #   監査関連のため除外
        # ====================================================

        csv_files = []

        for name in archive.namelist():
            lower_name = name.lower()

            if not lower_name.endswith(".csv"):
                continue

            if "jpaud" in lower_name:
                continue

            csv_files.append(name)

        if not csv_files:
            raise RuntimeError(
                "ZIP内に解析可能なCSVファイルがありません。"
            )

        print(
            f"解析対象CSV: {len(csv_files)}ファイル"
        )

        for filename in csv_files:
            raw = archive.read(filename)

            decoded = None

            for encoding in [
                "utf-16le",
                "utf-16",
                "utf-8-sig",
                "utf-8",
            ]:
                try:
                    decoded = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if decoded is None:
                print(
                    f"文字コードを判定できないためスキップ: "
                    f"{filename}"
                )
                continue

            reader = csv.DictReader(
                io.StringIO(decoded),
                delimiter="\t",
            )

            file_fact_count = 0

            for row in reader:
                normalized_row = {
                    normalize_text(key): normalize_text(value)
                    for key, value in row.items()
                    if key is not None
                }

                normalized_row["_source_file"] = filename

                facts.append(normalized_row)
                file_fact_count += 1

            print(
                f"CSV読込: {filename} "
                f"({file_fact_count:,}行)"
            )

    if not facts:
        raise RuntimeError(
            "EDINET CSVからデータ行を読み込めませんでした。"
        )

    return facts



# ============================================================
# 財務情報の抽出
# ============================================================

def get_fact_value(
    fact: dict[str, str],
    candidates: list[str],
) -> str:
    for candidate in candidates:
        value = normalize_text(fact.get(candidate, ""))

        if value:
            return value

    return ""


def score_fact(
    fact: dict[str, str],
    definition: dict[str, Any],
) -> int:
    element_id = get_fact_value(
        fact,
        ["要素ID", "element_id", "Element ID"],
    )

    label = get_fact_value(
        fact,
        ["項目名", "ラベル", "item_name"],
    )

    context_id = get_fact_value(
        fact,
        ["コンテキストID", "context_id"],
    )

    relative_year = get_fact_value(
        fact,
        ["相対年度", "relative_year"],
    )

    consolidated_type = get_fact_value(
        fact,
        ["連結・個別", "連結個別", "consolidated_or_nonconsolidated"],
    )

    score = 0
    element_suffix = element_id.split(":")[-1]

    if element_suffix in definition["element_ids"]:
        score += 200

    if label in definition["labels"]:
        score += 140
    elif any(candidate in label for candidate in definition["labels"]):
        score += 80

    current_terms = [
        "当期",
        "当年",
        "当連結会計年度",
        "当事業年度",
        "提出者",
    ]

    previous_terms = [
        "前期",
        "前連結会計年度",
        "前事業年度",
        "過年度",
    ]

    if any(term in relative_year for term in current_terms):
        score += 60

    if any(term in relative_year for term in previous_terms):
        score -= 200

    if "CurrentYear" in context_id:
        score += 50

    if "Current" in context_id:
        score += 15

    if "Prior" in context_id or "Previous" in context_id:
        score -= 200

    if "Segment" in context_id:
        score -= 80

    if definition.get("prefer_consolidated"):
        if consolidated_type == "連結":
            score += 45
        elif consolidated_type == "個別":
            score -= 20

        if "ConsolidatedMember" in context_id:
            score += 20

        if "NonConsolidatedMember" in context_id:
            score -= 15
    else:
        if consolidated_type == "個別":
            score += 15

    return score

# ============================================================
# 財務項目と単位の厳密な一致判定
# ============================================================

def normalize_matching_text(value: Any) -> str:
    """
    財務項目名の比較用に、全角・半角の数字差や空白差を吸収する。
    """
    text = normalize_text(value)

    translation_table = str.maketrans(
        {
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
            "（": "(",
            "）": ")",
            "／": "/",
        }
    )

    return (
        text.translate(translation_table)
        .replace(" ", "")
        .replace("\t", "")
        .strip()
    )


# ============================================================
# 財務項目の厳密な一致判定
# ============================================================

def fact_matches_definition(
    fact: dict[str, str],
    definition: dict[str, Any],
) -> bool:
    """
    要素IDまたは項目名が対象財務項目に一致するか判定する。

    相対年度・コンテキストだけでは一致としない。
    """
    element_id = get_fact_value(
        fact,
        [
            "要素ID",
            "element_id",
            "Element ID",
        ],
    )

    label = get_fact_value(
        fact,
        [
            "項目名",
            "ラベル",
            "item_name",
        ],
    )

    element_suffix = element_id.split(":")[-1]

    # ========================================================
    # 標準要素IDの完全一致
    # ========================================================

    if element_suffix in definition.get("element_ids", []):
        return True

    normalized_label = normalize_matching_text(label)

    normalized_candidate_labels = {
        normalize_matching_text(candidate)
        for candidate in definition.get("labels", [])
    }

    # ========================================================
    # 項目名の完全一致
    # ========================================================

    if normalized_label in normalized_candidate_labels:
        return True

    # ========================================================
    # EDINETの定型的な補足文字だけを許可
    # ========================================================

    allowed_suffixes = [
        "、経営指標等",
        "、主要な経営指標等の推移",
        "、連結経営指標等",
        "、提出会社の経営指標等",
    ]

    for candidate in normalized_candidate_labels:
        for allowed_suffix in allowed_suffixes:
            if normalized_label == candidate + normalize_matching_text(
                allowed_suffix
            ):
                return True

    return False



# ============================================================
# 財務項目の単位確認
# ============================================================

def fact_matches_expected_unit(
    fact: dict[str, str],
    definition: dict[str, Any],
) -> bool:
    """
    要素IDと単位情報を使って、対象項目の単位を確認する。

    EDINET CSVでは年度・企業・タクソノミによって
    単位の表記に差があるため、正しい標準要素IDに
    PerShareが含まれる場合は単位表記にかかわらず許可する。
    """
    expected_unit = definition.get("expected_unit")

    if not expected_unit:
        return True

    element_id = get_fact_value(
        fact,
        [
            "要素ID",
            "element_id",
            "Element ID",
        ],
    )

    element_suffix = element_id.split(":")[-1]

    unit_id = get_fact_value(
        fact,
        [
            "ユニットID",
            "unit_id",
            "Unit ID",
        ],
    )

    displayed_unit = get_fact_value(
        fact,
        [
            "単位",
            "unit",
            "Unit",
        ],
    )

    combined_unit = normalize_matching_text(
        f"{unit_id} {displayed_unit}"
    ).lower()

    # ========================================================
    # 1株当たり項目
    # ========================================================

    if expected_unit == "per_share":
        # 標準要素ID自体にPerShareが含まれていれば許可する。
        if "pershare" in element_suffix.lower():
            return True

        per_share_markers = [
            "jpyper",
            "jpypershare",
            "jpypershares",
            "jpy_per_share",
            "jpy_per_shares",
            "円/株",
            "円・銭",
            "円銭",
        ]

        return any(
            marker in combined_unit
            for marker in per_share_markers
        )

    # ========================================================
    # 株式数
    # ========================================================

    if expected_unit == "shares":
        if (
            "numberofissuedshares" in element_suffix.lower()
            or "totalnumberofissuedshares" in element_suffix.lower()
        ):
            return True

        share_markers = [
            "shares",
            "share",
            "株",
        ]

        return any(
            marker in combined_unit
            for marker in share_markers
        )

    return True



def fact_value_is_reasonable(
    value: float,
    definition: dict[str, Any],
) -> bool:
    """
    明らかに異常な桁の値を除外する。

    上限だけで正誤を決めるのではなく、
    要素ID・項目名・単位の一致後の最終安全装置として使用する。
    """
    expected_unit = definition.get("expected_unit")

    if expected_unit == "per_share":
        # EPS・BPS・1株配当が1億円単位になることは通常想定されない。
        if abs(value) > 10_000_000:
            return False

    if expected_unit == "shares":
        if value < 0:
            return False

    return True


# ============================================================
# 財務項目の抽出
# ============================================================

def extract_metric(
    facts: list[dict[str, str]],
    definition: dict[str, Any],
) -> dict[str, Any] | None:
    candidates = []

    element_ids = set(
        definition.get("element_ids", [])
    )

    is_dividend_metric = any(
        (
            "Dividend" in element_id
            or "Dividends" in element_id
        )
        for element_id in element_ids
    )

    for fact in facts:
        # ====================================================
        # 要素IDまたは項目名の一致を必須にする
        # ====================================================

        if not fact_matches_definition(
            fact,
            definition,
        ):
            continue

        value_text = get_fact_value(
            fact,
            [
                "値",
                "value",
                "Value",
            ],
        )

        if value_text == "":
            continue

        if definition["kind"] == "text":
            parsed_value: Any = value_text
        else:
            parsed_value = parse_number(value_text)

            # =================================================
            # 配当項目で明示的に「－」の場合は0円として扱う
            # =================================================

            if (
                parsed_value is None
                and is_dividend_metric
                and normalize_text(value_text)
                in {
                    "-",
                    "－",
                    "―",
                    "—",
                    "–",
                }
            ):
                parsed_value = 0.0

            if parsed_value is None:
                continue

            if not fact_value_is_reasonable(
                parsed_value,
                definition,
            ):
                continue

        score = score_fact(
            fact,
            definition,
        )

        element_id = get_fact_value(
            fact,
            [
                "要素ID",
                "element_id",
            ],
        )

        element_suffix = element_id.split(":")[-1]

        # ====================================================
        # 要素IDの完全一致を最優先する
        # ====================================================

        if element_suffix in element_ids:
            score += 500

        # ====================================================
        # 単位も一致していれば補助的に加点する
        # 単位不一致だけでは除外しない
        # ====================================================

        if fact_matches_expected_unit(
            fact,
            definition,
        ):
            score += 20

        candidates.append(
            {
                "score": score,
                "value": parsed_value,
                "element_id": element_id,
                "label": get_fact_value(
                    fact,
                    [
                        "項目名",
                        "ラベル",
                        "item_name",
                    ],
                ),
                "context_id": get_fact_value(
                    fact,
                    [
                        "コンテキストID",
                        "context_id",
                    ],
                ),
                "unit_id": get_fact_value(
                    fact,
                    [
                        "ユニットID",
                        "unit_id",
                    ],
                ),
                "unit": get_fact_value(
                    fact,
                    [
                        "単位",
                        "unit",
                    ],
                ),
                "source_file": fact.get(
                    "_source_file",
                    "",
                ),
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return candidates[0]




def extract_financial_metrics(
    facts: list[dict[str, str]],
) -> dict[str, dict[str, Any] | None]:
    return {
        metric_name: extract_metric(facts, definition)
        for metric_name, definition in METRIC_DEFINITIONS.items()
    }


def metric_value(
    metrics: dict[str, dict[str, Any] | None],
    name: str,
) -> Any:
    metric = metrics.get(name)

    if not metric:
        return None

    return metric.get("value")


def metric_element_id(
    metrics: dict[str, dict[str, Any] | None],
    name: str,
) -> str:
    metric = metrics.get(name)

    if not metric:
        return ""

    return normalize_text(metric.get("element_id", ""))


# ============================================================
# EDINET財務行の作成
# ============================================================

def build_financial_row(
    document: dict[str, str],
    metrics: dict[str, dict[str, Any] | None],
    status: str,
    error_message: str = "",
) -> list[Any]:
    doc_id = document["_doc_id"]

    security_code = normalize_security_code(
        get_first_value(
            document,
            ["証券コード", "secCode"],
        )
    )

    extracted_count = sum(
        1
        for name, metric in metrics.items()
        if name != "accounting_standard" and metric is not None
    )

    return [
        datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        get_first_value(document, ["提出日時", "submitDateTime"]),
        get_first_value(
            document,
            [
                "対象期間開始日",
                "期間開始日",
                "事業年度開始日",
                "periodStart",
            ],
        ),
        get_first_value(
            document,
            [
                "対象期間終了日",
                "期間終了日",
                "事業年度終了日",
                "periodEnd",
            ],
        ),
        security_code,
        get_first_value(document, ["銘柄名"]),
        get_first_value(document, ["市場"]),
        get_first_value(document, ["提出者名", "filerName"]),
        get_first_value(document, ["EDINETコード", "edinetCode"]),
        doc_id,
        get_first_value(document, ["書類種別", "書類概要"]),
        metric_value(metrics, "accounting_standard") or "",
        safe_round(yen_to_million(metric_value(metrics, "revenue"))),
        safe_round(yen_to_million(metric_value(metrics, "operating_income"))),
        safe_round(yen_to_million(metric_value(metrics, "ordinary_income"))),
        safe_round(yen_to_million(metric_value(metrics, "net_income"))),
        safe_round(yen_to_million(metric_value(metrics, "total_assets"))),
        safe_round(yen_to_million(metric_value(metrics, "net_assets"))),
        safe_round(yen_to_million(metric_value(metrics, "equity"))),
        safe_round(yen_to_million(metric_value(metrics, "operating_cf"))),
        safe_round(yen_to_million(metric_value(metrics, "investing_cf"))),
        safe_round(yen_to_million(metric_value(metrics, "financing_cf"))),
        safe_round(yen_to_million(metric_value(metrics, "cash"))),
        safe_round(metric_value(metrics, "eps")),
        safe_round(metric_value(metrics, "bps")),
        safe_round(metric_value(metrics, "dividend_per_share")),
        safe_round(metric_value(metrics, "shares_issued"), 0),
        extracted_count,
        status,
        error_message[:1000],
        metric_element_id(metrics, "revenue"),
        metric_element_id(metrics, "operating_income"),
        metric_element_id(metrics, "net_income"),
        metric_element_id(metrics, "dividend_per_share"),
        "金融庁EDINET API",
        EDINET_VIEW_URL.format(doc_id=doc_id),
    ]


# ============================================================
# 株式指標シート
# ============================================================

def load_latest_prices(
    service,
    spreadsheet_id: str,
) -> dict[str, dict[str, str]]:
    values = read_sheet(
        service,
        spreadsheet_id,
        PRICE_SHEET_NAME,
    )

    rows = rows_to_dicts(values)
    prices = {}

    for row in rows:
        code = normalize_security_code(
            get_first_value(row, ["証券コード"])
        )

        if not code:
            continue

        prices[code] = row

    return prices


def financial_row_to_dict(
    row: list[Any],
) -> dict[str, Any]:
    return dict(zip(FINANCIAL_HEADERS, row))


def build_indicator_rows(
    financial_rows: list[list[Any]],
    prices: dict[str, dict[str, str]],
) -> list[list[Any]]:
    latest_financials: dict[str, dict[str, Any]] = {}

    for raw_row in financial_rows:
        row = financial_row_to_dict(raw_row)

        if row.get("抽出状態") != "成功":
            continue

        code = normalize_security_code(row.get("証券コード"))

        if not code:
            continue

        existing = latest_financials.get(code)

        if (
            existing is None
            or normalize_text(row.get("対象期間終了日"))
            > normalize_text(existing.get("対象期間終了日"))
        ):
            latest_financials[code] = row

    output_rows = []

    for code, financial in latest_financials.items():
        price_row = prices.get(code)

        if not price_row:
            continue

        close_price = parse_number(
            get_first_value(price_row, ["終値", "終値（円）"])
        )

        if close_price is None:
            continue

        revenue = parse_number(financial.get("売上高（百万円）"))
        operating_income = parse_number(
            financial.get("営業利益（百万円）")
        )
        net_income = parse_number(
            financial.get("親会社株主帰属利益（百万円）")
        )
        total_assets = parse_number(
            financial.get("総資産（百万円）")
        )
        net_assets = parse_number(
            financial.get("純資産（百万円）")
        )
        equity = parse_number(
            financial.get("自己資本（百万円）")
        )
        eps = parse_number(financial.get("EPS（円）"))
        bps = parse_number(financial.get("BPS（円）"))
        dividend = parse_number(financial.get("1株配当（円）"))
        shares_issued = parse_number(
            financial.get("発行済株式数")
        )
        operating_cf = parse_number(
            financial.get("営業CF（百万円）")
        )
        investing_cf = parse_number(
            financial.get("投資CF（百万円）")
        )
        financing_cf = parse_number(
            financial.get("財務CF（百万円）")
        )

        market_cap = None

        if shares_issued is not None:
            market_cap = close_price * shares_issued / 1_000_000

        per = None

        if eps is not None and eps > 0:
            per = close_price / eps

        pbr = None

        if bps is not None and bps > 0:
            pbr = close_price / bps

        roe = None

        if net_income is not None and equity is not None and equity > 0:
            roe = net_income / equity * 100

        roa = None

        if (
            net_income is not None
            and total_assets is not None
            and total_assets > 0
        ):
            roa = net_income / total_assets * 100

        equity_ratio = None

        if equity is not None and total_assets is not None and total_assets > 0:
            equity_ratio = equity / total_assets * 100
        elif (
            net_assets is not None
            and total_assets is not None
            and total_assets > 0
        ):
            equity_ratio = net_assets / total_assets * 100

        operating_margin = None

        if (
            operating_income is not None
            and revenue is not None
            and revenue != 0
        ):
            operating_margin = operating_income / revenue * 100

        net_margin = None

        if (
            net_income is not None
            and revenue is not None
            and revenue != 0
        ):
            net_margin = net_income / revenue * 100

        dividend_yield = None

        if dividend is not None and close_price > 0:
            dividend_yield = dividend / close_price * 100

        payout_ratio = None

        if dividend is not None and eps is not None and eps > 0:
            payout_ratio = dividend / eps * 100

        free_cf = None

        if operating_cf is not None and investing_cf is not None:
            free_cf = operating_cf + investing_cf

        output_rows.append(
            [
                datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                get_first_value(
                    price_row,
                    ["株価基準日", "基準日"],
                ),
                code,
                get_first_value(price_row, ["銘柄名"])
                or financial.get("銘柄名", ""),
                get_first_value(price_row, ["市場"])
                or financial.get("市場", ""),
                safe_round(close_price),
                financial.get("対象期間終了日", ""),
                financial.get("会計基準", ""),
                safe_round(revenue),
                safe_round(operating_income),
                safe_round(net_income),
                safe_round(total_assets),
                safe_round(net_assets),
                safe_round(equity),
                safe_round(eps),
                safe_round(bps),
                safe_round(dividend),
                safe_round(shares_issued, 0),
                safe_round(market_cap),
                safe_round(per),
                safe_round(pbr),
                safe_round(roe),
                safe_round(roa),
                safe_round(equity_ratio),
                safe_round(operating_margin),
                safe_round(net_margin),
                safe_round(dividend_yield),
                safe_round(payout_ratio),
                safe_round(operating_cf),
                safe_round(investing_cf),
                safe_round(free_cf),
                safe_round(financing_cf),
                financial.get("書類管理番号", ""),
                financial.get("EDINET閲覧URL", ""),
            ]
        )

    output_rows.sort(
        key=lambda row: (
            row[4],
            row[2],
        )
    )

    return output_rows


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    service_account_json = get_required_environment_variable(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )
    spreadsheet_id = get_required_environment_variable(
        "GOOGLE_SPREADSHEET_ID"
    )
    discord_webhook_url = get_required_environment_variable(
        "DISCORD_WEBHOOK_URL"
    )
    edinet_api_key = get_required_environment_variable(
        "EDINET_API_KEY"
    )

    max_documents = int(
        os.getenv("MAX_DOCUMENTS", "100")
    )

    service = create_google_sheets_service(
        service_account_json
    )

    # ========================================================
    # 初回実行用シート作成
    #
    # 初回実行時には「EDINET財務」と「株式指標」が存在しないため、
    # 読み込み処理より先に作成する。
    # ========================================================

    get_or_create_sheet(
        service,
        spreadsheet_id,
        FINANCIAL_SHEET_NAME,
    )

    get_or_create_sheet(
        service,
        spreadsheet_id,
        INDICATOR_SHEET_NAME,
    )

    # ========================================================
    # 既存EDINET財務データの読み込み
    # ========================================================

    existing_financial_values = read_sheet(
        service,
        spreadsheet_id,
        FINANCIAL_SHEET_NAME,
    )

    existing_financial_rows = []

    if existing_financial_values:
        existing_headers = existing_financial_values[0]

        for source_row in existing_financial_values[1:]:
            row_dict = dict(
                zip(
                    existing_headers,
                    source_row
                    + [""] * max(
                        0,
                        len(existing_headers) - len(source_row),
                    ),
                )
            )

            existing_financial_rows.append(
                [
                    row_dict.get(header, "")
                    for header in FINANCIAL_HEADERS
                ]
            )

    processed_doc_ids = {
        normalize_text(
            financial_row_to_dict(row).get("書類管理番号")
        )
        for row in existing_financial_rows
        if normalize_text(
            financial_row_to_dict(row).get("書類管理番号")
        )
    }

    target_documents = load_target_documents(
        service,
        spreadsheet_id,
        processed_doc_ids,
        max_documents,
    )

    if not target_documents:
        prices = load_latest_prices(
            service,
            spreadsheet_id,
        )

        indicator_rows = build_indicator_rows(
            existing_financial_rows,
            prices,
        )

        write_sheet(
            service,
            spreadsheet_id,
            INDICATOR_SHEET_NAME,
            INDICATOR_HEADERS,
            indicator_rows,
        )

        send_discord_notification(
            discord_webhook_url,
            "ℹ️ EDINET財務更新対象なし",
            (
                "未処理の有価証券報告書はありません。\n\n"
                f"既存財務データ: {len(existing_financial_rows):,}件\n"
                f"株式指標: {len(indicator_rows):,}銘柄"
            ),
            success=True,
        )

        print("未処理の有価証券報告書はありません。")
        return

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "progressive-dividend-screener/1.0 "
                "(EDINET financial data collector)"
            )
        }
    )

    new_rows = []
    success_count = 0
    failure_count = 0
    missing_metric_count = 0

    for index, document in enumerate(target_documents, start=1):
        doc_id = document["_doc_id"]

        print(
            f"[{index}/{len(target_documents)}] "
            f"{doc_id} を処理しています。"
        )

        try:
            zip_bytes = download_edinet_csv_zip(
                session,
                edinet_api_key,
                doc_id,
            )

            facts = parse_edinet_csv_zip(zip_bytes)

            metrics = extract_financial_metrics(facts)

            extracted_count = sum(
                1
                for name, value in metrics.items()
                if name != "accounting_standard"
                and value is not None
            )

            if extracted_count == 0:
                raise RuntimeError(
                    "対象財務項目を1件も抽出できませんでした。"
                )

            if (
                metric_value(metrics, "revenue") is None
                or metric_value(metrics, "net_income") is None
                or metric_value(metrics, "total_assets") is None
            ):
                missing_metric_count += 1

            new_rows.append(
                build_financial_row(
                    document,
                    metrics,
                    "成功",
                )
            )

            success_count += 1

        except Exception as exc:
            print(
                f"{doc_id} の処理に失敗しました: {exc}"
            )

            empty_metrics = {
                name: None
                for name in METRIC_DEFINITIONS
            }

            new_rows.append(
                build_financial_row(
                    document,
                    empty_metrics,
                    "失敗",
                    str(exc),
                )
            )

            failure_count += 1

        time.sleep(REQUEST_INTERVAL_SECONDS)

    all_financial_rows = existing_financial_rows + new_rows

    all_financial_rows.sort(
        key=lambda row: (
            normalize_text(row[4]),
            normalize_text(row[3]),
            normalize_text(row[9]),
        )
    )

    write_sheet(
        service,
        spreadsheet_id,
        FINANCIAL_SHEET_NAME,
        FINANCIAL_HEADERS,
        all_financial_rows,
    )

    prices = load_latest_prices(
        service,
        spreadsheet_id,
    )

    indicator_rows = build_indicator_rows(
        all_financial_rows,
        prices,
    )

    write_sheet(
        service,
        spreadsheet_id,
        INDICATOR_SHEET_NAME,
        INDICATOR_HEADERS,
        indicator_rows,
    )

    send_discord_notification(
        discord_webhook_url,
        "✅ EDINET財務情報更新完了",
        (
            f"処理対象: {len(target_documents):,}件\n"
            f"成功: {success_count:,}件\n"
            f"失敗: {failure_count:,}件\n"
            f"主要項目に欠損あり: {missing_metric_count:,}件\n"
            f"EDINET財務保存総数: {len(all_financial_rows):,}件\n"
            f"株式指標作成数: {len(indicator_rows):,}銘柄\n\n"
            f"保存先: {FINANCIAL_SHEET_NAME} / "
            f"{INDICATOR_SHEET_NAME}\n"
            "データ出典: 金融庁EDINET API・JPX"
        ),
        success=failure_count == 0,
    )

    print(
        f"完了: 成功={success_count}, "
        f"失敗={failure_count}, "
        f"株式指標={len(indicator_rows)}"
    )



if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"致命的エラー: {exc}")
        traceback.print_exc()

        webhook_url = os.getenv(
            "DISCORD_WEBHOOK_URL",
            "",
        ).strip()

        if webhook_url:
            send_discord_notification(
                webhook_url,
                "❌ EDINET財務情報更新失敗",
                (
                    f"処理中に致命的なエラーが発生しました。\n\n"
                    f"```text\n{str(exc)[:3000]}\n```"
                ),
                success=False,
            )

        sys.exit(1)
