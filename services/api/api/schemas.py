"""Request and response models. Typed so OpenAPI is genuinely usable."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class Point(BaseModel):
    lat: float = Field(..., ge=-90, le=90, examples=[9.0579])
    lon: float = Field(..., ge=-180, le=180, examples=[7.4913])


class RouteRequest(BaseModel):
    origin: Point
    destination: Point
    lambda_: float | None = Field(
        None,
        alias="lambda",
        ge=0,
        le=20,
        description=(
            "Risk aversion. 0 returns the fastest route; higher values trade "
            "time for safety. Default 3.0."
        ),
    )

    model_config = {"populate_by_name": True}


class SubscriptionCreate(BaseModel):
    email: str = Field(..., examples=["ops@example.ng"])
    corridor: list[Point] = Field(..., min_length=2, description="Corridor as an ordered path.")
    corridor_name: str | None = Field(None, examples=["Airport Road morning run"])
    threshold: int = Field(60, ge=0, le=100, description="Alert when corridor risk crosses this.")
    channel: str = Field("email", pattern="^(email|webhook)$")
    webhook_url: str | None = None
    city: str = "abuja"


class SubscriptionOut(BaseModel):
    id: int
    email: str
    corridor_name: str | None
    threshold: int
    channel: str
    active: bool
    created_at: dt.datetime


class RiskContribution(BaseModel):
    """One factor's evidence, so the UI can render numeric chips."""

    label: str
    value: float
    unit: str | None = None
    weight: float | None = None


class PointRisk(BaseModel):
    segment_id: int
    name: str | None
    highway_class: str | None
    risk_score: float
    risk_band: str
    valid_date: dt.date
    distance_m: float
    susceptibility: float | None
    hand_min: float | None
    wofs_freq_max: float | None
    contributions: dict[str, Any] | None
    explanation: str
    evidence: list[RiskContribution]


class GeocodeResult(BaseModel):
    name: str
    lat: float
    lon: float
    source: str = Field(..., description="'gazetteer' (bundled) or 'osm' (live Nominatim)")
