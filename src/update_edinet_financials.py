import csv
import io
import json
import math
import os
import re
import sys
import time
import traceback
import unicodedata
import zipfile
from datetime import datetime, timezone, timedelta
from typing import Any

# ============================================================
# 外部ライブラリ
# ============================================================

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from migrate_edinet_financials import (
    build_financial_database_records,
    save_financials_to_database,
)


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
            "NetIncome",
            "NetIncomeLoss",
            "NetIncomeLossSummaryOfBusinessResults",
        ],
        "labels": [
            "当期純利益",
            "当期純損失",
            "当期純利益又は当期純損失",
            "当期純利益又は当期純損失（△）",
            "親会社株主に帰属する当期純利益",
            "親会社株主に帰属する当期純損失",
            "親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失",
            "親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）",
            "親会社の所有者に帰属する当期利益",
            "親会社の所有者に帰属する当期損失",
            "親会社の所有者に帰属する当期利益（△損失）",
        ],
        "required_label_patterns": [
            "当期純利益",
            "当期純損失",
            "親会社株主に帰属する当期純利益",
            "親会社株主に帰属する当期純損失",
            "親会社の所有者に帰属する当期利益",
            "親会社の所有者に帰属する当期損失",
        ],
        "excluded_label_patterns": [
            "1株当たり",
            "１株当たり",
            "潜在株式調整後",
            "希薄化後",
            "包括利益",
            "四半期",
            "中間期",
            "セグメント",
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
            "1株当たり当期純利益又は1株当たり当期純損失（△）",
            "１株当たり当期純利益又は１株当たり当期純損失（△）",
            "基本的1株当たり当期利益",
            "基本的１株当たり当期利益",
            "基本的1株当たり当期利益（△損失）",
            "基本的１株当たり当期利益（△損失）",
        ],
        "required_label_patterns": [
            "1株当たり当期純利益",
            "１株当たり当期純利益",
            "1株当たり当期純損失",
            "１株当たり当期純損失",
            "基本的1株当たり当期利益",
            "基本的１株当たり当期利益",
        ],
        "excluded_label_patterns": [
            "潜在株式調整後",
            "希薄化後",
            "四半期",
            "中間期",
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
            "TotalNumberOfIssuedSharesSummaryOfBusinessResults",
            "TotalNumberOfIssuedShares",
            "NumberOfIssuedShares",
        ],
        "labels": [
            "発行済株式総数",
            "期末発行済株式数",
            "事業年度末現在発行数",
            "発行済株式数",
        ],
        "required_label_patterns": [
            "発行済株式総数",
            "期末発行済株式数",
            "事業年度末現在発行数",
        ],
        "excluded_label_patterns": [
            "提出日現在",
            "潜在株式",
            "自己株式",
            "新株予約権",
        ],
        "kind": "number",
        "expected_unit": "shares",
        "prefer_consolidated": False,
    },
}

# ============================================================
# IFRS要素・決算期間要素の追加
# ============================================================

def extend_metric_definition(
    metric_name: str,
    key: str,
    values: list[str],
) -> None:
    """
    財務項目定義へ値を重複なしで追加する。
    """
    definition = METRIC_DEFINITIONS[metric_name]
    existing_values = definition.setdefault(key, [])

    for value in values:
        if value not in existing_values:
            existing_values.append(value)


# ============================================================
# IFRSの経営指標等で使用される要素IDを追加
# ============================================================

extend_metric_definition(
    "revenue",
    "element_ids",
    [
        "RevenueIFRSSummaryOfBusinessResults",
        "OperatingRevenueIFRS",
        "OperatingRevenueIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "revenue",
    "labels",
    [
        "売上収益（IFRS）",
        "営業収益（IFRS）",
    ],
)

extend_metric_definition(
    "operating_income",
    "element_ids",
    [
        "OperatingProfitLossIFRS",
        "OperatingProfitLossIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "operating_income",
    "labels",
    [
        "営業利益（IFRS）",
        "営業損失（IFRS）",
    ],
)

extend_metric_definition(
    "net_income",
    "element_ids",
    [
        "ProfitLossIFRS",
        "ProfitLossIFRSSummaryOfBusinessResults",
        "ProfitLossAttributableToOwnersOfParentIFRS",
        "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "net_income",
    "labels",
    [
        "親会社の所有者に帰属する当期利益（IFRS）",
        "親会社の所有者に帰属する当期損失（IFRS）",
        "親会社の所有者に帰属する当期利益（△損失）（IFRS）",
    ],
)

extend_metric_definition(
    "total_assets",
    "element_ids",
    [
        "AssetsIFRS",
        "AssetsIFRSSummaryOfBusinessResults",
        "TotalAssetsIFRS",
        "TotalAssetsIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "total_assets",
    "labels",
    [
        "資産合計（IFRS）",
        "総資産額（IFRS）",
    ],
)

extend_metric_definition(
    "net_assets",
    "element_ids",
    [
        "EquityIFRS",
        "EquityIFRSSummaryOfBusinessResults",
        "TotalEquityIFRS",
        "TotalEquityIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "net_assets",
    "labels",
    [
        "資本合計",
        "資本合計（IFRS）",
    ],
)

extend_metric_definition(
    "equity",
    "element_ids",
    [
        "EquityAttributableToOwnersOfParentIFRS",
        "EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "equity",
    "labels",
    [
        "親会社の所有者に帰属する持分",
        "親会社の所有者に帰属する持分（IFRS）",
        "親会社所有者帰属持分",
    ],
)

extend_metric_definition(
    "eps",
    "element_ids",
    [
        "BasicEarningsLossPerShareIFRS",
        "BasicEarningsPerShareIFRS",
        "BasicEarningsLossPerShareIFRSSummaryOfBusinessResults",
        "BasicEarningsPerShareIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "eps",
    "labels",
    [
        "基本的1株当たり当期利益（IFRS）",
        "基本的１株当たり当期利益（IFRS）",
        "基本的1株当たり当期利益（△損失）（IFRS）",
        "基本的１株当たり当期利益（△損失）（IFRS）",
    ],
)

extend_metric_definition(
    "bps",
    "element_ids",
    [
        "EquityAttributableToOwnersOfParentPerShareIFRS",
        "EquityAttributableToOwnersOfParentPerShareIFRSSummaryOfBusinessResults",
        "EquityPerShareAttributableToOwnersOfParentIFRS",
        "EquityPerShareAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    ],
)

extend_metric_definition(
    "bps",
    "labels",
    [
        "1株当たり親会社所有者帰属持分（IFRS）",
        "１株当たり親会社所有者帰属持分（IFRS）",
    ],
)


# ============================================================
# 対象期間開始日・終了日の定義
# ============================================================

METRIC_DEFINITIONS["period_start"] = {
    "element_ids": [
        "CurrentFiscalYearStartDateDEI",
        "CurrentPeriodStartDateDEI",
    ],
    "labels": [
        "当会計期間開始日、DEI",
        "当事業年度開始日、DEI",
        "当会計期間開始日",
        "当事業年度開始日",
    ],
    "kind": "text",
    "prefer_consolidated": False,
    "standard_sensitive": False,
}

METRIC_DEFINITIONS["period_end"] = {
    "element_ids": [
        "CurrentFiscalYearEndDateDEI",
        "CurrentPeriodEndDateDEI",
    ],
    "labels": [
        "当会計期間終了日、DEI",
        "当事業年度終了日、DEI",
        "当会計期間終了日",
        "当事業年度終了日",
    ],
    "kind": "text",
    "prefer_consolidated": False,
    "standard_sensitive": False,
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

# ============================================================
# PostgreSQL同期
# ============================================================

def save_financial_sheet_rows_to_database(
    headers: list[str],
    rows: list[list[Any]],
) -> int:
    """
    EDINET財務シート形式の行をPostgreSQLへ保存する。

    PostgreSQLへの保存が成功した場合のみ、
    呼び出し元でGoogle Sheetsの更新を続行する。
    """

    normalized_headers = [
        normalize_text(header)
        for header in headers
    ]

    financial_rows: list[
        dict[str, Any]
    ] = []

    for sheet_row_number, original_row in enumerate(
        rows,
        start=2,
    ):
        padded_row = list(
            original_row
        )

        if len(padded_row) < len(normalized_headers):
            padded_row.extend(
                [""] * (
                    len(normalized_headers)
                    - len(padded_row)
                )
            )

        row = dict(
            zip(
                normalized_headers,
                padded_row,
            )
        )

        row["_sheet_row_number"] = (
            sheet_row_number
        )

        financial_rows.append(
            row
        )

    if not financial_rows:
        print(
            "PostgreSQLへ保存する"
            "EDINET財務情報はありません。"
        )
        return 0

    records = build_financial_database_records(
        financial_rows
    )

    saved_count = save_financials_to_database(
        records
    )

    expected_count = len(
        financial_rows
    )

    if saved_count != expected_count:
        raise RuntimeError(
            "EDINET財務のPostgreSQL保存件数が"
            "一致しません。"
            f"期待件数: {expected_count:,}, "
            f"保存件数: {saved_count:,}"
        )

    print(
        "EDINET財務のPostgreSQL同期が"
        "完了しました。"
        f"件数: {saved_count:,}"
    )

    return saved_count


# ============================================================
# シート書き込み
# ============================================================

def write_sheet(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    """
    Googleスプレッドシートを更新する。

    EDINET財務シートの場合は、
    PostgreSQLへの保存成功後にシートを更新する。
    """

    if sheet_name == FINANCIAL_SHEET_NAME:
        database_count = (
            save_financial_sheet_rows_to_database(
                headers,
                rows,
            )
        )

        if database_count != len(rows):
            raise RuntimeError(
                "PostgreSQLへの保存が"
                "完了していないため、"
                "EDINET財務シートを更新しません。"
                f"シート予定件数: {len(rows):,}, "
                f"DB保存確認件数: "
                f"{database_count:,}"
            )

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

    values = [
        headers,
        *rows,
    ]

    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            body={
                "values": values,
            },
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
                            "fields": (
                                "gridProperties."
                                "frozenRowCount"
                            ),
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
                                "userEnteredFormat."
                                "backgroundColor,"
                                "userEnteredFormat."
                                "textFormat"
                            ),
                        }
                    },
                    {
                        "setBasicFilter": {
                            "filter": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": 0,
                                    "endRowIndex": max(
                                        len(values),
                                        1,
                                    ),
                                    "startColumnIndex": 0,
                                    "endColumnIndex": len(
                                        headers
                                    ),
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
                                "endIndex": len(
                                    headers
                                ),
                            }
                        }
                    },
                ]
            },
        )
        .execute()
    )

    print(
        f"{sheet_name}シートを更新しました。"
        f"件数: {len(rows):,}"
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

def normalize_edinet_csv_header(value: Any) -> str:
    """
    EDINET CSVのヘッダーをNFKC正規化し、
    後段で使用する標準的な日本語キーへ統一する。
    """
    normalized = unicodedata.normalize(
        "NFKC",
        normalize_text(value),
    )

    # 比較用として空白を除去し、小文字化する。
    comparison_key = (
        normalized
        .replace(" ", "")
        .replace("\t", "")
        .lower()
    )

    header_aliases = {
        # 要素ID
        "要素id": "要素ID",
        "elementid": "要素ID",
        "element_id": "要素ID",

        # 項目名
        "項目名": "項目名",
        "ラベル": "項目名",
        "label": "項目名",
        "itemname": "項目名",
        "item_name": "項目名",

        # コンテキストID
        "コンテキストid": "コンテキストID",
        "contextid": "コンテキストID",
        "context_id": "コンテキストID",

        # 相対年度
        "相対年度": "相対年度",
        "relativeyear": "相対年度",
        "relative_year": "相対年度",

        # 連結・個別
        "連結・個別": "連結・個別",
        "連結個別": "連結・個別",
        "consolidatedornonconsolidated": "連結・個別",
        "consolidated_or_nonconsolidated": "連結・個別",

        # 期間・時点
        "期間・時点": "期間・時点",
        "期間時点": "期間・時点",
        "periodtype": "期間・時点",
        "period_type": "期間・時点",

        # ユニットID
        "ユニットid": "ユニットID",
        "unitid": "ユニットID",
        "unit_id": "ユニットID",

        # 単位
        "単位": "単位",
        "unit": "単位",

        # 値
        "値": "値",
        "value": "値",
    }

    return header_aliases.get(
        comparison_key,
        normalized,
    )


def decode_edinet_csv(raw: bytes) -> str:
    """
    EDINET CSVを適切な文字コードでデコードする。

    単純にutf-16leから順番に試すだけでは、
    別の文字コードでも例外なく誤デコードされる場合があるため、
    BOMとヘッダー内容も確認する。
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding_candidates = [
            "utf-16",
            "utf-16le",
            "utf-8-sig",
            "utf-8",
        ]
    elif raw.startswith(b"\xef\xbb\xbf"):
        encoding_candidates = [
            "utf-8-sig",
            "utf-8",
            "utf-16le",
            "utf-16",
        ]
    else:
        encoding_candidates = [
            "utf-16le",
            "utf-16",
            "utf-8-sig",
            "utf-8",
        ]

    fallback_text = None

    for encoding in encoding_candidates:
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

        if fallback_text is None:
            fallback_text = decoded

        normalized_preview = unicodedata.normalize(
            "NFKC",
            decoded[:2000],
        )

        # EDINET CSVの代表的なヘッダーが確認できたものを採用する。
        if (
            "\t" in normalized_preview
            and (
                "要素ID" in normalized_preview
                or "項目名" in normalized_preview
                or "コンテキストID" in normalized_preview
            )
        ):
            return decoded

    if fallback_text is not None:
        return fallback_text

    raise RuntimeError(
        "EDINET CSVの文字コードを判定できませんでした。"
    )


def parse_edinet_csv_zip(
    zip_bytes: bytes,
) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []

    # EDINET type=5 CSVの標準的な列順。
    #
    # ヘッダー名に予期しない表記揺れがあった場合でも、
    # 公式CSVの列順から値を復元できるようにする。
    standard_columns = [
        "要素ID",
        "項目名",
        "コンテキストID",
        "相対年度",
        "連結・個別",
        "期間・時点",
        "ユニットID",
        "単位",
        "値",
    ]

    with zipfile.ZipFile(
        io.BytesIO(zip_bytes)
    ) as archive:
        # ====================================================
        # ZIP内の解析対象CSVを取得
        # ====================================================

        csv_files = []

        for filename in archive.namelist():
            lower_filename = filename.lower()

            if not lower_filename.endswith(".csv"):
                continue

            # 監査関連CSVは財務数値抽出の対象外。
            if "jpaud" in lower_filename:
                continue

            csv_files.append(filename)

        if not csv_files:
            raise RuntimeError(
                "ZIP内に解析可能なCSVファイルがありません。"
            )

        print(
            f"解析対象CSV: {len(csv_files)}ファイル"
        )

        # ====================================================
        # CSVファイル単位で読み込み
        # ====================================================

        for filename in csv_files:
            raw = archive.read(filename)
            decoded = decode_edinet_csv(raw)

            # newline=""を指定し、引用符内の改行を
            # csv.readerに正しく処理させる。
            reader = csv.reader(
                io.StringIO(
                    decoded,
                    newline="",
                ),
                delimiter="\t",
                quotechar='"',
                doublequote=True,
            )

            try:
                raw_headers = next(reader)
            except StopIteration:
                print(
                    f"空のCSVのためスキップ: {filename}"
                )
                continue

            normalized_headers = [
                normalize_edinet_csv_header(header)
                for header in raw_headers
            ]

            # ヘッダー名が9列すべて正しく認識されたか確認する。
            missing_headers = [
                header
                for header in standard_columns
                if header not in normalized_headers
            ]

            if missing_headers:
                print(
                    f"CSVヘッダー警告: "
                    f"file={filename}, "
                    f"不足={missing_headers}, "
                    f"raw_headers={raw_headers}"
                )

            file_fact_count = 0
            first_element_id = ""

            for raw_values in reader:
                if not raw_values:
                    continue

                # 列数がヘッダー数より少ない場合は空文字で補完する。
                padded_values = raw_values + [""] * max(
                    0,
                    len(normalized_headers) - len(raw_values),
                )

                normalized_row: dict[str, str] = {}

                # =================================================
                # 正規化したヘッダー名で行データを作成
                # =================================================

                for column_index, header in enumerate(
                    normalized_headers
                ):
                    if not header:
                        continue

                    value = ""

                    if column_index < len(padded_values):
                        value = normalize_text(
                            padded_values[column_index]
                        )

                    normalized_row[header] = value

                # =================================================
                # 公式CSVの列順によるフォールバック
                #
                # ヘッダーに不可視文字などが残っていても、
                # 先頭列の要素IDを確実に取得する。
                # =================================================

                for column_index, standard_column in enumerate(
                    standard_columns
                ):
                    existing_value = normalize_text(
                        normalized_row.get(
                            standard_column,
                            "",
                        )
                    )

                    if existing_value:
                        continue

                    if column_index >= len(raw_values):
                        continue

                    normalized_row[standard_column] = (
                        normalize_text(
                            raw_values[column_index]
                        )
                    )

                # =================================================
                # 英語の統一キーも明示的に設定
                #
                # 現在の後段処理は日本語キーも参照しているが、
                # 診断処理や将来の修正でキーがずれないよう、
                # 両方を必ず保持する。
                # =================================================

                normalized_row["element_id"] = (
                    normalized_row.get(
                        "要素ID",
                        "",
                    )
                )

                normalized_row["label"] = (
                    normalized_row.get(
                        "項目名",
                        "",
                    )
                )

                normalized_row["context_id"] = (
                    normalized_row.get(
                        "コンテキストID",
                        "",
                    )
                )

                normalized_row["relative_year"] = (
                    normalized_row.get(
                        "相対年度",
                        "",
                    )
                )

                normalized_row[
                    "consolidated_or_nonconsolidated"
                ] = normalized_row.get(
                    "連結・個別",
                    "",
                )

                normalized_row["period_type"] = (
                    normalized_row.get(
                        "期間・時点",
                        "",
                    )
                )

                normalized_row["unit_id"] = (
                    normalized_row.get(
                        "ユニットID",
                        "",
                    )
                )

                normalized_row["unit"] = (
                    normalized_row.get(
                        "単位",
                        "",
                    )
                )

                normalized_row["value"] = (
                    normalized_row.get(
                        "値",
                        "",
                    )
                )

                normalized_row["_source_file"] = filename

                facts.append(normalized_row)
                file_fact_count += 1

                if (
                    not first_element_id
                    and normalized_row["element_id"]
                ):
                    first_element_id = (
                        normalized_row["element_id"]
                    )

            # =================================================
            # ファイル単位の要素ID取得確認
            # =================================================

            print(
                f"CSV読込: {filename} "
                f"({file_fact_count:,}行)"
            )

            # =================================================
            # 要素IDを取得できなかった場合のみ警告
            # =================================================

            if not first_element_id:
                print(
                    f"要素ID取得失敗: "
                    f"file={filename}, "
                    f"headers={normalized_headers}"
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
            
    # ========================================================
    # CurrentYearの年次経営指標を優先
    # ========================================================

    if "CurrentYearDuration" in context_id:
        score += 80

    if "CurrentYearInstant" in context_id:
        score += 80

    if "SummaryOfBusinessResults" in element_suffix:
        score += 70

    # 四半期・中間期は年次実績として採用しない
    if "Quarter" in context_id:
        score -= 300

    if "Interim" in context_id:
        score -= 300

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
    次のいずれかで財務項目を識別する。

    1. 標準要素IDの完全一致
    2. 項目名の完全一致
    3. 項目別の必須語を含み、除外語を含まない

    コンテキストや相対年度だけでは一致としない。
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
    # 項目別の除外語を確認
    # ========================================================

    excluded_patterns = [
        normalize_matching_text(pattern)
        for pattern in definition.get(
            "excluded_label_patterns",
            [],
        )
    ]

    if any(
        pattern in normalized_label
        for pattern in excluded_patterns
    ):
        return False

    # ========================================================
    # 項目別の必須語を確認
    # ========================================================

    required_patterns = [
        normalize_matching_text(pattern)
        for pattern in definition.get(
            "required_label_patterns",
            [],
        )
    ]

    if required_patterns:
        return any(
            pattern in normalized_label
            for pattern in required_patterns
        )

    # ========================================================
    # EDINETの定型的な補足文字
    # ========================================================

    allowed_suffixes = [
        "、経営指標等",
        "、主要な経営指標等の推移",
        "、連結経営指標等",
        "、提出会社の経営指標等",
    ]

    for candidate in normalized_candidate_labels:
        for allowed_suffix in allowed_suffixes:
            normalized_suffix = normalize_matching_text(
                allowed_suffix
            )

            if normalized_label == candidate + normalized_suffix:
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
# 会計基準の判定
# ============================================================

def normalize_accounting_standard(value: Any) -> str:
    """
    EDINETの会計基準表記を統一する。
    """
    text = normalize_matching_text(value).lower()

    if not text:
        return ""

    if (
        "ifrs" in text
        or "国際財務報告基準" in text
        or "国際会計基準" in text
    ):
        return "IFRS"

    if (
        "japanesegaap" in text
        or "japangaap" in text
        or "日本基準" in text
        or "企業会計基準" in text
    ):
        return "Japan GAAP"

    if "usgaap" in text or "米国基準" in text:
        return "US GAAP"

    return normalize_text(value)


def detect_accounting_standard(
    facts: list[dict[str, str]],
) -> str:
    """
    AccountingStandardsDEIから書類全体の会計基準を取得する。
    """
    target_element_ids = {
        "AccountingStandardsDEI",
    }

    target_labels = {
        normalize_matching_text("会計基準、DEI"),
        normalize_matching_text("会計基準"),
    }

    candidates = []

    for fact in facts:
        element_id = get_fact_value(
            fact,
            [
                "要素ID",
                "element_id",
                "Element ID",
            ],
        )

        element_suffix = element_id.split(":")[-1]

        label = normalize_matching_text(
            get_fact_value(
                fact,
                [
                    "項目名",
                    "ラベル",
                    "item_name",
                ],
            )
        )

        if (
            element_suffix not in target_element_ids
            and label not in target_labels
        ):
            continue

        value = get_fact_value(
            fact,
            [
                "値",
                "value",
                "Value",
            ],
        )

        accounting_standard = normalize_accounting_standard(
            value
        )

        if not accounting_standard:
            continue

        context_id = get_fact_value(
            fact,
            [
                "コンテキストID",
                "context_id",
            ],
        )

        score = 0

        if element_suffix == "AccountingStandardsDEI":
            score += 200

        if "FilingDateInstant" in context_id:
            score += 50

        candidates.append(
            {
                "score": score,
                "value": accounting_standard,
                "element_id": element_id,
                "context_id": context_id,
            }
        )

    if not candidates:
        return ""

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return candidates[0]["value"]


def get_fact_accounting_standard_scope(
    fact: dict[str, str],
) -> str:
    """
    ファクトがIFRS、日本基準、または会計基準に依存しない
    共通項目のどれに該当するかを判定する。

    戻り値:
        ifrs
        japan_gaap
        neutral
    """
    element_id = get_fact_value(
        fact,
        [
            "要素ID",
            "element_id",
            "Element ID",
        ],
    )

    source_file = normalize_text(
        fact.get("_source_file", "")
    ).lower()

    element_id_lower = element_id.lower()
    element_suffix = element_id.split(":")[-1]
    element_suffix_lower = element_suffix.lower()

    namespace_prefix = ""

    if ":" in element_id:
        namespace_prefix = element_id.split(":", 1)[0].lower()

    # IFRS標準タクソノミまたはIFRS専用要素
    if (
        "jpigp" in namespace_prefix
        or "jpigp" in source_file
        or "ifrs" in element_suffix_lower
        or "ifrs" in element_id_lower
    ):
        return "ifrs"

    # 日本基準の財務諸表タクソノミ
    if (
        "jppfs" in namespace_prefix
        or "jppfs" in source_file
    ):
        return "japan_gaap"

    # 経営指標等にIFRS表記がない場合は日本基準値として扱う。
    #
    # IFRS移行年度の有価証券報告書には、
    # IFRS値と監査対象外の日本基準値が併記される場合がある。
    if "summaryofbusinessresults" in element_suffix_lower:
        if "ifrs" in element_suffix_lower:
            return "ifrs"

        return "japan_gaap"

    return "neutral"


def fact_matches_accounting_standard(
    fact: dict[str, str],
    definition: dict[str, Any],
    accounting_standard: str,
) -> bool:
    """
    書類の採用会計基準と異なる財務ファクトを除外する。

    配当・株式数・DEI・決算期間など、会計基準に依存しない
    項目には適用しない。
    """
    standard_sensitive = definition.get(
        "standard_sensitive",
        definition.get("prefer_consolidated", False),
    )

    if not standard_sensitive:
        return True

    normalized_standard = normalize_accounting_standard(
        accounting_standard
    )

    if not normalized_standard:
        return True

    fact_scope = get_fact_accounting_standard_scope(fact)

    # 発行会社独自要素など、会計基準を断定できない要素は
    # ラベル一致などの条件を満たしていれば候補に残す。
    if fact_scope == "neutral":
        return True

    if normalized_standard == "IFRS":
        return fact_scope == "ifrs"

    if normalized_standard == "Japan GAAP":
        return fact_scope == "japan_gaap"

    # US GAAPなど、現在個別対応していない基準は
    # 明らかなIFRS・日本基準要素だけを除外する。
    return fact_scope == "neutral"


# ============================================================
# 連結・個別の判定
# ============================================================

def parse_boolean_value(value: Any) -> bool | None:
    text = normalize_text(value).lower()

    if text in {
        "true",
        "1",
        "yes",
        "有",
        "あり",
    }:
        return True

    if text in {
        "false",
        "0",
        "no",
        "無",
        "なし",
    }:
        return False

    return None


def detect_consolidated_report(
    facts: list[dict[str, str]],
) -> bool | None:
    """
    EDINETのDEI情報から、その書類に連結財務諸表が
   含まれるかを判定する。

    True:
        連結財務諸表あり

    False:
        非連結・個別のみ

    None:
        判定不能
    """
    target_element_ids = {
        "WhetherConsolidatedFinancialStatementsArePreparedDEI",
    }

    target_labels = {
        normalize_matching_text("連結決算の有無、DEI"),
        normalize_matching_text("連結決算の有無"),
    }

    for fact in facts:
        element_id = get_fact_value(
            fact,
            [
                "要素ID",
                "element_id",
                "Element ID",
            ],
        )

        element_suffix = element_id.split(":")[-1]

        label = normalize_matching_text(
            get_fact_value(
                fact,
                [
                    "項目名",
                    "ラベル",
                    "item_name",
                ],
            )
        )

        if (
            element_suffix not in target_element_ids
            and label not in target_labels
        ):
            continue

        value = get_fact_value(
            fact,
            [
                "値",
                "value",
                "Value",
            ],
        )

        parsed_value = parse_boolean_value(value)

        if parsed_value is not None:
            return parsed_value

    return None


def get_fact_consolidation_scope(
    fact: dict[str, str],
) -> str:
    """
    各ファクトの連結・個別スコープを判定する。

    戻り値:
        consolidated
        non_consolidated
        unknown
    """
    context_id = get_fact_value(
        fact,
        [
            "コンテキストID",
            "context_id",
        ],
    )

    consolidated_type = normalize_matching_text(
        get_fact_value(
            fact,
            [
                "連結・個別",
                "連結個別",
                "consolidated_or_nonconsolidated",
            ],
        )
    )

    # NonConsolidatedMemberにはConsolidatedMemberという
    # 文字列も含まれるため、先に個別を判定する。
    if (
        "NonConsolidatedMember" in context_id
        or consolidated_type in {
            "個別",
            "非連結",
        }
    ):
        return "non_consolidated"

    if (
        "ConsolidatedMember" in context_id
        or consolidated_type == "連結"
    ):
        return "consolidated"

    return "unknown"


def fact_matches_report_scope(
    fact: dict[str, str],
    definition: dict[str, Any],
    is_consolidated_report: bool | None,
) -> bool:
    """
    連結決算企業では個別財務数値を除外し、
    非連結企業では連結財務数値を除外する。

    配当・発行済株式数など、prefer_consolidated=Falseの
    項目は提出会社情報としてこの判定を適用しない。
    """
    if not definition.get("prefer_consolidated"):
        return True

    if is_consolidated_report is None:
        return True

    scope = get_fact_consolidation_scope(fact)

    if is_consolidated_report:
        return scope != "non_consolidated"

    return scope != "consolidated"


# ============================================================
# 財務項目の抽出
# ============================================================

def extract_metric(
    facts: list[dict[str, str]],
    metric_name: str,
    definition: dict[str, Any],
    is_consolidated_report: bool | None,
    accounting_standard: str,
) -> dict[str, Any] | None:
    candidates = []

    element_ids = set(
        definition.get("element_ids", [])
    )

    is_dividend_metric = (
        metric_name == "dividend_per_share"
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

        # ====================================================
        # IFRSと日本基準を混在させない
        # ====================================================

        if not fact_matches_accounting_standard(
            fact,
            definition,
            accounting_standard,
        ):
            continue

        # ====================================================
        # 連結・個別のスコープを統一する
        # ====================================================

        if not fact_matches_report_scope(
            fact,
            definition,
            is_consolidated_report,
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
            # 配当が明示的に「－」の場合は0円
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
                "Element ID",
            ],
        )

        element_suffix = element_id.split(":")[-1]

        context_id = get_fact_value(
            fact,
            [
                "コンテキストID",
                "context_id",
            ],
        )

        fact_accounting_scope = (
            get_fact_accounting_standard_scope(fact)
        )

        # ====================================================
        # 標準要素IDの完全一致を優先
        # ====================================================

        if element_suffix in element_ids:
            score += 500

        # ====================================================
        # 書類の採用会計基準と一致する要素を優先
        # ====================================================

        normalized_standard = normalize_accounting_standard(
            accounting_standard
        )

        if (
            normalized_standard == "IFRS"
            and fact_accounting_scope == "ifrs"
        ):
            score += 250

        if (
            normalized_standard == "Japan GAAP"
            and fact_accounting_scope == "japan_gaap"
        ):
            score += 250

        # ====================================================
        # 純利益は親会社所有者帰属利益を最優先
        #
        # IFRSでは次の2種類が同時に存在する。
        #
        # ProfitLoss:
        #   非支配持分を含む当期利益全体
        #
        # ProfitLossAttributableToOwnersOfParent:
        #   親会社の所有者に帰属する当期利益
        #
        # 株式指標のROE・EPS等で使用する純利益には、
        # 親会社所有者帰属利益を採用する。
        # ====================================================

        if metric_name == "net_income":
            parent_owner_element_ids = {
                "ProfitLossAttributableToOwnersOfParent",
                "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
                "ProfitLossAttributableToOwnersOfParentIFRS",
                "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
            }

            generic_profit_element_ids = {
                "ProfitLoss",
                "ProfitLossSummaryOfBusinessResults",
                "ProfitLossIFRS",
                "ProfitLossIFRSSummaryOfBusinessResults",
            }

            if element_suffix in parent_owner_element_ids:
                score += 500
            elif element_suffix in generic_profit_element_ids:
                score -= 100

            # 財務諸表本表や持分変動表の内訳コンテキストより、
            # ディメンションのない当期全体の値を優先する。
            if context_id == "CurrentYearDuration":
                score += 100
            elif context_id.startswith(
                "CurrentYearDuration_"
            ):
                score -= 200

        # ====================================================
        # 単位一致は補助的な加点
        # ====================================================

        if fact_matches_expected_unit(
            fact,
            definition,
        ):
            score += 20

        # ====================================================
        # 発行済株式数は普通株式を優先
        # ====================================================

        if definition.get("expected_unit") == "shares":
            if "CommonStockMember" in context_id:
                score += 200

            if any(
                marker in context_id
                for marker in [
                    "PreferredStockMember",
                    "ClassA",
                    "ClassB",
                    "StockAcquisitionRights",
                ]
            ):
                score -= 400

            if (
                element_suffix
                == "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc"
            ):
                score += 150

            if (
                element_suffix
                == "TotalNumberOfIssuedSharesSummaryOfBusinessResults"
            ):
                score += 100

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
                "context_id": context_id,
                "scope": get_fact_consolidation_scope(fact),
                "accounting_scope": fact_accounting_scope,
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




# ============================================================
# 財務情報一括抽出
# ============================================================

def extract_financial_metrics(
    facts: list[dict[str, str]],
    is_consolidated_report: bool | None,
    accounting_standard: str,
) -> dict[str, dict[str, Any] | None]:
    metrics = {
        metric_name: extract_metric(
            facts,
            metric_name,
            definition,
            is_consolidated_report,
            accounting_standard,
        )
        for metric_name, definition
        in METRIC_DEFINITIONS.items()
    }

    # AccountingStandardsDEIの表記を統一した値で上書きする。
    if accounting_standard:
        existing_metric = metrics.get(
            "accounting_standard"
        )

        metrics["accounting_standard"] = {
            "score": (
                existing_metric.get("score", 0)
                if existing_metric
                else 0
            ),
            "value": accounting_standard,
            "element_id": (
                existing_metric.get("element_id", "")
                if existing_metric
                else "AccountingStandardsDEI"
            ),
            "label": (
                existing_metric.get("label", "")
                if existing_metric
                else "会計基準、DEI"
            ),
            "context_id": (
                existing_metric.get("context_id", "")
                if existing_metric
                else ""
            ),
            "scope": "unknown",
            "accounting_scope": "neutral",
            "unit_id": "",
            "unit": "",
            "source_file": (
                existing_metric.get("source_file", "")
                if existing_metric
                else ""
            ),
        }

    return metrics



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

    metadata_metric_names = {
        "accounting_standard",
        "period_start",
        "period_end",
    }

    extracted_count = sum(
        1
        for name, metric in metrics.items()
        if (
            name not in metadata_metric_names
            and metric is not None
        )
    )

    period_start = (
        metric_value(metrics, "period_start")
        or get_first_value(
            document,
            [
                "対象期間開始日",
                "期間開始日",
                "事業年度開始日",
                "periodStart",
            ],
        )
    )

    period_end = (
        metric_value(metrics, "period_end")
        or get_first_value(
            document,
            [
                "対象期間終了日",
                "期間終了日",
                "事業年度終了日",
                "periodEnd",
            ],
        )
    )

    return [
        datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        get_first_value(
            document,
            ["提出日時", "submitDateTime"],
        ),
        normalize_text(period_start),
        normalize_text(period_end),
        security_code,
        get_first_value(document, ["銘柄名"]),
        get_first_value(document, ["市場"]),
        get_first_value(
            document,
            ["提出者名", "filerName"],
        ),
        get_first_value(
            document,
            ["EDINETコード", "edinetCode"],
        ),
        doc_id,
        get_first_value(
            document,
            ["書類種別", "書類概要"],
        ),
        metric_value(
            metrics,
            "accounting_standard",
        ) or "",
        safe_round(
            yen_to_million(
                metric_value(metrics, "revenue")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(
                    metrics,
                    "operating_income",
                )
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(
                    metrics,
                    "ordinary_income",
                )
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "net_income")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "total_assets")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "net_assets")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "equity")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "operating_cf")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "investing_cf")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "financing_cf")
            )
        ),
        safe_round(
            yen_to_million(
                metric_value(metrics, "cash")
            )
        ),
        safe_round(metric_value(metrics, "eps")),
        safe_round(metric_value(metrics, "bps")),
        safe_round(
            metric_value(
                metrics,
                "dividend_per_share",
            )
        ),
        safe_round(
            metric_value(metrics, "shares_issued"),
            0,
        ),
        extracted_count,
        status,
        error_message[:1000],
        metric_element_id(metrics, "revenue"),
        metric_element_id(
            metrics,
            "operating_income",
        ),
        metric_element_id(metrics, "net_income"),
        metric_element_id(
            metrics,
            "dividend_per_share",
        ),
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

            # =================================================
            # 会計基準を判定
            # =================================================

            accounting_standard = detect_accounting_standard(
                facts
            )

            if not accounting_standard:
                accounting_standard = "判定不能"

            print(
                f"{doc_id}: "
                f"会計基準={accounting_standard}"
            )

            # =================================================
            # 連結決算の有無を判定
            # =================================================

            is_consolidated_report = detect_consolidated_report(
                facts
            )

            if is_consolidated_report is True:
                consolidation_label = "連結"
            elif is_consolidated_report is False:
                consolidation_label = "非連結"
            else:
                consolidation_label = "判定不能"

            print(
                f"{doc_id}: "
                f"決算範囲={consolidation_label}"
            )

            # =================================================
            # 会計基準と決算範囲を統一して抽出
            # =================================================

            metrics = extract_financial_metrics(
                facts,
                is_consolidated_report,
                accounting_standard,
            )

            # =================================================
            # 最終的に選択された財務項目を確認
            # =================================================

            if doc_id in {
                "S100YYKB",  # 523A セイワホールディングス
                "S100YYQX",  # 277A グロービング
                "S100YTII",  # 4088 エア・ウォーター
            }:
                for selected_metric_name in [
                    "net_income",
                    "net_assets",
                    "equity",
                ]:
                    selected_metric = metrics.get(
                        selected_metric_name
                    )

                    if selected_metric is None:
                        print(
                            f"{doc_id}: "
                            f"最終選択なし "
                            f"metric={selected_metric_name}"
                        )
                        continue

                    print(
                        f"{doc_id}: "
                        f"最終選択 "
                        f"metric={selected_metric_name} | "
                        f"value={selected_metric.get('value')} | "
                        f"element_id="
                        f"{selected_metric.get('element_id')} | "
                        f"context_id="
                        f"{selected_metric.get('context_id')} | "
                        f"accounting_scope="
                        f"{selected_metric.get('accounting_scope')} | "
                        f"scope="
                        f"{selected_metric.get('scope')} | "
                        f"score="
                        f"{selected_metric.get('score')} | "
                        f"source_file="
                        f"{selected_metric.get('source_file')}"
                    )

            metadata_metric_names = {
                "accounting_standard",
                "period_start",
                "period_end",
            }

            extracted_count = sum(
                1
                for name, value in metrics.items()
                if (
                    name not in metadata_metric_names
                    and value is not None
                )
            )

            if extracted_count == 0:
                raise RuntimeError(
                    "対象財務項目を1件も抽出できませんでした。"
                )

            # =================================================
            # 主要項目の欠損判定
            # =================================================

            required_metric_names = [
                "revenue",
                "net_income",
                "total_assets",
                "equity",
                "eps",
                "bps",
            ]

            missing_metric_names = [
                metric_name
                for metric_name in required_metric_names
                if metric_value(
                    metrics,
                    metric_name,
                ) is None
            ]

            if missing_metric_names:
                missing_metric_count += 1

                print(
                    f"{doc_id}: 主要項目欠損="
                    f"{', '.join(missing_metric_names)}"
                )

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

    # ========================================================
    # 不完全行の除外・書類管理番号による重複排除
    # ========================================================

    source_financial_rows = (
        existing_financial_rows
        + new_rows
    )

    financial_rows_by_doc_id: dict[str, list[Any]] = {}

    skipped_incomplete_rows = 0
    duplicate_rows = 0

    for row in source_financial_rows:
        if len(row) < len(FINANCIAL_HEADERS):
            row = row + [""] * (
                len(FINANCIAL_HEADERS) - len(row)
            )

        security_code = normalize_security_code(
            row[4]
        )

        doc_id = normalize_text(
            row[9]
        )

        # 証券コードまたは書類管理番号がない行は保存しない
        if not security_code or not doc_id:
            skipped_incomplete_rows += 1
            continue

        row[4] = security_code

        if doc_id in financial_rows_by_doc_id:
            duplicate_rows += 1

        # 同じ書類管理番号が複数ある場合は、
        # 後から処理した新しい行で置き換える。
        financial_rows_by_doc_id[doc_id] = row

    all_financial_rows = list(
        financial_rows_by_doc_id.values()
    )

    all_financial_rows.sort(
        key=lambda row: (
            normalize_text(row[4]),
            normalize_text(row[3]),
            normalize_text(row[9]),
        )
    )

    print(
        f"不完全行除外: {skipped_incomplete_rows}件"
    )

    print(
        f"重複行統合: {duplicate_rows}件"
    )

    # ============================================================
    # 長時間処理後のGoogle Sheets接続再生成
    # ============================================================
    
    print(
        "Google Sheets接続を"
        "書き込み前に再生成します。"
    )
    
    service = create_google_sheets_service(
        service_account_json
    )

    
    # ============================================================
    # EDINET財務シート更新
    # ============================================================

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
