#!/usr/bin/env bash
# The whole demo path, in under 30 seconds.
#
# Run this before the pitch, and run it again after every deploy. It exercises
# exactly what a judge will see, so if it passes the demo works and if it fails
# you know before you are standing in front of anyone.
#
#   ./scripts/smoke.sh                  # against localhost
#   API=https://climatepass.example ./scripts/smoke.sh
set -uo pipefail

API="${API:-http://localhost:8000}"
PASS=0
FAIL=0
START=$(date +%s)

green() { printf "\033[32m%s\033[0m" "$1"; }
red()   { printf "\033[31m%s\033[0m" "$1"; }

check() { # check <name> <jq-ish python expr> <curl args...>
  local name="$1"; shift
  local expr="$1"; shift
  local body
  body=$(curl -s --max-time 12 "$@" 2>/dev/null)
  # Body goes in on stdin, never interpolated into the source: a 5000-feature
  # FeatureCollection embedded in a string literal breaks on the first quote,
  # which made two correct endpoints look broken.
  if printf '%s' "$body" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if ($expr) else 1)
" 2>/dev/null; then
    printf "  %s %s\n" "$(green "PASS")" "$name"; PASS=$((PASS+1))
  else
    printf "  %s %s\n" "$(red "FAIL")" "$name"
    printf "         %s\n" "$(echo "$body" | head -c 160)"
    FAIL=$((FAIL+1))
  fi
}

echo
echo "ClimatePass smoke test  ->  $API"
echo

check "health responds ok" \
  "d['status']=='ok'" "$API/health"

check "database and PostGIS reachable" \
  "d['status']=='ok'" "$API/health/db"

check "routing graph warm with risk attached" \
  "d['loaded'] and d['edges']>10000 and d['risk_date'] is not None" "$API/health/routing"

check "segments return GeoJSON for the city bbox" \
  "d['type']=='FeatureCollection' and d['count']>100" \
  "$API/v1/segments?bbox=7.25,8.90,7.62,9.22&classes=motorway,trunk,primary"

check "point risk carries evidence and an explanation" \
  "d['risk_score']>0 and len(d['explanation'])>40 and len(d['evidence'])>0" \
  "$API/v1/risk/point?lat=9.0579&lon=7.4913"

check "highest-risk corridors are ranked" \
  "d['count']>0 and d['clusters'][0]['peak_risk']>0" "$API/v1/alerts?limit=5"

check "geocoder resolves from the bundled gazetteer" \
  "len(d['results'])>0 and d['results'][0]['source']=='gazetteer'" \
  "$API/v1/geocode?q=Lugbe"

check "model card publishes weights and limitations" \
  "d['susceptibility']['weights']['hand']==0.4 and len(d['limitations'])>=5" \
  "$API/v1/meta/model"

# The centrepiece: two distinct routes, a real delay, a real reduction.
check "route analysis returns two distinct routes" \
  "(not d['routes_identical']) and d['delay_seconds']>0 and d['risk_reduction_pct']>0 and len(d['recommendation'])>40" \
  -X POST "$API/v1/route/analyze" -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":9.1088,"lon":7.4066},"destination":{"lat":9.1518,"lon":7.3269},"lambda":3}'

# lambda=0 must reproduce the fastest route exactly, or the control is a lie.
check "lambda 0 returns the fastest route unchanged" \
  "d['routes_identical'] and d['delay_seconds']==0" \
  -X POST "$API/v1/route/analyze" -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":9.1088,"lon":7.4066},"destination":{"lat":9.1518,"lon":7.3269},"lambda":0}'

ELAPSED=$(( $(date +%s) - START ))
echo
echo "  $PASS passed, $FAIL failed, ${ELAPSED}s"
echo
[ "$FAIL" -eq 0 ] || exit 1
