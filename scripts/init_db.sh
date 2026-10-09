#!/bin/bash
# Runs once, the first time the Postgres volume is created.
# Why a shell script: .sql files cannot read environment variables, but we
# want passwords to come from .env and never be committed.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v etl_password="$ETL_PASSWORD" \
  -v assistant_password="$ASSISTANT_PASSWORD" \
  -f /sql/00_roles_schemas.sql
