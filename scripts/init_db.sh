#!/usr/bin/env bash
# Initialize PostgreSQL database schema using external DATABASE_URL
# Usage: DATABASE_URL=postgresql://user:pass@host[:port]/db ./scripts/init_db.sh
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required (e.g., postgresql://user:pass@host:port/db)"
  exit 1
fi

SQL_FILE="$(dirname "$0")/../sql/init_postgres.sql"

# Use libpq URI directly; supports postgres:// and postgresql://
psql "${DATABASE_URL}" -f "$SQL_FILE"

echo "Schema initialized using ${DATABASE_URL}"
