-- ============================================================
-- 007_jpx_corporate_action_sources.sql
--
-- JPXが無料公開する月次PDFおよび日次Excelについて、
-- 取得元ファイル、内容ハッシュ、保証対象期間、取得結果を保存する。
--
-- corporate action本体は既存の
-- screener.corporate_actionsへsource='JPX'として保存する。
-- ============================================================


-- ============================================================
-- JPX取得元ファイル・保証範囲
-- ============================================================

CREATE TABLE screener.jpx_corporate_action_source_files (
    source_file_id bigint
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    source_kind text NOT NULL,

    coverage_start date NOT NULL,
    coverage_end date NOT NULL,

    publication_date date NOT NULL,

    source_url text NOT NULL,
    content_sha256 text,

    sync_status text NOT NULL,

    record_count integer NOT NULL DEFAULT 0,
    last_error text,

    fetched_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT jpx_corporate_action_source_files_url_unique
        UNIQUE (source_url),

    CONSTRAINT jpx_corporate_action_source_kind_valid
        CHECK (
            source_kind IN (
                'monthly_pdf',
                'daily_excel'
            )
        ),

    CONSTRAINT jpx_corporate_action_source_period_valid
        CHECK (
            coverage_start <= coverage_end
        ),

    CONSTRAINT jpx_corporate_action_source_url_not_blank
        CHECK (
            btrim(source_url) <> ''
        ),

    CONSTRAINT jpx_corporate_action_source_status_valid
        CHECK (
            sync_status IN (
                'complete',
                'failed'
            )
        ),

    CONSTRAINT jpx_corporate_action_source_record_count_valid
        CHECK (
            record_count >= 0
        ),

    CONSTRAINT jpx_corporate_action_source_hash_valid
        CHECK (
            content_sha256 IS NULL
            OR content_sha256 ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT jpx_corporate_action_source_result_consistent
        CHECK (
            (
                sync_status = 'complete'
                AND content_sha256 IS NOT NULL
                AND last_error IS NULL
            )
            OR
            (
                sync_status = 'failed'
                AND last_error IS NOT NULL
            )
        )
);


-- ============================================================
-- インデックス
-- ============================================================

CREATE INDEX
    jpx_corporate_action_source_files_coverage_idx
ON screener.jpx_corporate_action_source_files (
    coverage_start,
    coverage_end
)
WHERE sync_status = 'complete';


CREATE INDEX
    jpx_corporate_action_source_files_kind_publication_idx
ON screener.jpx_corporate_action_source_files (
    source_kind,
    publication_date DESC
);


-- ============================================================
-- updated_at自動更新
-- ============================================================

CREATE TRIGGER
    set_jpx_corporate_action_source_files_updated_at
BEFORE UPDATE
ON screener.jpx_corporate_action_source_files
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- コメント
-- ============================================================

COMMENT ON TABLE
    screener.jpx_corporate_action_source_files
IS
    'JPX月次PDF・日次Excelの取得結果とcorporate action保証範囲。';


COMMENT ON COLUMN
    screener.jpx_corporate_action_source_files.source_kind
IS
    '取得元種別。monthly_pdfまたはdaily_excel。';


COMMENT ON COLUMN
    screener.jpx_corporate_action_source_files.coverage_start
IS
    '取得元ファイルが完全性を保証する期間の開始日。';


COMMENT ON COLUMN
    screener.jpx_corporate_action_source_files.coverage_end
IS
    '取得元ファイルが完全性を保証する期間の終了日。';


COMMENT ON COLUMN
    screener.jpx_corporate_action_source_files.content_sha256
IS
    '取得した元ファイルのSHA-256。内容変更の検知に使用する。';


COMMENT ON COLUMN
    screener.jpx_corporate_action_source_files.sync_status
IS
    '取得結果。completeまたはfailed。';


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL
ON screener.jpx_corporate_action_source_files
FROM PUBLIC, anon, authenticated;


REVOKE ALL
ON SEQUENCE
    screener.jpx_corporate_action_source_files_source_file_id_seq
FROM PUBLIC, anon, authenticated;
