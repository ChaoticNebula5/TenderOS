-- Phase 4 plan item 44: the workflow outbox rows of one E2E run, for
-- scripts/e2e_step11_timeline.py's "last workflow event" (its --outbox
-- input). Run after the E2E, inside the stack's postgres container:
--   docker compose exec -T postgres sh -c \
--     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q' \
--     < scripts/e2e_outbox_export.sql > e2e-logs/outbox.tsv
-- Tab-separated: created_at in UTC, aggregate_type, event_type. created_at
-- is the database's now() in the writing transaction, which the stream's
-- poll compares against; no payload is exported.
COPY (
    SELECT created_at AT TIME ZONE 'UTC', aggregate_type, event_type
    FROM outbox_events
    WHERE aggregate_type LIKE 'workflow%'
    ORDER BY created_at
) TO STDOUT;
