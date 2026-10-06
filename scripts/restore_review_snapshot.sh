#!/usr/bin/env bash
# B17 Phase 0: restore a local review snapshot of the e2e Postgres database,
# then rebuild the Neo4j projection for every tender from it.
#
# LOCAL ONLY. A review snapshot holds fake-provider run data (labelled fake
# in its filename) and the real-provider run data already in that database
# (tender 14cd7584, 2026-09-01) - it is never a demo seed and never leaves
# this machine (backups/ is gitignored and excluded by .dockerignore).
#
# DESTRUCTIVE to the target database: pg_restore --clean drops and recreates
# every object in it. So this script:
#   - refuses to run without --yes;
#   - refuses any database name ending in _test (the pytest database);
#   - takes a safety dump of the target first, into backups/, and verifies
#     its checksum on both sides before touching anything.
# It never removes a Docker volume.
#
# Stop api/worker/outbox_dispatcher first (keep postgres and neo4j up): a
# --clean restore drops tables those processes hold open, and a worker
# writing mid-restore would leave the result undefined.
#
# Usage, from the repo root, with the stack's postgres and neo4j running:
#   scripts/restore_review_snapshot.sh backups/<snapshot>.dump --yes [DBNAME]
# DBNAME defaults to tenderos.
#
# Graph rebuild: scripts/rebuild_graph.py is a stub (KNOWN_LIMITATIONS.md),
# so the projection is rebuilt with scripts/project_all_tenders.py, which
# calls graph_service.project_tender per tender - deterministic, no LLM.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: keep /tmp/... container paths literal

SNAPSHOT="${1:?usage: $0 backups/<snapshot>.dump --yes [DBNAME]}"
CONFIRM="${2:-}"
DB="${3:-tenderos}"
DB_USER=tenderos
CONTAINER=tenderos-postgres-1

if [[ "$CONFIRM" != "--yes" ]]; then
  echo "refusing: this drops and recreates every object in database '$DB'. Re-run with --yes." >&2
  exit 2
fi
if [[ "$DB" == *_test ]]; then
  echo "refusing: '$DB' is a pytest database, not a review target." >&2
  exit 2
fi
[[ -f "$SNAPSHOT" ]] || { echo "no such snapshot: $SNAPSHOT" >&2; exit 2; }

# 1. Safety dump of the current target, written inside the container and
#    copied out, checksum-verified - piping pg_dump's binary output through
#    `docker exec` stdout is avoided on purpose (Windows/Git Bash).
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SAFETY="pre_restore_${DB}_${TS}.dump"
docker compose exec -T postgres pg_dump -U "$DB_USER" -d "$DB" -Fc -f "/tmp/$SAFETY"
docker cp "$CONTAINER:/tmp/$SAFETY" "backups/$SAFETY" >/dev/null
IN="$(docker compose exec -T postgres sha256sum "/tmp/$SAFETY" | cut -c1-64)"
OUT="$(sha256sum "backups/$SAFETY" | cut -c1-64)"
[[ "$IN" == "$OUT" ]] || { echo "safety dump checksum mismatch - aborting before any restore" >&2; exit 1; }
docker compose exec -T postgres rm "/tmp/$SAFETY"
echo "safety dump: backups/$SAFETY ($IN)"

# 2. Restore the snapshot over the target.
NAME="$(basename "$SNAPSHOT")"
docker cp "$SNAPSHOT" "$CONTAINER:/tmp/$NAME" >/dev/null
docker compose exec -T postgres pg_restore -U "$DB_USER" -d "$DB" --clean --if-exists --no-owner "/tmp/$NAME"
docker compose exec -T postgres rm "/tmp/$NAME"
echo "restored $SNAPSHOT into $DB"

# 3. Rebuild the graph from the restored Postgres state.
POSTGRES_DSN="postgresql+asyncpg://$DB_USER:changeme@localhost:5432/$DB" \
NEO4J_URI=bolt://localhost:7687 \
REDIS_URL=redis://localhost:6379/0 \
S3_ENDPOINT_URL=http://localhost:9000 \
  uv run python scripts/project_all_tenders.py
