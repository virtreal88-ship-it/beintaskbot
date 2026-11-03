-- Review-only expansion: run separately before new writers. NOT an automatic
-- production migration. Fail promptly rather than block text indefinitely.
BEGIN;
SET LOCAL lock_timeout = '250ms';
ALTER TABLE saas_operator_copilot_threads
  ADD COLUMN IF NOT EXISTS clear_revision bigint NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute a JOIN pg_attrdef d
      ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attrelid='saas_operator_copilot_threads'::regclass
      AND a.attname='clear_revision' AND a.attnum>0 AND NOT a.attisdropped
      AND a.atttypid='pg_catalog.int8'::regtype AND a.attnotnull
      AND pg_get_expr(d.adbin,d.adrelid) IN ('0','0::bigint','(0)::bigint')
  ) THEN
    RAISE EXCEPTION 'incompatible clear_revision schema';
  END IF;
END $$;
COMMIT;
