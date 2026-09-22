-- ============================================================
-- 013_jpx_current_rights_watchlist.sql
--
-- JPXが無料公開する当月の権利処理ページを監視し、
-- 月次PDFへ収録される前の株式分割候補銘柄を保存する。
--
-- 当月ページの日付は月次PDFの権利落ち日とは限らないため、
-- corporate_actionsへ直接登録しない。
--
-- 月次PDFで正式な権利落ち日と調整係数が確定するまで、
-- Discord候補検索から保守的に除外するために使用する。
-- ============================================================


-- ============================================================
-- JPX当月権利処理ページの取得結果
-- ============================================================

CREATE TABLE
    screener.jpx_current_rights_page_snapshots (
        coverage_month date PRIMARY KEY,

        page_url text NOT NULL,

        content_sha256 text NOT NULL,

        source_count integer NOT NULL,

        record_count integer NOT NULL,

        fetched_at timestamptz
            NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        created_at timestamptz
            NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        updated_at timestamptz
            NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        CONSTRAINT
            jpx_current_rights_page_month_start
        CHECK (
            coverage_month
                = DATE_TRUNC(
                    'month',
                    coverage_month
                )::date
        ),

        CONSTRAINT
            jpx_current_rights_page_url_not_blank
        CHECK (
            BTRIM(page_url) <> ''
        ),

        CONSTRAINT
            jpx_current_rights_page_hash_valid
        CHECK (
            content_sha256
                ~ '^[0-9a-f]{64}$'
        ),

        CONSTRAINT
            jpx_current_rights_page_source_count_valid
        CHECK (
            source_count > 0
        ),

        CONSTRAINT
            jpx_current_rights_page_record_count_valid
        CHECK (
            record_count >= 0
        )
    );


-- ============================================================
-- 月次PDF確定待ち銘柄
-- ============================================================

CREATE TABLE
    screener.jpx_current_rights_watchlist (
        coverage_month date NOT NULL,

        security_code text NOT NULL,

        allocation_date date NOT NULL,

        adjustment_factor numeric(20, 10)
            NOT NULL,

        source_url text NOT NULL,

        fetched_at timestamptz
            NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        created_at timestamptz
            NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        updated_at timestamptz
            NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        PRIMARY KEY (
            coverage_month,
            security_code,
            allocation_date,
            source_url
        ),

        CONSTRAINT
            jpx_current_rights_watchlist_snapshot_fk
        FOREIGN KEY (
            coverage_month
        )
        REFERENCES
            screener.jpx_current_rights_page_snapshots (
                coverage_month
            )
        ON DELETE CASCADE,

        CONSTRAINT
            jpx_current_rights_watchlist_month_start
        CHECK (
            coverage_month
                = DATE_TRUNC(
                    'month',
                    coverage_month
                )::date
        ),

        CONSTRAINT
            jpx_current_rights_watchlist_date_month
        CHECK (
            allocation_date
                >= coverage_month
            AND allocation_date
                < (
                    coverage_month
                    + INTERVAL '1 month'
                )::date
        ),

        CONSTRAINT
            jpx_current_rights_watchlist_code_valid
        CHECK (
            security_code
                ~ '^[0-9A-Z]{4}$'
        ),

        CONSTRAINT
            jpx_current_rights_watchlist_factor_valid
        CHECK (
            adjustment_factor > 0
        ),

        CONSTRAINT
            jpx_current_rights_watchlist_url_not_blank
        CHECK (
            BTRIM(source_url) <> ''
        )
    );


-- ============================================================
-- 検索用インデックス
-- ============================================================

CREATE INDEX
    jpx_current_rights_watchlist_security_idx
ON screener.jpx_current_rights_watchlist (
    security_code,
    allocation_date
);


CREATE INDEX
    jpx_current_rights_watchlist_month_idx
ON screener.jpx_current_rights_watchlist (
    coverage_month,
    security_code
);


CREATE INDEX
    jpx_current_rights_snapshots_fetched_idx
ON screener.jpx_current_rights_page_snapshots (
    fetched_at DESC
);


-- ============================================================
-- updated_at自動更新
-- ============================================================

CREATE TRIGGER
    set_jpx_current_rights_page_snapshots_updated_at
BEFORE UPDATE
ON screener.jpx_current_rights_page_snapshots
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


CREATE TRIGGER
    set_jpx_current_rights_watchlist_updated_at
BEFORE UPDATE
ON screener.jpx_current_rights_watchlist
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- コメント
-- ============================================================

COMMENT ON TABLE
    screener.jpx_current_rights_page_snapshots
IS
    'JPX当月権利処理ページと参照CSVの取得結果。月次PDF公開前の完全性確認に使用する。';


COMMENT ON COLUMN
    screener.jpx_current_rights_page_snapshots.coverage_month
IS
    'JPX当月権利処理ページが対象とする月の月初日。';


COMMENT ON COLUMN
    screener.jpx_current_rights_page_snapshots.content_sha256
IS
    'HTML、参照CSVのURLおよびCSV内容を結合して計算したSHA-256。';


COMMENT ON COLUMN
    screener.jpx_current_rights_page_snapshots.source_count
IS
    '当月ページから検出して正常取得したCSV数。';


COMMENT ON COLUMN
    screener.jpx_current_rights_page_snapshots.record_count
IS
    '当月ページのCSVから抽出した企業行動候補件数。';


COMMENT ON TABLE
    screener.jpx_current_rights_watchlist
IS
    '月次PDFで正式確定するまでDiscord候補検索から除外するJPX当月企業行動候補。';


COMMENT ON COLUMN
    screener.jpx_current_rights_watchlist.allocation_date
IS
    'JPX当月ページの見出しに記載された割当日。権利落ち日として使用してはならない。';


COMMENT ON COLUMN
    screener.jpx_current_rights_watchlist.adjustment_factor
IS
    'JPX当月CSVの分割比率から計算した参考係数。月次PDF確定前は配当補正へ使用しない。';


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL
ON screener.jpx_current_rights_page_snapshots
FROM PUBLIC, anon, authenticated;


REVOKE ALL
ON screener.jpx_current_rights_watchlist
FROM PUBLIC, anon, authenticated;
