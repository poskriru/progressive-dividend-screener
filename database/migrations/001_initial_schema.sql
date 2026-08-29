-- ============================================================
-- 累進配当スクリーナー 初期データベーススキーマ
-- ============================================================

-- Supabaseのpublicスキーマとは分離し、
-- アプリケーション専用スキーマへ保存する。
CREATE SCHEMA IF NOT EXISTS screener;

-- Data API用ロールからのアクセスを許可しない。
REVOKE ALL ON SCHEMA screener FROM PUBLIC;
REVOKE ALL ON SCHEMA screener FROM anon;
REVOKE ALL ON SCHEMA screener FROM authenticated;

-- 今後作成するテーブルについても、
-- Data API用ロールへ自動的に権限を付与しない。
ALTER DEFAULT PRIVILEGES IN SCHEMA screener
REVOKE ALL ON TABLES FROM anon, authenticated;

ALTER DEFAULT PRIVILEGES IN SCHEMA screener
REVOKE ALL ON SEQUENCES FROM anon, authenticated;


-- ============================================================
-- updated_at自動更新関数
-- ============================================================

CREATE OR REPLACE FUNCTION screener.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;

$$;


-- ============================================================
-- 銘柄マスター
-- ============================================================

CREATE TABLE IF NOT EXISTS screener.securities (
    security_code text PRIMARY KEY,
    company_name text NOT NULL,
    market text,
    market_category text,
    industry_33_code text,
    industry_33_name text,
    industry_17_code text,
    industry_17_name text,
    scale_code text,
    scale_name text,
    reference_date date,
    is_active boolean NOT NULL DEFAULT true,
    source text NOT NULL DEFAULT 'JPX',
    source_url text,
    source_updated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT securities_security_code_not_blank
        CHECK (btrim(security_code) <> ''),

    CONSTRAINT securities_company_name_not_blank
        CHECK (btrim(company_name) <> '')
);

COMMENT ON TABLE screener.securities IS
'JPX上場銘柄一覧を基準とする銘柄マスター';

COMMENT ON COLUMN screener.securities.security_code IS
'英字を含む場合がある4桁の証券コード';

COMMENT ON COLUMN screener.securities.reference_date IS
'JPX上場銘柄一覧の基準日';

DROP TRIGGER IF EXISTS
    trg_securities_set_updated_at
ON screener.securities;

CREATE TRIGGER trg_securities_set_updated_at
BEFORE UPDATE ON screener.securities
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- 日次株価
-- ============================================================

CREATE TABLE IF NOT EXISTS screener.daily_prices (
    daily_price_id bigint
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    security_code text NOT NULL,
    trading_date date NOT NULL,
    trading_unit numeric(20, 6),
    open_price numeric(20, 6),
    high_price numeric(20, 6),
    low_price numeric(20, 6),
    close_price numeric(20, 6),
    previous_close numeric(20, 6),
    price_change numeric(20, 6),
    change_rate_percent numeric(20, 6),
    final_quote numeric(20, 6),
    vwap numeric(20, 6),
    volume numeric(28, 6),
    turnover_thousand_yen numeric(28, 6),
    adjustment_factor numeric(20, 10),
    adjusted_close_price numeric(20, 6),
    price_status text,
    source text NOT NULL,
    source_url text,
    source_updated_at timestamptz,
    fetched_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT daily_prices_security_code_fk
        FOREIGN KEY (security_code)
        REFERENCES screener.securities (security_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT daily_prices_unique_source_record
        UNIQUE (
            security_code,
            trading_date,
            source
        ),

    CONSTRAINT daily_prices_source_not_blank
        CHECK (btrim(source) <> '')
);

COMMENT ON TABLE screener.daily_prices IS
'JPXおよびJ-Quantsから取得した日次株価履歴';

COMMENT ON COLUMN screener.daily_prices.turnover_thousand_yen IS
'JPX株式相場表に記載された千円単位の売買代金';

COMMENT ON COLUMN screener.daily_prices.source IS
'JPX東京証券取引所日報、J-Quantsなどのデータ出典';

CREATE INDEX IF NOT EXISTS
    idx_daily_prices_trading_date
ON screener.daily_prices (trading_date);

CREATE INDEX IF NOT EXISTS
    idx_daily_prices_security_date
ON screener.daily_prices (
    security_code,
    trading_date DESC
);

DROP TRIGGER IF EXISTS
    trg_daily_prices_set_updated_at
ON screener.daily_prices;

CREATE TRIGGER trg_daily_prices_set_updated_at
BEFORE UPDATE ON screener.daily_prices
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- EDINET書類
-- ============================================================

CREATE TABLE IF NOT EXISTS screener.edinet_documents (
    doc_id text PRIMARY KEY,
    security_code text,
    edinet_code text,
    filer_name text,
    document_type_code text,
    document_type_name text,
    submitted_at timestamptz,
    period_start date,
    period_end date,
    accounting_standard text,
    is_consolidated boolean,
    processing_status text NOT NULL DEFAULT '未処理',
    processed_at timestamptz,
    error_message text,
    source text NOT NULL DEFAULT 'EDINET',
    source_url text,
    fetched_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT edinet_documents_security_code_fk
        FOREIGN KEY (security_code)
        REFERENCES screener.securities (security_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT edinet_documents_doc_id_not_blank
        CHECK (btrim(doc_id) <> '')
);

COMMENT ON TABLE screener.edinet_documents IS
'EDINETから取得した開示書類と処理状態';

COMMENT ON COLUMN screener.edinet_documents.doc_id IS
'EDINET書類管理番号';

CREATE INDEX IF NOT EXISTS
    idx_edinet_documents_security_code
ON screener.edinet_documents (security_code);

CREATE INDEX IF NOT EXISTS
    idx_edinet_documents_submitted_at
ON screener.edinet_documents (submitted_at DESC);

CREATE INDEX IF NOT EXISTS
    idx_edinet_documents_processing_status
ON screener.edinet_documents (processing_status);

DROP TRIGGER IF EXISTS
    trg_edinet_documents_set_updated_at
ON screener.edinet_documents;

CREATE TRIGGER trg_edinet_documents_set_updated_at
BEFORE UPDATE ON screener.edinet_documents
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- 年次財務実績
-- 金額項目は原則として円単位で保存する。
-- Googleスプレッドシート出力時に百万円へ変換する。
-- ============================================================

CREATE TABLE IF NOT EXISTS screener.annual_financials (
    annual_financial_id bigint
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    security_code text NOT NULL,
    doc_id text NOT NULL,
    fiscal_period_start date,
    fiscal_period_end date NOT NULL,
    accounting_standard text,
    is_consolidated boolean,

    revenue_jpy numeric(28, 6),
    operating_profit_jpy numeric(28, 6),
    ordinary_profit_jpy numeric(28, 6),
    net_income_jpy numeric(28, 6),
    total_assets_jpy numeric(28, 6),
    net_assets_jpy numeric(28, 6),
    equity_jpy numeric(28, 6),

    operating_cash_flow_jpy numeric(28, 6),
    investing_cash_flow_jpy numeric(28, 6),
    financing_cash_flow_jpy numeric(28, 6),
    cash_and_equivalents_jpy numeric(28, 6),

    eps_yen numeric(20, 6),
    bps_yen numeric(20, 6),
    annual_dividend_yen numeric(20, 6),
    issued_shares bigint,

    revenue_element_id text,
    operating_profit_element_id text,
    ordinary_profit_element_id text,
    net_income_element_id text,
    total_assets_element_id text,
    net_assets_element_id text,
    equity_element_id text,
    operating_cash_flow_element_id text,
    investing_cash_flow_element_id text,
    financing_cash_flow_element_id text,
    cash_and_equivalents_element_id text,
    eps_element_id text,
    bps_element_id text,
    dividend_element_id text,
    issued_shares_element_id text,

    extracted_item_count integer,
    extraction_status text NOT NULL DEFAULT '未処理',
    extraction_error text,
    source text NOT NULL DEFAULT 'EDINET',
    source_file text,
    source_url text,
    extracted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT annual_financials_security_code_fk
        FOREIGN KEY (security_code)
        REFERENCES screener.securities (security_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT annual_financials_doc_id_fk
        FOREIGN KEY (doc_id)
        REFERENCES screener.edinet_documents (doc_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT annual_financials_doc_id_unique
        UNIQUE (doc_id),

    CONSTRAINT annual_financials_item_count_non_negative
        CHECK (
            extracted_item_count IS NULL
            OR extracted_item_count >= 0
        ),

    CONSTRAINT annual_financials_issued_shares_non_negative
        CHECK (
            issued_shares IS NULL
            OR issued_shares >= 0
        )
);

COMMENT ON TABLE screener.annual_financials IS
'EDINET有価証券報告書から抽出した年次財務実績';

COMMENT ON COLUMN screener.annual_financials.revenue_jpy IS
'円単位の売上高またはIFRS売上収益';

COMMENT ON COLUMN screener.annual_financials.net_income_jpy IS
'円単位の親会社株主または親会社所有者帰属利益';

COMMENT ON COLUMN screener.annual_financials.equity_jpy IS
'円単位の自己資本または親会社所有者帰属持分';

CREATE INDEX IF NOT EXISTS
    idx_annual_financials_security_period
ON screener.annual_financials (
    security_code,
    fiscal_period_end DESC
);

CREATE INDEX IF NOT EXISTS
    idx_annual_financials_extraction_status
ON screener.annual_financials (extraction_status);

DROP TRIGGER IF EXISTS
    trg_annual_financials_set_updated_at
ON screener.annual_financials;

CREATE TRIGGER trg_annual_financials_set_updated_at
BEFORE UPDATE ON screener.annual_financials
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- 最終権限制御
-- ============================================================

REVOKE ALL ON ALL TABLES IN SCHEMA screener
FROM anon, authenticated;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA screener
FROM anon, authenticated;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA screener
FROM anon, authenticated;
