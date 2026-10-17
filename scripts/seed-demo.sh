#!/usr/bin/env bash
# Seed a clearly named DEMO tenant into the running compose stack (never touches other orgs):
#   user    e2e-demo-<runId>@example.com   (credentials -> .demo-credentials.local, gitignored, 0600)
#   org     "TracePilot Demo (e2e)"
#   project "Demo Shop API" (environment demo)
# 1. Real pipeline: every demo scenario through the SDK -> POST /ingest/logs, spread over the
#    last 6 hours (COUNT events per scenario).
# 2. Optional bulk fixture (BULK=N, default 0): N synthetic rows inserted directly with SQL over
#    the last 7 days, used for EXPLAIN (ANALYZE, BUFFERS) in docs/PERFORMANCE.md. Rows carry
#    attributes.bulk=true and is_synthetic=true so they can be told apart (or deleted).
# Rerunning reuses the saved demo user/org/project and adds another batch (new seed).
set -euo pipefail
cd "$(dirname "$0")/.."

API="${API:-http://localhost:8000/api/v1}"
COUNT="${COUNT:-150}"
BULK="${BULK:-0}"
CREDS=".demo-credentials.local"
ORG_NAME="TracePilot Demo (e2e)"
PROJECT_NAME="Demo Shop API"
SCENARIOS=(normal latency http-500 db-timeout external-api-failure recovery)
WORK="$(mktemp -d "${PWD}/.seed-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/cookies"

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
csrf_from_jar() { awk '$6=="tp_csrf"{print $7}' "$JAR" | tail -n1; }
call() {  # call <METHOD> <path> [json]
  curl -sS --fail-with-body -b "$JAR" -c "$JAR" -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $(csrf_from_jar)" -X "$1" "$API$2" ${3:+-d "$3"}
}
psql_q() {
  docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tA -c "$1"' _ "$1"
}

curl -sS -c "$JAR" "$API/auth/csrf" >/dev/null
if [[ -f "$CREDS" ]]; then
  # shellcheck disable=SC1090
  source "$CREDS"
else
  RUN_ID="$(date +%s)"
  DEMO_EMAIL="e2e-demo-$RUN_ID@example.com"
  DEMO_PASSWORD="$(python3 -c 'import secrets; print("demo-" + secrets.token_urlsafe(18))')"
  umask 077
  printf 'DEMO_EMAIL=%q\nDEMO_PASSWORD=%q\n' "$DEMO_EMAIL" "$DEMO_PASSWORD" >"$CREDS"
  call POST /auth/register "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASSWORD\",\"display_name\":\"Demo (e2e)\"}" >/dev/null
fi
call POST /auth/login "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASSWORD\"}" >/dev/null

ORG_ID="$(call GET /organizations | json "next((o['id'] for o in d['items'] if o['name']=='$ORG_NAME'), '')")"
[[ -n "$ORG_ID" ]] || ORG_ID="$(call POST /organizations "{\"name\":\"$ORG_NAME\"}" | json "d['id']")"
PROJECT_ID="$(call GET "/organizations/$ORG_ID/projects" | json "next((p['id'] for p in d['items'] if p['name']=='$PROJECT_NAME'), '')")"
[[ -n "$PROJECT_ID" ]] || PROJECT_ID="$(call POST "/organizations/$ORG_ID/projects" "{\"name\":\"$PROJECT_NAME\",\"environment\":\"demo\"}" | json "d['id']")"
echo "demo user=$DEMO_EMAIL org=$ORG_ID project=$PROJECT_ID"

KEY_JSON="$(call POST "/projects/$PROJECT_ID/ingestion-keys" "{\"name\":\"seed-demo $(date +%s)\"}")"
export TRACEPILOT_INGEST_KEY="$(json "d['secret']" <<<"$KEY_JSON")"
unset KEY_JSON
SEED="$(date +%s)"
NOW_MIN=$(( $(date +%s) / 60 * 60 ))
i=0
for s in "${SCENARIOS[@]}"; do
  START="$(date -u -d "@$(( NOW_MIN - 6 * 3600 + i * 3600 ))" +%Y-%m-%dT%H:%M:%SZ)"
  uv run --quiet --project demo/sample-service tracepilot-demo --endpoint "${API%/api/v1}" \
    --scenario "$s" --seed "$(( SEED + i ))" --start "$START" --duration 3500 --count "$COUNT"
  i=$(( i + 1 ))
done
unset TRACEPILOT_INGEST_KEY

if (( BULK > 0 )); then
  echo "== bulk fixture: $BULK rows over the last 7 days"
  psql_q "
    INSERT INTO log_events (project_id, event_id, ts, level, service, environment, message,
                            trace_id, request_id, http_method, http_path, http_status,
                            duration_ms, attributes, is_synthetic)
    SELECT '$PROJECT_ID', gen_random_uuid(),
           now() - (random() * interval '7 days'),
           (ARRAY['debug','info','info','info','info','info','warning','warning','error','critical'])[1 + (g % 10)],
           (ARRAY['checkout','payments','catalog','auth','search','worker'])[1 + (g % 6)],
           'demo',
           (ARRAY['GET /api/cart completed','Timeout connecting to db-primary',
                  'Payment declined by provider','Cache miss for user profile',
                  'Upstream inventory service returned 503','User login succeeded'])[1 + (g % 6)]
             || ' #' || g,
           md5(g::text), 'bulk-' || g,
           CASE WHEN g % 5 < 4 THEN 'GET' END,
           CASE WHEN g % 5 < 4 THEN '/api/v1/items/' || (g % 100) END,
           CASE WHEN g % 5 < 4 THEN (CASE WHEN g % 37 = 0 THEN 503 WHEN g % 11 = 0 THEN 404 ELSE 200 END) END,
           CASE WHEN g % 5 < 4 THEN round((20 + random() * 180 + (CASE WHEN g % 50 = 0 THEN 2000 ELSE 0 END))::numeric, 3)::float8 END,
           jsonb_build_object('bulk', true, 'n', g),
           true
    FROM generate_series(1, $BULK) AS g;
    ANALYZE log_events;"
fi
psql_q "select count(*) from log_events where project_id='$PROJECT_ID'" | sed 's/^/rows in demo project: /'
echo "credentials: $CREDS"
