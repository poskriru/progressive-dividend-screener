"""自己株式数抽出の調査スクリプト（一時利用）。

指定されたEDINET書類のCSV替換データから、
自己株式・TreasuryStockに関連するファクトを
要素ID・単位・値つきでダンプする。
"""

import io
import os
import sys
from collections import Counter
from urllib.parse import urlparse

import requests

sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace"
)

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__)),
)

from update_edinet_financials import (
    download_edinet_csv_zip,
    parse_edinet_csv_zip,
)


def collect_matching_facts(
    facts: list[dict[str, str]],
) -> list[dict[str, str]]:
    matched = []

    for fact in facts:
        element_id = fact.get("要素ID", "") or fact.get(
            "element_id", ""
        )
        label = fact.get("項目名", "") or fact.get(
            "label", ""
        )
        unit = fact.get("単位", "") or fact.get("unit", "")

        element_lower = element_id.lower()

        if (
            "自己株式" in label
            or "treasury" in element_lower
            or (
                "自己" in label
                and ("株" in unit or "share" in unit.lower())
            )
        ):
            matched.append(fact)

    return matched


def describe_fact(fact: dict[str, str]) -> str:
    element_id = fact.get("要素ID", "") or fact.get(
        "element_id", ""
    )
    label = fact.get("項目名", "") or fact.get(
        "label", ""
    )
    context_id = fact.get("コンテキストID", "") or fact.get(
        "context_id", ""
    )
    relative_year = fact.get("相対年度", "") or fact.get(
        "relative_year", ""
    )
    consolidated = fact.get("連結・個別", "") or fact.get(
        "consolidated_or_nonconsolidated", ""
    )
    unit = fact.get("単位", "") or fact.get("unit", "")
    unit_id = fact.get("ユニットID", "") or fact.get(
        "unit_id", ""
    )
    value = fact.get("値", "") or fact.get("value", "")

    if len(value) > 18:
        value = value[:18] + "..."

    return (
        f"{element_id} | {label} | 単位={unit} "
        f"ユニット={unit_id} | {relative_year} "
        f"{consolidated} | {context_id} | 値={value}"
    )


def main() -> None:
    api_key = os.environ.get("EDINET_API_KEY", "")
    raw_doc_ids = os.environ.get("DOC_IDS", "")

    doc_ids = [
        doc_id.strip()
        for doc_id in raw_doc_ids.split(",")
        if doc_id.strip()
    ]

    if not api_key or not doc_ids:
        raise SystemExit(
            "EDINET_API_KEYとDOC_IDSが必要です。"
        )

    element_unit_counter: Counter = Counter()

    session = requests.Session()

    for doc_id in doc_ids:
        print(f"===== {doc_id} =====", flush=True)

        try:
            zip_bytes = download_edinet_csv_zip(
                session,
                api_key,
                doc_id,
            )
        except Exception as exc:
            print(
                f"ダウンロード失敗: {exc}",
                flush=True,
            )
            continue

        facts = parse_edinet_csv_zip(zip_bytes)

        matched = collect_matching_facts(facts)

        print(
            f"ファクト総数: {len(facts):,} / "
            f"自己株式関連: {len(matched):,}",
            flush=True,
        )

        doc_counter: Counter = Counter()

        for fact in matched:
            element_id = fact.get("要素ID", "") or ""
            unit = fact.get("単位", "") or fact.get(
                "unit", ""
            )
            unit_id = fact.get("ユニットID", "") or ""

            doc_counter[
                (element_id, unit, unit_id)
            ] += 1

        for (
            (element_id, unit, unit_id),
            count,
        ), description in sorted(
            doc_counter.items(),
            key=lambda item: -item[1],
        ):
            print(
                f"  組合せ: {element_id} | 単位={unit} "
                f"ユニット={unit_id} | 件数={count}",
                flush=True,
            )

        for fact in matched[:25]:
            print(f"  {describe_fact(fact)}", flush=True)

        for (
            (element_id, unit, unit_id),
            count,
        ) in doc_counter.items():
            element_unit_counter[
                (element_id, unit)
            ] += count

    print("===== 集計 =====", flush=True)

    for (
        (element_id, unit),
        count,
    ) in element_unit_counter.most_common(60):
        print(
            f"合計: {element_id} | 単位={unit} | "
            f"件数={count:,}",
            flush=True,
        )


if __name__ == "__main__":
    main()
