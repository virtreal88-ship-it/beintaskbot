"""Read committed catalog shape before running legacy additive initializers.

No Python readiness cache: an uncommitted migration, rollback, another database
or another search_path must never inherit a positive result. Warm requests read
catalogs only and never upgrade locks on the application tables.
"""
import json


def ready(cur, columns, indexes):
    cur.execute("""SELECT NOT EXISTS (
      SELECT 1 FROM jsonb_each(%s::jsonb) AS relation(name, columns)
      WHERE to_regclass(relation.name) IS NULL OR EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(relation.columns) AS expected(name)
        WHERE NOT EXISTS (
          SELECT 1 FROM pg_attribute attribute
          WHERE attribute.attrelid=to_regclass(relation.name)
            AND attribute.attname=expected.name AND attribute.attnum>0
            AND NOT attribute.attisdropped)))
      AND NOT EXISTS (
        SELECT 1 FROM unnest(%s::text[]) AS expected(name)
        WHERE NOT EXISTS (
          SELECT 1 FROM pg_index index
          WHERE index.indexrelid=to_regclass(expected.name) AND index.indisvalid
            AND index.indrelid IN (
              SELECT to_regclass(relation.name)
              FROM jsonb_object_keys(%s::jsonb) AS relation(name)))) AS ready""",
      (json.dumps(columns), list(indexes), json.dumps(columns)))
    return (cur.fetchone() or {}).get('ready') is True


def lock_schema(cur):
    # Serialize actual missing-schema work across Connect and Spend. This is
    # transaction scoped and reentrant for their nested initialization calls.
    # Do not commit a caller's receipt, reservation or support-ticket transaction.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ('tenant-connect-spend-schema',))
