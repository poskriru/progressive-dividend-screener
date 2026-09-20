-- ============================================================
-- 010_tdnet_pdf_policy_analysis.sql
--
-- TDnetの配当・株主還元方針候補について、
-- PDF本文の取得状態、本文抽出結果、機械判定と
-- 判定根拠を監査可能な形で保存する。
--
-- PDFファイル本体および抽出した全文は保存せず、
-- SHA-256、文字数、判定根拠となった短い文章を保存する。
-- ============================================================


-- ============================================================
-- TDnet PDF本文解析結果
-- ============================================================

CREATE TABLE IF NOT EXISTS
    screener.tdnet_policy_pdf_analyses
(
    disclosure_id text PRIMARY KEY,

    security_code text NOT NULL,
    published_date date NOT NULL,
    published_time time,
    company_name text NOT NULL,
    title text NOT NULL,
    pdf_url text NOT NULL,

    content_sha256 text,
    content_type text,
    content_length_bytes bigint,
    http_status_code integer,
    page_count integer,
    extracted_text_length integer,

    analysis_status text
        NOT NULL
        DEFAULT 'pending',

    policy_classification text,
    matched_phrase text,
    evidence_text text,
    evidence_page_number integer,

    analyzer_version text
        NOT NULL
        DEFAULT 'v1',

    fetch_attempt_count integer
        NOT NULL
        DEFAULT 0,

    fetched_at timestamptz,
    analyzed_at timestamptz,
    last_error text,

    created_at timestamptz
        NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at timestamptz
        NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT tdnet_policy_pdf_security_code_fk
        FOREIGN KEY (security_code)
        REFERENCES screener.securities (security_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT tdnet_policy_pdf_disclosure_id_not_blank
        CHECK (
            btrim(disclosure_id) <> ''
        ),

    CONSTRAINT tdnet_policy_pdf_security_code_not_blank
        CHECK (
            btrim(security_code) <> ''
        ),

    CONSTRAINT tdnet_policy_pdf_company_name_not_blank
        CHECK (
            btrim(company_name) <> ''
        ),

    CONSTRAINT tdnet_policy_pdf_title_not_blank
        CHECK (
            btrim(title) <> ''
        ),

    CONSTRAINT tdnet_policy_pdf_url_not_blank
        CHECK (
            btrim(pdf_url) <> ''
        ),

    CONSTRAINT tdnet_policy_pdf_url_https
        CHECK (
            pdf_url ~ '^https://'
        ),

    CONSTRAINT tdnet_policy_pdf_sha256_format
        CHECK (
            content_sha256 IS NULL
            OR content_sha256 ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT tdnet_policy_pdf_content_length_non_negative
        CHECK (
            content_length_bytes IS NULL
            OR content_length_bytes >= 0
        ),

    CONSTRAINT tdnet_policy_pdf_http_status_range
        CHECK (
            http_status_code IS NULL
            OR http_status_code BETWEEN 100 AND 599
        ),

    CONSTRAINT tdnet_policy_pdf_page_count_non_negative
        CHECK (
            page_count IS NULL
            OR page_count >= 0
        ),

    CONSTRAINT tdnet_policy_pdf_text_length_non_negative
        CHECK (
            extracted_text_length IS NULL
            OR extracted_text_length >= 0
        ),

    CONSTRAINT tdnet_policy_pdf_analysis_status_allowed
        CHECK (
            analysis_status IN (
                'pending',
                'completed',
                'fetch_failed',
                'text_extraction_failed'
            )
        ),

    CONSTRAINT tdnet_policy_pdf_classification_allowed
        CHECK (
            policy_classification IS NULL
            OR policy_classification IN (
                'confirmed',
                'not_confirmed',
                'manual_review'
            )
        ),

    CONSTRAINT tdnet_policy_pdf_completed_has_classification
        CHECK (
            analysis_status <> 'completed'
            OR policy_classification IS NOT NULL
        ),

    CONSTRAINT tdnet_policy_pdf_incomplete_has_no_classification
        CHECK (
            analysis_status = 'completed'
            OR policy_classification IS NULL
        ),

    CONSTRAINT tdnet_policy_pdf_evidence_page_positive
        CHECK (
            evidence_page_number IS NULL
            OR evidence_page_number >= 1
        ),

    CONSTRAINT tdnet_policy_pdf_analyzer_version_not_blank
        CHECK (
            btrim(analyzer_version) <> ''
        ),

    CONSTRAINT tdnet_policy_pdf_attempt_count_non_negative
        CHECK (
            fetch_attempt_count >= 0
        )
);


-- ============================================================
-- コメント
-- ============================================================

COMMENT ON TABLE
    screener.tdnet_policy_pdf_analyses
IS
'TDnet配当・株主還元方針候補のPDF本文取得状態、機械判定、判定根拠を保存する。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.disclosure_id
IS
'TDnet PDFファイル名から取得した一意の開示ID。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.content_sha256
IS
'取得したPDFファイル内容のSHA-256。PDF本体はDBへ保存しない。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.analysis_status
IS
'pending、completed、fetch_failed、text_extraction_failedのいずれか。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.policy_classification
IS
'本文判定。confirmed、not_confirmed、manual_reviewのいずれか。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.matched_phrase
IS
'本文判定で一致した累進配当方針表現。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.evidence_text
IS
'本文判定の根拠となった短い文章。PDF全文は保存しない。';

COMMENT ON COLUMN
    screener.tdnet_policy_pdf_analyses.analyzer_version
IS
'再解析時に判定ロジックを識別するためのバージョン。';


-- ============================================================
-- インデックス
-- ============================================================

CREATE INDEX IF NOT EXISTS
    idx_tdnet_policy_pdf_security_date
ON screener.tdnet_policy_pdf_analyses (
    security_code,
    published_date DESC,
    disclosure_id
);

CREATE INDEX IF NOT EXISTS
    idx_tdnet_policy_pdf_analysis_status
ON screener.tdnet_policy_pdf_analyses (
    analysis_status,
    published_date DESC
);

CREATE INDEX IF NOT EXISTS
    idx_tdnet_policy_pdf_classification
ON screener.tdnet_policy_pdf_analyses (
    policy_classification,
    published_date DESC
);


-- ============================================================
-- updated_at自動更新
-- ============================================================

DROP TRIGGER IF EXISTS
    trg_tdnet_policy_pdf_analyses_set_updated_at
ON screener.tdnet_policy_pdf_analyses;

CREATE TRIGGER
    trg_tdnet_policy_pdf_analyses_set_updated_at
BEFORE UPDATE
ON screener.tdnet_policy_pdf_analyses
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL
ON screener.tdnet_policy_pdf_analyses
FROM PUBLIC, anon, authenticated;
