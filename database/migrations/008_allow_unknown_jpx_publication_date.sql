-- ============================================================
-- 008_allow_unknown_jpx_publication_date.sql
--
-- JPX取得元ファイルの公開日を資料やHTTP応答から
-- 確実に特定できない場合、推測値を保存しないようにする。
-- ============================================================


-- ============================================================
-- publication_dateのnullable化
-- ============================================================

ALTER TABLE
    screener.jpx_corporate_action_source_files
ALTER COLUMN
    publication_date DROP NOT NULL;


-- ============================================================
-- コメント
-- ============================================================

COMMENT ON COLUMN
    screener.jpx_corporate_action_source_files.publication_date
IS
    'JPXが取得元ファイルを公開した日。確実に特定できない場合はNULL。';
