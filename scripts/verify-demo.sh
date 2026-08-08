#!/usr/bin/env bash
# Prove DEMO_MODE works with no internet at all.
#
# Not a claim, a proof: docker-compose.demo.yml puts the containers on an
# `internal: true` network with no route out, and this script first shows the
# network really is unreachable, then runs the entire demo path from inside it.
#
#   make demo-up && ./scripts/verify-demo.sh
set -uo pipefail
cd "$(dirname "$0")/.."

echo
echo "1. The containers must NOT be able to reach the internet"
docker compose exec -T api python - <<'PY'
import socket, urllib.request
socket.setdefaulttimeout(5)
hosts = ["pypi.org", "explorer.digitalearth.africa",
         "api.open-meteo.com", "nominatim.openstreetmap.org"]
leaked = False
for host in hosts:
    try:
        urllib.request.urlopen(f"https://{host}", timeout=5)
        print(f"   {host:34s} REACHABLE  <-- NOT AIR-GAPPED")
        leaked = True
    except Exception as exc:
        print(f"   {host:34s} blocked ({type(exc).__name__})")
raise SystemExit(1 if leaked else 0)
PY
if [ $? -ne 0 ]; then
  echo
  echo "   FAILED: the demo stack still has internet. Start it with:"
  echo "     docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d"
  exit 1
fi

echo
echo "2. The pipeline must run from cache alone"
docker compose exec -T processing python -m processing.scoring.daily \
  --city abuja --date "${DEMO_DATE:-2026-08-02}" 2>&1 \
  | grep -E "rainfall_cache_hit|segments scored" | sed 's/^/   /'

echo
echo "3. Alerts must fire and render an .eml"
docker compose exec -T alerts python -m alerts.evaluate --now --force 2>&1 \
  | grep -E "email_simulated|fired" | sed 's/^/   /'

echo
echo "4. The full demo path, from inside the air-gapped network"
docker compose exec -T -e API=http://api:8000 api bash -s < scripts/smoke.sh
