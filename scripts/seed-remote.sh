#!/usr/bin/env bash
# Seed a remote (managed) Postgres from the local dump.
#
#   ./scripts/seed-remote.sh 'postgres://user:pass@host/db'
#
# Nothing does this for you. Render creates an empty database; this is the
# step that puts the road network and scored risk into it. Run it once.
set -uo pipefail

DB="${1:-${DATABASE_URL:-}}"
if [ -z "$DB" ]; then
  echo "usage: $0 'postgres://...'   (the External Database URL from Render)" >&2
  exit 1
fi

DUMP=deploy/seed.sql.gz
[ -f "$DUMP" ] || { echo "$DUMP not found — run: make dump-db" >&2; exit 1; }

# psql lives in the compose db container, so you do not need it installed.
PSQL=(docker compose exec -T db psql "$DB")

echo
echo "  target: $(echo "$DB" | sed -E 's#//[^:]+:[^@]+@#//***:***@#')"
echo "  dump:   $(du -h "$DUMP" | cut -f1) gzipped, $(gunzip -c "$DUMP" | wc -c | awk '{printf "%.0f MB", $1/1048576}') on the wire"
echo

echo "  [1/3] enabling PostGIS…"
"${PSQL[@]}" -q -c 'CREATE EXTENSION IF NOT EXISTS postgis;' || {
  echo "  FAILED. Does this instance offer PostGIS? Check the Postgres version." >&2; exit 1; }
"${PSQL[@]}" -tAc 'SELECT PostGIS_Version();' | sed 's/^/        PostGIS /'

echo "  [2/3] restoring (48 MB over your connection — a few minutes is normal)…"
START=$(date +%s)
gunzip -c "$DUMP" | "${PSQL[@]}" -q >/dev/null || {
  echo "  FAILED during restore. Re-run; it is idempotent enough to retry." >&2; exit 1; }
echo "        done in $(( $(date +%s) - START ))s"

echo "  [3/3] verifying…"
"${PSQL[@]}" -tAc "
  SELECT '        segments   '||count(*) FROM road_segments
  UNION ALL SELECT '        risk rows  '||count(*) FROM segment_risk
  UNION ALL SELECT '        watches    '||count(*) FROM subscriptions
  UNION ALL SELECT '        dates      '||string_agg(DISTINCT valid_date::text, ', ') FROM segment_risk;"
echo
echo "  Seeded. Now: API=https://your-api.onrender.com ./scripts/smoke.sh"
echo
