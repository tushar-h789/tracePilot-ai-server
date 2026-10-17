#!/bin/sh
# Runs once, on first initialisation of an empty data volume (official image behaviour).
# Creates "<POSTGRES_DB>_test" for host-run integration tests (pytest uses TEST_DATABASE_URL).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "CREATE DATABASE \"${POSTGRES_DB}_test\" OWNER \"$POSTGRES_USER\";"
