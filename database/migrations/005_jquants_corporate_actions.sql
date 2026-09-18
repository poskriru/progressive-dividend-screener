-- ============================================================
-- 005_jquants_corporate_actions.sql
--
-- J-Quants V2の日次株価から取得した株式分割・株式併合等と、
-- 銘柄ごとの取得範囲を保存する。
-- ============================================================

CREATE TABLE screener.corporate_actions (
    corporate_action_id bigint
        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    security_code text NOT NULL,
    effective_date date NOT NULL,
    adjustment_factor numeric(20, 10) NOT NULL,
    ex_right_type text,
    source text NOT NULL DEFAULT 'J-Quants V2',
    source_url text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT corporate_actions_security_code_fk
        FOREIGN KEY (security_code)
        REFERENCES screener.securities (security_code)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT corporate_actions_unique_source_record
        UNIQUE (security_code, effective_date, source),

    CONSTRAINT corporate_actions_adjustment_factor_positive
        CHECK (adjustment_factor > 0),

    CONSTRAINT corporate_actions_ex_right_type_valid
        CHECK (
            ex_right_type IS NULL
            OR ex_right_type IN ('1', '2', '3')
        ),

    CONSTRAINT corporate_actions_source_not_blank
        CHECK (btrim(source) <> ''),

    CONSTRAINT corporate_actions_source_url_not_blank
        CHECK (btrim(source_url) <> '')
);

COMMENT ON TABLE screener.corporate_actions IS
'J-Quants V2株価四本値のAdjFactorまたはExRTが示すコーポレートアクション。';

COMMENT ON COLUMN screener.corporate_actions.effective_date IS
'J-Quantsの権利落ち日。係数はこの日より前の配当履歴へ適用する。';

COMMENT ON COLUMN screener.corporate_actions.adjustment_factor IS
'J-Quants AdjFactor。1:2分割は0.5、株式併合は1より大きくなり得る。';

COMMENT ON COLUMN screener.corporate_actions.ex_right_type IS
'J-Quants ExRT。1:株式分割、2:株式併合、3:ライツイシュー。3は自動配当補正の対象外。';

CREATE INDEX idx_corporate_actions_security_date
ON screener.corporate_actions (
    security_code,
    effective_date
);

CREATE TRIGGER trg_corporate_actions_set_updated_at
BEFORE UPDATE ON screener.corporate_actions
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- J-Quants取得範囲
-- ============================================================

CREATE TABLE screener.jquants_adjustment_sync_status (
    security_code text PRIMARY KEY,
    requested_from date NOT NULL,
    requested_to date NOT NULL,
    covered_from date,
    covered_to date,
    first_observed_date date,
    last_observed_date date,
    sync_status text NOT NULL,
    api_record_count integer NOT NULL DEFAULT 0,
    action_record_count integer NOT NULL DEFAULT 0,
    last_error text,
    fetched_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT jquants_adjustment_sync_security_code_fk
        FOREIGN KEY (security_code)
        REFERENCES screener.securities (security_code)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT jquants_adjustment_sync_date_order
        CHECK (requested_from <= requested_to),

    CONSTRAINT jquants_adjustment_sync_coverage_order
        CHECK (
            covered_from IS NULL
            OR covered_to IS NULL
            OR covered_from <= covered_to
        ),

    CONSTRAINT jquants_adjustment_sync_status_valid
        CHECK (sync_status IN ('complete', 'partial', 'failed')),

    CONSTRAINT jquants_adjustment_sync_counts_non_negative
        CHECK (
            api_record_count >= 0
            AND action_record_count >= 0
        )
);

COMMENT ON TABLE screener.jquants_adjustment_sync_status IS
'銘柄別のJ-Quants AdjFactor取得要求範囲、実取得範囲、成否。adjusted判定の採用可否に使用する。';

COMMENT ON COLUMN screener.jquants_adjustment_sync_status.covered_from IS
'API応答が要求開始付近まで存在し、全ページ取得できた場合の保証開始日。';

COMMENT ON COLUMN screener.jquants_adjustment_sync_status.covered_to IS
'全ページ取得できた場合の要求終了日。最新取引日ではなくAPI取得保証範囲の終端。';

CREATE INDEX idx_jquants_adjustment_sync_status
ON screener.jquants_adjustment_sync_status (
    sync_status,
    covered_to
);

CREATE TRIGGER trg_jquants_adjustment_sync_set_updated_at
BEFORE UPDATE ON screener.jquants_adjustment_sync_status
FOR EACH ROW
EXECUTE FUNCTION screener.set_updated_at();


-- ============================================================
-- 権限制御
-- ============================================================

REVOKE ALL ON screener.corporate_actions
FROM PUBLIC, anon, authenticated;

REVOKE ALL ON screener.jquants_adjustment_sync_status
FROM PUBLIC, anon, authenticated;

REVOKE ALL ON SEQUENCE
    screener.corporate_actions_corporate_action_id_seq
FROM PUBLIC, anon, authenticated;
