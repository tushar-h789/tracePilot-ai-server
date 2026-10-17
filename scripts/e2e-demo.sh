#!/usr/bin/env bash
# End-to-end acceptance for Phase 3 against the running compose stack:
#   1. create a run-unique user, org, project and ingestion key through the API
#   2. send every demo scenario with a fixed seed and start time (SDK -> POST /api/v1/ingest/logs)
#   3. count the rows in PostgreSQL (docker compose exec postgres psql)
#   4. rerun with the same seed: the counts must not change (idempotency)
#   5. revoke the key: a further run must fail with 401 and store nothing
#
#   docker compose --profile web up -d && docker compose run --rm migrate
#   ./scripts/e2e-demo.sh            # COUNT=200 SEED=42 by default
#
# The ingestion key lives only in this process's environment. It is never printed or written to disk.
# The script creates its own user and org (e2e-<runId>@example.com) and deletes nothing.
set -euo pipefail
cd "$(dirname "$0")/.."

API="${API:-http://localhost:8000/api/v1}"
SEED="${SEED:-42}"
COUNT="${COUNT:-200}"
DURATION="${DURATION:-600}"
SCENARIOS=(normal latency http-500 db-timeout external-api-failure recovery)
RUN_ID="$(date +%s)-$$"
WORK="$(mktemp -d "${PWD}/.e2e-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/cookies"

json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
csrf_from_jar() { awk '$6=="tp_csrf"{print $7}' "$JAR" | tail -n1; }
post() {  # post <path> <json> -> body; fails on non-2xx
  curl -sS --fail-with-body -b "$JAR" -c "$JAR" -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_from_jar)" -X POST "$API$1" -d "$2"
}
psql_q() {
  docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "$1"' _ "$1"
}
demo() {
  uv run --quiet --project demo/sample-service tracepilot-demo --endpoint "${API%/api/v1}" "$@"
}

echo "== run $RUN_ID: seed=$SEED count=$COUNT duration=${DURATION}s"
curl -sS -c "$JAR" "$API/auth/csrf" >/dev/null
EMAIL="e2e-$RUN_ID@example.com"
PASSWORD="e2e-local-demo-passphrase-$RUN_ID"
post /auth/register "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"E2E $RUN_ID\"}" >/dev/null
post /auth/login "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" >/dev/null
ORG_ID="$(post /organizations "{\"name\":\"E2E Org $RUN_ID\"}" | json "['id']")"
PROJECT_ID="$(post "/organizations/$ORG_ID/projects" '{"name":"E2E demo","environment":"demo"}' | json "['id']")"
KEY_JSON="$(post "/projects/$PROJECT_ID/ingestion-keys" '{"name":"e2e demo key"}')"
KEY_ID="$(json "['key']['id']" <<<"$KEY_JSON")"
export TRACEPILOT_INGEST_KEY="$(json "['secret']" <<<"$KEY_JSON")"
unset KEY_JSON
echo "user=$EMAIL org=$ORG_ID project=$PROJECT_ID key=$(psql_q "select prefix from ingestion_keys where id='$KEY_ID'")"

# Fixed window: the same --start on both runs gives byte-identical events.
START="$(date -u -d "@$(( $(date +%s) / 60 * 60 - DURATION - 60 ))" +%Y-%m-%dT%H:%M:%SZ)"
counts() {
  psql_q "select attributes->>'scenario', count(*), count(*) filter (where level='error'),
                 bool_and(is_synthetic and environment='demo')
          from log_events where project_id='$PROJECT_ID' group by 1 order by 1"
}
total() { psql_q "select count(*) from log_events where project_id='$PROJECT_ID'"; }

for pass in 1 2; do
  echo "== pass $pass (start=$START)"
  for s in "${SCENARIOS[@]}"; do
    demo --scenario "$s" --seed "$SEED" --start "$START" --duration "$DURATION" --count "$COUNT"
  done
  echo "-- rows per scenario (scenario|rows|errors|all demo+synthetic)"
  counts
  eval "TOTAL_$pass=$(total)"
  echo "-- total rows: $(total)"
done

EXPECTED=$(( COUNT * ${#SCENARIOS[@]} ))
[[ "$TOTAL_1" == "$EXPECTED" ]] || { echo "FAIL: expected $EXPECTED rows after pass 1, got $TOTAL_1"; exit 1; }
[[ "$TOTAL_2" == "$TOTAL_1" ]] || { echo "FAIL: rerun changed the row count ($TOTAL_1 -> $TOTAL_2)"; exit 1; }

echo "== revoke the key, then try another seed"
post "/projects/$PROJECT_ID/ingestion-keys/$KEY_ID/revoke" '{}' >/dev/null
if demo --scenario normal --seed $(( SEED + 1 )) --start "$START" --count 5; then
  echo "FAIL: revoked key was accepted"; exit 1
fi
[[ "$(total)" == "$TOTAL_1" ]] || { echo "FAIL: rows were stored with a revoked key"; exit 1; }
unset TRACEPILOT_INGEST_KEY
echo "PASS: $TOTAL_1 rows after pass 1, $TOTAL_2 after the same-seed rerun; revoked key rejected (project $PROJECT_ID)"
