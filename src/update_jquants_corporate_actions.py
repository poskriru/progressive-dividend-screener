"""
J-Quants API V2の株価四本値からAdjFactorとExRTを取得し、
株式分割・株式併合等と取得保証範囲をPostgreSQLへ保存する。

全銘柄を日付単位で取得する。全対象日の全ページ取得に成功した場合だけ
取得範囲をcompleteとして保存し、途中失敗をadjusted判定へ流用しない。
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests

from database import create_database_connection


JQUANTS_API_URL = "https://api.jquants.com/v2/equities/bars/daily"
JQUANTS_SOURCE = "J-Quants V2"
DATABASE_APPLICATION_NAME = "progressive-dividend-jquants-actions"
REQUEST_TIMEOUT_SECONDS = 120
DEFAULT_REQUESTS_PER_MINUTE = 5
MAX_REQUEST_RETRIES = 5


@dataclass(frozen=True)
class CorporateAction:
    """J-Quantsが返したコーポレートアクション。"""

    security_code: str
    effective_date: date
    adjustment_factor: Decimal
    ex_right_type: str | None


def get_required_environment_variable(name: str) -> str:
    """必須環境変数を取得する。値はログへ出力しない。"""

    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"必須環境変数が設定されていません: {name}")
    return value


def parse_iso_date(value: Any, *, field_name: str) -> date:
    """YYYY-MM-DDをdateへ変換する。"""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise RuntimeError(
            f"{field_name}をYYYY-MM-DD形式として読み込めません。値={text}"
        ) from error


def parse_positive_decimal(value: Any, *, field_name: str) -> Decimal:
    """有限かつ正のDecimalへ変換する。"""

    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(f"{field_name}が数値ではありません。値={value}") from error

    if not number.is_finite() or number <= 0:
        raise RuntimeError(f"{field_name}は有限の正数である必要があります。値={value}")
    return number


def normalize_security_code(value: Any) -> str:
    """J-Quantsの5桁コードをプロジェクトの4桁コードへ合わせる。"""

    code = str(value or "").strip().upper()
    if len(code) == 5 and code.endswith("0"):
        code = code[:4]
    return code


def iter_weekdays(start_date: date, end_date: date) -> Iterable[date]:
    """土日を除く対象日を昇順で返す（祝日はAPIの空応答で扱う）。"""

    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def parse_response_page(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """V2レスポンスからdataと次ページキーを厳格に取得する。"""

    if not isinstance(payload, dict):
        raise RuntimeError("J-Quants応答がJSONオブジェクトではありません。")

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("J-Quants応答にdata配列がありません。")

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise RuntimeError("J-Quantsのdataにオブジェクト以外が含まれています。")
        records.append(item)

    pagination_key = payload.get("pagination_key")
    if pagination_key is None:
        pagination_key = payload.get("paginationKey")

    normalized_key = str(pagination_key).strip() if pagination_key else None
    return records, normalized_key


def calculate_dividend_adjustment(
    annual_dividend: Decimal,
    fiscal_period_end: date,
    as_of_date: date,
    actions: Iterable[CorporateAction],
) -> tuple[Decimal, Decimal]:
    """決算期後から基準日までの分割・併合係数を累積する。

    権利落ち日当日の係数は、その日より前の履歴だけに適用するため、
    ``effective_date > fiscal_period_end``を境界とする。ライツイシューや
    種別不明の非1係数は自動補正せず例外にする。
    """

    factor = Decimal("1")
    for action in actions:
        if not fiscal_period_end < action.effective_date <= as_of_date:
            continue
        if action.ex_right_type not in {"1", "2"}:
            if action.adjustment_factor != Decimal("1"):
                raise RuntimeError(
                    "分割・併合以外のAdjFactorは自動補正できません。"
                    f"ExRT={action.ex_right_type}, date={action.effective_date}"
                )
            continue
        factor *= action.adjustment_factor

    return factor, annual_dividend * factor


def parse_corporate_action(record: dict[str, Any]) -> CorporateAction | None:
    """日次バーを保存対象アクションへ変換する。"""

    security_code = normalize_security_code(record.get("Code"))
    if not security_code:
        raise RuntimeError("J-QuantsレコードのCodeが空です。")

    effective_date = parse_iso_date(record.get("Date"), field_name="Date")
    adjustment_factor = parse_positive_decimal(
        record.get("AdjFactor", 1),
        field_name="AdjFactor",
    )
    raw_ex_right_type = record.get("ExRT")
    ex_right_type = (
        str(raw_ex_right_type).strip()
        if raw_ex_right_type not in (None, "")
        else None
    )

    if ex_right_type not in {None, "1", "2", "3"}:
        raise RuntimeError(f"未知のExRTです。値={ex_right_type}")

    if adjustment_factor == Decimal("1") and ex_right_type is None:
        return None

    return CorporateAction(
        security_code=security_code,
        effective_date=effective_date,
        adjustment_factor=adjustment_factor,
        ex_right_type=ex_right_type,
    )


class RateLimiter:
    """J-Quantsのプラン別レート制限を守る単純な間隔制御。"""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute < 1 or requests_per_minute > 500:
            raise ValueError("requests_per_minuteは1〜500で指定してください。")
        self.minimum_interval = 60.0 / requests_per_minute
        self.last_request_started_at: float | None = None

    def wait(self) -> None:
        """前回リクエストから必要な間隔だけ待機する。"""

        if self.last_request_started_at is not None:
            elapsed = time.monotonic() - self.last_request_started_at
            remaining = self.minimum_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self.last_request_started_at = time.monotonic()


def request_json(
    session: requests.Session,
    params: dict[str, str],
    rate_limiter: RateLimiter,
) -> dict[str, Any]:
    """429と一時的サーバー障害を再試行し、JSONを返す。"""

    last_error: Exception | None = None

    for attempt in range(1, MAX_REQUEST_RETRIES + 1):
        rate_limiter.wait()
        try:
            response = session.get(
                JQUANTS_API_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 429 or 500 <= response.status_code < 600:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    wait_seconds = max(float(retry_after), float(attempt * 5))
                except ValueError:
                    wait_seconds = float(attempt * 5)
                raise requests.HTTPError(
                    f"一時的HTTPエラー: {response.status_code}; retry={wait_seconds}",
                    response=response,
                )

            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("J-Quants応答がJSONオブジェクトではありません。")
            return payload

        except (requests.RequestException, ValueError, RuntimeError) as error:
            last_error = error
            retryable = isinstance(error, (requests.ConnectionError, requests.Timeout))
            if isinstance(error, requests.HTTPError) and error.response is not None:
                retryable = error.response.status_code == 429 or error.response.status_code >= 500

            if not retryable or attempt == MAX_REQUEST_RETRIES:
                break

            retry_after = ""
            if isinstance(error, requests.HTTPError) and error.response is not None:
                retry_after = error.response.headers.get("Retry-After", "")
            try:
                wait_seconds = max(float(retry_after), float(attempt * 5))
            except ValueError:
                wait_seconds = float(attempt * 5)
            print(
                "J-Quants APIを再試行します。"
                f"試行={attempt}/{MAX_REQUEST_RETRIES}, 待機={wait_seconds:.1f}秒",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)

    raise RuntimeError(f"J-Quants APIの取得に失敗しました: {last_error}") from last_error


def fetch_daily_bars(
    session: requests.Session,
    target_date: date,
    rate_limiter: RateLimiter,
) -> list[dict[str, Any]]:
    """指定日の全ページを取得する。"""

    records: list[dict[str, Any]] = []
    pagination_key: str | None = None
    seen_keys: set[str] = set()

    while True:
        params = {"date": target_date.isoformat()}
        if pagination_key:
            params["pagination_key"] = pagination_key

        payload = request_json(session, params, rate_limiter)
        page_records, next_key = parse_response_page(payload)
        records.extend(page_records)

        if not next_key:
            return records
        if next_key in seen_keys:
            raise RuntimeError("J-Quantsのpagination_keyが循環しています。")
        seen_keys.add(next_key)
        pagination_key = next_key


def load_active_security_codes() -> set[str]:
    """補正対象となる有効銘柄コードをDBから取得する。"""

    with create_database_connection(DATABASE_APPLICATION_NAME) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT security_code
                FROM screener.securities
                WHERE is_active IS TRUE
                ORDER BY security_code
                """
            )
            codes = {str(row["security_code"]).strip().upper() for row in cursor.fetchall()}

    if not codes:
        raise RuntimeError("補正対象の有効銘柄がありません。")
    return codes


def determine_required_date_range() -> tuple[date, date]:
    """環境変数または直近5期の最古決算日から必要取得範囲を決める。"""

    configured_from = os.getenv("JQUANTS_ADJUSTMENT_FROM", "").strip()
    configured_to = os.getenv("JQUANTS_ADJUSTMENT_TO", "").strip()
    end_date = (
        parse_iso_date(configured_to, field_name="JQUANTS_ADJUSTMENT_TO")
        if configured_to
        else datetime.now(timezone.utc).date()
    )

    if configured_from:
        start_date = parse_iso_date(
            configured_from,
            field_name="JQUANTS_ADJUSTMENT_FROM",
        )
    else:
        with create_database_connection(DATABASE_APPLICATION_NAME) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH distinct_periods AS (
                        SELECT DISTINCT
                            security_code,
                            fiscal_period_end
                        FROM screener.annual_financials
                        WHERE fiscal_period_end IS NOT NULL
                    ),
                    ranked AS (
                        SELECT
                            security_code,
                            fiscal_period_end,
                            ROW_NUMBER() OVER (
                                PARTITION BY security_code
                                ORDER BY fiscal_period_end DESC
                            ) AS period_rank
                        FROM distinct_periods
                    ),
                    required AS (
                        SELECT MIN(fiscal_period_end) AS required_from
                        FROM ranked
                        WHERE period_rank <= 5
                    ),
                    active_coverage AS (
                        SELECT
                            COUNT(*) AS active_count,
                            COUNT(sync_status.security_code) FILTER (
                                WHERE sync_status.sync_status = 'complete'
                                  AND sync_status.covered_from
                                        <= required.required_from
                            ) AS covered_count,
                            MIN(sync_status.covered_to) AS minimum_covered_to,
                            required.required_from
                        FROM screener.securities AS securities
                        CROSS JOIN required
                        LEFT JOIN screener.jquants_adjustment_sync_status
                            AS sync_status
                            ON sync_status.security_code
                                = securities.security_code
                        WHERE securities.is_active IS TRUE
                        GROUP BY required.required_from
                    )
                    SELECT
                        required_from,
                        active_count,
                        covered_count,
                        minimum_covered_to
                    FROM active_coverage
                    """
                )
                result = cursor.fetchone()

        if result is None or result["required_from"] is None:
            raise RuntimeError("配当補正の必要開始日を決定できません。")

        if (
            int(result["active_count"]) > 0
            and int(result["covered_count"]) == int(result["active_count"])
            and result["minimum_covered_to"] is not None
        ):
            start_date = result["minimum_covered_to"] + timedelta(days=1)
        else:
            # 新規銘柄または取得範囲不足があれば、安全のため必要期間を
            # 全銘柄一括で再取得する。保存はUPSERTなので重複しない。
            start_date = result["required_from"]

    return start_date, end_date


def save_failed_result(
    security_codes: set[str],
    requested_from: date,
    requested_to: date,
    error: Exception,
) -> None:
    """取得失敗を記録し、古いcomplete状態の誤採用を防ぐ。"""

    fetched_at = datetime.now(timezone.utc)
    error_text = f"{type(error).__name__}: {error}"[:1000]
    rows = [
        (
            security_code,
            requested_from,
            requested_to,
            "failed",
            error_text,
            fetched_at,
        )
        for security_code in sorted(security_codes)
    ]

    with create_database_connection(DATABASE_APPLICATION_NAME) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO screener.jquants_adjustment_sync_status (
                    security_code,
                    requested_from,
                    requested_to,
                    sync_status,
                    last_error,
                    fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (security_code)
                DO UPDATE SET
                    requested_from = EXCLUDED.requested_from,
                    requested_to = EXCLUDED.requested_to,
                    sync_status = EXCLUDED.sync_status,
                    last_error = EXCLUDED.last_error,
                    fetched_at = EXCLUDED.fetched_at
                """,
                rows,
            )
        connection.commit()

    print(
        "J-Quants取得失敗状態を保存しました。"
        f"対象銘柄={len(security_codes):,}件",
        file=sys.stderr,
    )


def save_complete_result(
    actions: dict[tuple[str, date], CorporateAction],
    security_codes: set[str],
    observed_ranges: dict[str, tuple[date, date]],
    requested_from: date,
    requested_to: date,
    api_record_counts: dict[str, int],
) -> None:
    """全日取得成功後にだけアクションとcomplete範囲を一括保存する。"""

    fetched_at = datetime.now(timezone.utc)
    action_count_by_code: dict[str, int] = {}
    action_rows: list[tuple[Any, ...]] = []

    for action in actions.values():
        if action.security_code not in security_codes:
            continue
        action_count_by_code[action.security_code] = (
            action_count_by_code.get(action.security_code, 0) + 1
        )
        action_rows.append(
            (
                action.security_code,
                action.effective_date,
                action.adjustment_factor,
                action.ex_right_type,
                JQUANTS_SOURCE,
                JQUANTS_API_URL,
                fetched_at,
            )
        )

    with create_database_connection(DATABASE_APPLICATION_NAME) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                # J-Quants側で過去データが訂正された場合に、以前は
                # アクションだったが現在は通常日に戻った行も消せるよう、
                # 今回完全取得した範囲を入れ替える。
                cursor.execute(
                    """
                    DELETE FROM screener.corporate_actions
                    WHERE source = %s
                      AND effective_date BETWEEN %s AND %s
                    """,
                    (
                        JQUANTS_SOURCE,
                        requested_from,
                        requested_to,
                    ),
                )

                if action_rows:
                    cursor.executemany(
                        """
                        INSERT INTO screener.corporate_actions (
                            security_code,
                            effective_date,
                            adjustment_factor,
                            ex_right_type,
                            source,
                            source_url,
                            fetched_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (security_code, effective_date, source)
                        DO UPDATE SET
                            adjustment_factor = EXCLUDED.adjustment_factor,
                            ex_right_type = EXCLUDED.ex_right_type,
                            source_url = EXCLUDED.source_url,
                            fetched_at = EXCLUDED.fetched_at
                        """,
                        action_rows,
                    )

                status_rows = []
                for security_code in sorted(security_codes):
                    observed = observed_ranges.get(security_code)
                    status_rows.append(
                        (
                            security_code,
                            requested_from,
                            requested_to,
                            requested_from,
                            requested_to,
                            observed[0] if observed else None,
                            observed[1] if observed else None,
                            "complete",
                            api_record_counts.get(security_code, 0),
                            action_count_by_code.get(security_code, 0),
                            fetched_at,
                        )
                    )

                cursor.executemany(
                    """
                    INSERT INTO screener.jquants_adjustment_sync_status (
                        security_code,
                        requested_from,
                        requested_to,
                        covered_from,
                        covered_to,
                        first_observed_date,
                        last_observed_date,
                        sync_status,
                        api_record_count,
                        action_record_count,
                        last_error,
                        fetched_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
                    ON CONFLICT (security_code)
                    DO UPDATE SET
                        requested_from = EXCLUDED.requested_from,
                        requested_to = EXCLUDED.requested_to,
                        covered_from = LEAST(
                            screener.jquants_adjustment_sync_status.covered_from,
                            EXCLUDED.covered_from
                        ),
                        covered_to = GREATEST(
                            screener.jquants_adjustment_sync_status.covered_to,
                            EXCLUDED.covered_to
                        ),
                        first_observed_date = LEAST(
                            screener.jquants_adjustment_sync_status.first_observed_date,
                            EXCLUDED.first_observed_date
                        ),
                        last_observed_date = GREATEST(
                            screener.jquants_adjustment_sync_status.last_observed_date,
                            EXCLUDED.last_observed_date
                        ),
                        sync_status = 'complete',
                        api_record_count = EXCLUDED.api_record_count,
                        action_record_count = EXCLUDED.action_record_count,
                        last_error = NULL,
                        fetched_at = EXCLUDED.fetched_at
                    """,
                    status_rows,
                )

    print(
        "J-Quantsコーポレートアクションを保存しました。"
        f"アクション={len(action_rows):,}件, 対象銘柄={len(security_codes):,}件"
    )


def main() -> None:
    """対象期間の全営業候補日を取得し、完全取得時だけDBへ反映する。"""

    api_key = get_required_environment_variable("JQUANTS_API_KEY")
    raw_rate = os.getenv(
        "JQUANTS_REQUESTS_PER_MINUTE",
        str(DEFAULT_REQUESTS_PER_MINUTE),
    ).strip()
    try:
        requests_per_minute = int(raw_rate)
    except ValueError as error:
        raise RuntimeError("JQUANTS_REQUESTS_PER_MINUTEは整数で指定してください。") from error

    start_date, end_date = determine_required_date_range()
    if start_date > end_date:
        print(
            "J-Quants AdjFactorは最新日まで取得済みです。"
            f"取得済み終端={start_date - timedelta(days=1)}"
        )
        return

    target_dates = list(iter_weekdays(start_date, end_date))
    security_codes = load_active_security_codes()

    if not target_dates:
        # 土日だけの増分範囲には権利落ち日がない。API呼び出しなしで
        # 保証終端を進め、週末にadjusted判定が不必要に無効化されるのを防ぐ。
        save_complete_result(
            {},
            security_codes,
            {},
            start_date,
            end_date,
            {},
        )
        print("J-Quants取得範囲を非取引日分だけ更新しました。")
        return

    print(
        "J-Quants AdjFactor取得を開始します。"
        f"期間={start_date}〜{end_date}, 対象日={len(target_dates):,}, "
        f"上限={requests_per_minute}req/min"
    )

    session = requests.Session()
    session.headers.update({"x-api-key": api_key, "Accept": "application/json"})
    rate_limiter = RateLimiter(requests_per_minute)
    actions: dict[tuple[str, date], CorporateAction] = {}
    observed_ranges: dict[str, tuple[date, date]] = {}
    api_record_counts: dict[str, int] = {}

    try:
        for index, target_date in enumerate(target_dates, start=1):
            records = fetch_daily_bars(session, target_date, rate_limiter)

            for record in records:
                code = normalize_security_code(record.get("Code"))
                if code not in security_codes:
                    continue

                record_date = parse_iso_date(record.get("Date"), field_name="Date")
                current_range = observed_ranges.get(code)
                observed_ranges[code] = (
                    min(current_range[0], record_date) if current_range else record_date,
                    max(current_range[1], record_date) if current_range else record_date,
                )
                api_record_counts[code] = api_record_counts.get(code, 0) + 1

                action = parse_corporate_action(record)
                if action is not None:
                    key = (action.security_code, action.effective_date)
                    existing = actions.get(key)
                    if existing is not None and existing != action:
                        raise RuntimeError(
                            "同一銘柄・日付に矛盾する"
                            "J-Quantsアクションがあります。"
                            f"code={code}, date={record_date}"
                        )
                    actions[key] = action

            if index == 1 or index % 25 == 0 or index == len(target_dates):
                print(
                    "J-Quants取得進捗: "
                    f"{index:,}/{len(target_dates):,}日, "
                    f"日次レコード={len(records):,}, "
                    f"アクション={len(actions):,}"
                )
    except Exception as error:
        save_failed_result(
            security_codes,
            start_date,
            end_date,
            error,
        )
        raise

    if not observed_ranges:
        error = RuntimeError(
            "J-Quantsから対象銘柄を1件も取得できませんでした。"
            "空の応答を完全取得として保存しません。"
        )
        save_failed_result(
            security_codes,
            start_date,
            end_date,
            error,
        )
        raise error

    verified_to = max(
        observed_range[1]
        for observed_range in observed_ranges.values()
    )

    save_complete_result(
        actions,
        security_codes,
        observed_ranges,
        start_date,
        verified_to,
        api_record_counts,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("J-Quantsコーポレートアクション更新に失敗しました。", file=sys.stderr)
        traceback.print_exc()
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
