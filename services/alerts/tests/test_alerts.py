"""P9 unit tests: signing, message construction, debounce policy. No network."""

from __future__ import annotations

import hashlib
import hmac
import json

from alerts.deliver import SIGNATURE_HEADER, render_email, sign
from alerts.evaluate import CORRIDOR_BUFFER_M, DEBOUNCE_HOURS, build_message

SECRET = "test-secret"

PAYLOAD = {
    "subscription_id": 7,
    "email": "ops@fcta.gov.ng",
    "corridor_name": "Airport Road",
    "threshold": 60,
    "risk_score": 91.8,
    "risk_band": "High",
    "segment_id": 6765,
    "segment_name": "Independence Avenue",
    "highway_class": "primary",
    "hand_min_m": 1.7,
    "valid_date": "2026-08-09",
    "message": "Airport Road has reached risk 92/100.",
    "link": "http://localhost:5173/results?segment=6765",
    "contributions": {"rain_7d_mm": 110.0},
}


def test_signature_is_verifiable_by_a_third_party():
    """An integrator must be able to reproduce this with the stdlib alone."""
    body = json.dumps(PAYLOAD, default=str, separators=(",", ":")).encode()
    signature = sign(body, SECRET)

    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"
    assert hmac.compare_digest(signature.split("=", 1)[1], expected)


def test_signature_changes_when_the_body_changes():
    """Otherwise the signature proves nothing about integrity."""
    a = sign(b'{"risk":50}', SECRET)
    b = sign(b'{"risk":95}', SECRET)
    assert a != b


def test_signature_changes_with_the_secret():
    body = b'{"risk":50}'
    assert sign(body, "secret-a") != sign(body, "secret-b")


def test_signature_header_name_is_stable():
    """INTEGRATION.md documents this header; renaming it breaks integrators."""
    assert SIGNATURE_HEADER == "X-ClimatePass-Signature"


def test_message_names_the_corridor_the_risk_and_the_worst_point():
    segment = {
        "name": "Independence Avenue",
        "risk_score": 91.8,
        "risk_band": "High",
        "hand_min": 1.7,
        "contributions": {"rain_7d_mm": 110.0, "rain_24h_forecast_mm": 45.0},
    }
    message = build_message("Airport Road", segment, threshold=60)
    assert "Airport Road" in message
    assert "92" in message and "High" in message
    assert "Independence Avenue" in message
    assert "60" in message
    assert "1.7" in message and "110" in message


def test_scenario_rainfall_is_disclosed_in_the_alert_body():
    """A hypothetical figure must never reach a user looking like an observation."""
    segment = {
        "name": "Independence Avenue",
        "risk_score": 91.8,
        "risk_band": "High",
        "hand_min": 1.7,
        "contributions": {"rain_7d_mm": 110.0, "scenario": True},
    }
    assert "SCENARIO" in build_message("Airport Road", segment, 60)


def test_observed_rainfall_carries_no_scenario_warning():
    segment = {
        "name": "X",
        "risk_score": 70.0,
        "risk_band": "High",
        "hand_min": 9.0,
        "contributions": {"rain_7d_mm": 40.0, "scenario": False},
    }
    assert "SCENARIO" not in build_message("Corridor", segment, 60)


def test_email_is_a_valid_message_with_the_evidence_in_it():
    message = render_email(PAYLOAD)
    assert message["To"] == "ops@fcta.gov.ng"
    assert "Airport Road" in message["Subject"]
    body = message.get_content()
    assert "Independence Avenue" in body
    assert "http://localhost:5173/results?segment=6765" in body
    # The disclaimer is not decoration: it stops a relative ranking being read
    # as a depth forecast.
    assert "relative ranking" in body


def test_debounce_and_buffer_policy_are_what_the_brief_specified():
    assert DEBOUNCE_HOURS == 6
    assert CORRIDOR_BUFFER_M == 50.0
