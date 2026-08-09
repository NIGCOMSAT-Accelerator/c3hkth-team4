# Integration guide

For engineers wiring ClimatePass AI into something else. Two integrators are assumed throughout:

- **A state emergency agency** wanting daily exposure across a set of corridors, pushed rather than polled.
- **A logistics platform** wanting per-journey risk at dispatch time, synchronously, in under a second.

Every example below is a runnable command with its **real response**, captured from a live instance. No API key, no auth — this is a public read API. Write endpoints (`POST /v1/subscriptions`) are open in this release; put them behind your own gateway before exposing them publicly.

```bash
export CP=https://climatepass-api.onrender.com          # live
# export CP=http://localhost:8000              # or your own instance
```

Every command below runs as-is against the live deployment.

> **The responses shown were captured on 2026-08-02**, a wet day chosen so the
> examples are illustrative. The live API scores every day, so your figures
> will differ — and on a calm day `routes_identical` is legitimately `true`
> with no detour to offer. Check `risk_date` in any response to see which day
> you are looking at. The shapes are stable; the numbers are weather.

Interactive docs: <https://climatepass-api.onrender.com/docs> · OpenAPI schema: <https://climatepass-api.onrender.com/openapi.json>

---

## Quick reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness. No dependencies checked |
| `GET` | `/health/db` | Database and PostGIS readiness |
| `GET` | `/health/routing` | Routing graph state and risk date |
| `GET` | `/v1/segments` | Road segments with risk, as GeoJSON |
| `GET` | `/v1/risk/point` | Risk at the segment nearest a point |
| `POST` | `/v1/route/analyze` | Fastest vs safest route |
| `GET` | `/v1/alerts` | Today's highest-risk corridors |
| `POST` | `/v1/subscriptions` | Create a Corridor Watch |
| `GET` | `/v1/subscriptions/{id}` | Fetch a watch and its recent alerts |
| `GET` | `/v1/geocode` | Place name → coordinates |
| `GET` | `/v1/meta/model` | Weights, provenance, limitations |

---

## 1. Is it up, and what day is it serving?

```bash
curl -s $CP/health/routing
```

```json
{
  "loaded": true,
  "nodes": 10728,
  "edges": 19444,
  "risk_date": "2026-08-02",
  "risk_age_seconds": 0.0,
  "median_risk": 44.94
}
```

**`risk_date` is the field to monitor.** If it stops advancing, the daily pipeline has stopped and every score you receive is stale. Alert on it.

---

## 2. Route risk at dispatch time

The core call for a logistics integrator.

```bash
curl -s -X POST $CP/v1/route/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "origin":      {"lat": 9.1088, "lon": 7.4066},
    "destination": {"lat": 9.1518, "lon": 7.3269},
    "lambda": 3
  }'
```

Real response, geometry elided:

```json
{
  "fastest": {
    "distance_m": 21207.4, "duration_s": 824.8,
    "route_risk": 72.8, "max_segment_risk": 75.1,
    "mean_segment_risk": 69.5, "segment_count": 44
  },
  "safest": {
    "distance_m": 29958.0, "duration_s": 1547.3,
    "route_risk": 64.2, "max_segment_risk": 74.8,
    "mean_segment_risk": 48.3, "segment_count": 222
  },
  "lambda": 3.0,
  "delay_seconds": 722.5,
  "delay_minutes": 12.0,
  "risk_reduction_pct": 11.8,
  "routes_identical": false,
  "recommendation": "Taking the safer route costs 12 extra minutes and cuts route risk 12%, from 73 to 64.",
  "graph": { "risk_date": "2026-08-02", "nodes": 10728, "edges": 19444 }
}
```

`fastest.geometry` and `safest.geometry` are GeoJSON LineStrings. `fastest.segments` / `safest.segments` are FeatureCollections carrying per-segment `risk`, `name`, `length_m` and `travel_time_s` — use these to colour a route by risk rather than drawing one flat line.

### Things that will bite you if you skip them

**`routes_identical` can be true.** When no alternative lowers risk, both routes are the same path, `delay_seconds` is 0, and `identical_reason` explains why. Do not render "12 min slower, 0% safer" — check the flag.

```bash
# lambda 0 always returns the fastest route, by definition
curl -s -X POST $CP/v1/route/analyze -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":9.1088,"lon":7.4066},"destination":{"lat":9.1518,"lon":7.3269},"lambda":0}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["routes_identical"], d["delay_seconds"])'
# -> True 0.0
```

**`lambda` is your risk appetite,** 0–20, default 3.0. 0 is the fastest route. Higher trades time for safety. An ambulance and a cement lorry should not send the same value.

**Points outside the mapped network return 422**, not a wrong answer. See §7.

---

## 3. Risk at a point

```bash
curl -s "$CP/v1/risk/point?lat=9.0579&lon=7.4913"
```

```json
{
  "segment_id": 22575,
  "name": "Sani Abacha Way North",
  "highway_class": "trunk",
  "risk_score": 66.9,
  "risk_band": "High",
  "valid_date": "2026-08-02",
  "distance_m": 99.6,
  "susceptibility": 0.69,
  "hand_min": 4.5,
  "wofs_freq_max": 0.0065,
  "explanation": "Risk is High (67/100) for Sani Abacha Way North. It sits 4.5 m above the nearest drainage channel, low enough to be reached by a swollen channel. 88 mm has fallen in the past week, so the ground is already wet and further rain will run off rather than soak in.",
  "evidence": [
    {"label": "Height above drainage", "value": 4.5, "unit": "m", "weight": 0.4},
    {"label": "Rain, last 7 days", "value": 88.5, "unit": "mm"}
  ]
}
```

`explanation` is **deterministic template text**, not LLM output — every clause traces to a number in `contributions`. Safe to display verbatim; it will not hallucinate and it does not need a network call to a model provider.

`distance_m` is how far the queried point was from the returned segment. **Check it.** A large value means you queried somewhere with no nearby mapped road.

---

## 4. Map layers

```bash
curl -s "$CP/v1/segments?bbox=7.45,9.03,7.53,9.09&min_risk=60&classes=motorway,trunk,primary"
```

Standard GeoJSON `FeatureCollection`, plus `valid_date`, `count` and `truncated`.

**Capped at 5000 features.** When `truncated` is true, either zoom in, raise `min_risk`, or narrow `classes`. Results are ordered by **road importance**, so the cap sheds minor streets first and what you get is a coherent network rather than a scattered sample — order by risk and cap, and you get confetti.

`classes` should narrow as your viewport widens. A workable ladder:

| Zoom | `classes` |
|---|---|
| < 11.5 | `motorway,trunk,primary,motorway_link,trunk_link` |
| 11.5–13 | add `secondary,primary_link` |
| > 13 | omit the parameter entirely |

---

## 5. Corridor Watch and webhooks

The push integration. Create a watch:

```bash
curl -s -X POST $CP/v1/subscriptions \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "ops@fcta.gov.ng",
    "corridor": [
      {"lat": 9.0579, "lon": 7.4913},
      {"lat": 9.0200, "lon": 7.4300},
      {"lat": 8.9816, "lon": 7.3736}
    ],
    "corridor_name": "Central Area to Lugbe via Airport Road",
    "threshold": 60,
    "channel": "webhook",
    "webhook_url": "https://ops.example.ng/hooks/climatepass"
  }'
```

```json
{
  "id": 1,
  "email": "ops@fcta.gov.ng",
  "corridor_name": "Central Area to Lugbe via Airport Road",
  "threshold": 60,
  "channel": "webhook",
  "active": true,
  "created_at": "2026-08-08T17:32:23.417641Z"
}
```

**Semantics you need to know:**

- Segments within a **50 m** buffer of the corridor are considered; the **worst** one decides.
- Evaluated **every 30 minutes**.
- **Debounced to one alert per subscription per 6 hours.** Alert spam destroys trust: a user who gets six notifications about one storm mutes the channel and misses the next one.

### The webhook payload

`POST` to your URL, `Content-Type: application/json`, with header `X-ClimatePass-Signature: sha256=<hex>`.

```json
{
  "subscription_id": 1,
  "corridor_name": "Central Area to Lugbe via Airport Road",
  "threshold": 60,
  "risk_score": 91.8,
  "risk_band": "High",
  "segment_id": 6765,
  "segment_name": "Independence Avenue",
  "hand_min_m": 1.7,
  "valid_date": "2026-08-09",
  "message": "Central Area to Lugbe via Airport Road has reached risk 92/100 (High), above your threshold of 60. The riskiest point is Independence Avenue...",
  "link": "http://localhost:5173/results?segment=6765"
}
```

### Verifying the signature

HMAC-SHA256 over the **exact raw body bytes**. Do not re-serialise the parsed JSON before verifying — key order will change and the signature will not match.

```python
import hashlib, hmac

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    """header is the X-ClimatePass-Signature value, e.g. 'sha256=abc123...'"""
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    # Constant-time: a plain == leaks timing information about the digest.
    return hmac.compare_digest(header.split("=", 1)[1], expected)
```

Flask receiver:

```python
from flask import Flask, request, abort
app = Flask(__name__)
SECRET = "your-shared-secret"   # matches WEBHOOK_HMAC_SECRET on the server

@app.post("/hooks/climatepass")
def hook():
    if not verify(request.get_data(), request.headers.get("X-ClimatePass-Signature", ""), SECRET):
        abort(401)
    alert = request.get_json()
    if alert["risk_score"] >= 80:
        page_duty_officer(alert)
    return "", 204
```

Return **2xx quickly**. Delivery is attempted once and the outcome recorded in `alerts.payload.delivery` — there is no retry queue in this release. Poll `GET /v1/subscriptions/{id}` if you need to reconcile.

---

## 6. Model transparency

```bash
curl -s $CP/v1/meta/model
```

Returns live weights, the risk formula, band boundaries, route-risk rationale, rainfall provenance, validation status and known limitations. It reads the same constants the pipeline applies, so published weights cannot drift from applied weights.

Worth reading before you trust a score. In particular `rainfall_provenance` reports the real source split, e.g. `antecedent: chirps:0d, open-meteo:7d; chirps_latency_days=54`, and `validation.status` is `"not_run"` rather than a fabricated accuracy figure.

---

## 7. Errors

Every error has the same shape:

```json
{"error": {"code": "invalid_bbox", "message": "bbox must be four comma-separated numbers: min_lon,min_lat,max_lon,max_lat", "detail": {"received": "garbage"}}}
```

| Code | HTTP | Meaning | What to do |
|---|---|---|---|
| `invalid_bbox` | 422 | bbox malformed | Send `min_lon,min_lat,max_lon,max_lat` |
| `invalid_request` | 422 | Body or query failed validation | Read `detail`; compare against `/docs` |
| `no_route` | 422 | No path between the points | Both must be inside the mapped Abuja arterial network |
| `no_risk_data` | 503 | Nothing scored yet | Daily pipeline has not run. Retry; alert if persistent |
| `no_segment` | 404 | No road near that point | Query nearer a mapped road |
| `unknown_city` | 422 | City slug not loaded | Only `abuja` in this release |
| `missing_webhook_url` | 422 | `channel="webhook"` without a URL | Supply `webhook_url` or use `channel="email"` |
| `not_found` | 404 | No such resource | Check the id |
| `internal_error` | 500 | Unhandled failure | Retry once, then report path and time |

Messages are written to say what went wrong **and what to do next**. Surface them to your users rather than replacing them with a generic string — you would be discarding the advice.

---

## 8. A complete Python client

No dependencies beyond `httpx`. Copy it whole.

```python
"""Minimal ClimatePass AI client."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx


class ClimatePassError(RuntimeError):
    def __init__(self, code: str, message: str, detail: Any = None, status: int | None = None):
        super().__init__(f"[{code}] {message}")
        self.code, self.detail, self.status = code, detail, status


@dataclass
class RouteAdvice:
    fastest_risk: float
    safest_risk: float
    delay_minutes: float
    risk_reduction_pct: float
    identical: bool
    recommendation: str
    safest_geometry: dict
    valid_date: str | None

    @property
    def worth_detouring(self) -> bool:
        """Your policy, not ours. Tune to your own tolerance."""
        return not self.identical and self.risk_reduction_pct >= 10 and self.delay_minutes <= 20


class ClimatePass:
    def __init__(self, base_url: str = "https://climatepass-api.onrender.com", timeout: float = 15.0):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def __enter__(self): return self
    def __exit__(self, *_): self._client.close()

    def _call(self, method: str, path: str, **kw) -> Any:
        try:
            response = self._client.request(method, path, **kw)
        except httpx.RequestError as exc:
            raise ClimatePassError("network_unreachable", str(exc)) from exc

        if response.status_code >= 400:
            try:
                err = response.json()["error"]
            except Exception:
                raise ClimatePassError("http_error", response.text[:200], status=response.status_code)
            raise ClimatePassError(err["code"], err["message"], err.get("detail"), response.status_code)
        return response.json()

    # -- health ---------------------------------------------------------
    def risk_date(self) -> str | None:
        """The scoring date being served. Alert if this stops advancing."""
        return self._call("GET", "/health/routing")["risk_date"]

    # -- core -----------------------------------------------------------
    def analyze_route(self, origin: tuple[float, float], destination: tuple[float, float],
                      lam: float = 3.0) -> RouteAdvice:
        """origin/destination are (lat, lon)."""
        data = self._call("POST", "/v1/route/analyze", json={
            "origin": {"lat": origin[0], "lon": origin[1]},
            "destination": {"lat": destination[0], "lon": destination[1]},
            "lambda": lam,
        })
        return RouteAdvice(
            fastest_risk=data["fastest"]["route_risk"],
            safest_risk=data["safest"]["route_risk"],
            delay_minutes=data["delay_minutes"],
            risk_reduction_pct=data["risk_reduction_pct"],
            identical=data["routes_identical"],
            recommendation=data["recommendation"],
            safest_geometry=data["safest"]["geometry"],
            valid_date=data["graph"]["risk_date"],
        )

    def risk_at(self, lat: float, lon: float, max_distance_m: float = 500) -> dict | None:
        """None when the nearest mapped road is further than max_distance_m."""
        data = self._call("GET", "/v1/risk/point", params={"lat": lat, "lon": lon})
        return None if data["distance_m"] > max_distance_m else data

    def segments(self, bbox: tuple[float, float, float, float], min_risk: float = 0,
                 classes: str | None = None) -> dict:
        params = {"bbox": ",".join(map(str, bbox)), "min_risk": min_risk}
        if classes:
            params["classes"] = classes
        data = self._call("GET", "/v1/segments", params=params)
        if data["truncated"]:
            import warnings
            warnings.warn("Result truncated at 5000 features: narrow bbox, classes or min_risk.")
        return data

    def top_corridors(self, limit: int = 10) -> list[dict]:
        return self._call("GET", "/v1/alerts", params={"limit": limit})["clusters"]

    def watch_corridor(self, email: str, corridor: list[tuple[float, float]], name: str,
                       threshold: int = 60, webhook_url: str | None = None) -> dict:
        payload = {
            "email": email,
            "corridor": [{"lat": la, "lon": lo} for la, lo in corridor],
            "corridor_name": name,
            "threshold": threshold,
            "channel": "webhook" if webhook_url else "email",
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return self._call("POST", "/v1/subscriptions", json=payload)

    def model_card(self) -> dict:
        return self._call("GET", "/v1/meta/model")

    # -- webhooks -------------------------------------------------------
    @staticmethod
    def verify_webhook(raw_body: bytes, signature_header: str, secret: str) -> bool:
        if not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature_header.split("=", 1)[1], expected)


if __name__ == "__main__":
    with ClimatePass() as cp:
        print("serving:", cp.risk_date())

        advice = cp.analyze_route((9.1088, 7.4066), (9.1518, 7.3269))
        print(advice.recommendation)
        print("worth detouring:", advice.worth_detouring)

        for corridor in cp.top_corridors(3):
            print(f"  {corridor['peak_risk']:5.1f}  {corridor['name']}")
```

Expected output against a seeded instance:

```
serving: 2026-08-02
Taking the safer route costs 12 extra minutes and cuts route risk 12%, from 73 to 64.
worth detouring: True
   75.1  Herbert Macaulay Way
   75.1  Ameyo Adadevoh Way
   75.1  Independence Avenue
```

---

## 9. Operational notes

- **No rate limiting in this release.** Put a gateway in front before exposing it publicly.
- **The routing graph is per-process.** Multiple uvicorn workers each hold their own copy (~10,728 nodes) and refresh risk independently on a 15-minute timer. Fine to a handful of workers; beyond that, move the graph to a shared service.
- **Scores update once daily.** Polling more often than hourly gains you nothing. Watch `risk_date`.
- **Geometry is EPSG:4326** everywhere in the API. Metric work happens internally in EPSG:32632.
- **Licensing:** road geometry derives from OpenStreetMap under **ODbL (share-alike)**. Redistributing segment geometry carries that obligation. See [DATA_SOURCES.md](DATA_SOURCES.md).
