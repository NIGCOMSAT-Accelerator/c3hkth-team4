"""SQLAlchemy 2.0 typed models for ClimatePass AI.

Geometry is always stored as EPSG:4326. Metric math happens in EPSG:32632
(see cities.yaml) and never touches these columns.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

__all__ = [
    "Base",
    "City",
    "RoadSegment",
    "SegmentRisk",
    "Subscription",
    "Alert",
    "IngestionRun",
]


class City(Base):
    """An area of interest. Abuja FCT is the only one for the hackathon."""

    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    bbox: Mapped[Any] = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    centroid: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326), nullable=False)

    segments: Mapped[list[RoadSegment]] = relationship(
        back_populates="city", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<City {self.slug}>"


class RoadSegment(Base):
    """A ~100m slice of the drivable network, plus its static flood features.

    Feature columns are populated by P3/P4 (rasters) and P5 stage B (zonal
    stats). `susceptibility` is written by BOTH scoring stages — v1 places
    placeholder values there within 30 minutes so the API and frontend can
    develop against real rows, and v2 overwrites in place. Nothing downstream
    ever has to know which stage produced the number.
    """

    __tablename__ = "road_segments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Parent OSM identity. Children of a split edge share these.
    osm_way_id: Mapped[int | None] = mapped_column(BigInteger)
    u_node: Mapped[int | None] = mapped_column(BigInteger)
    v_node: Mapped[int | None] = mapped_column(BigInteger)

    name: Mapped[str | None] = mapped_column(Text)
    highway_class: Mapped[str | None] = mapped_column(Text, index=True)
    length_m: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[Any] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=True), nullable=False
    )

    # --- terrain (P3) ---
    elev_mean: Mapped[float | None] = mapped_column(Float)
    slope_mean: Mapped[float | None] = mapped_column(Float)
    hand_min: Mapped[float | None] = mapped_column(Float)
    hand_mean: Mapped[float | None] = mapped_column(Float)

    # --- water history (P4) ---
    wofs_freq_max: Mapped[float | None] = mapped_column(Float)

    # --- drainage relationship (P5 stage B) ---
    dist_to_drainage_m: Mapped[float | None] = mapped_column(Float)
    # True where the segment crosses a channel: culverts and low-water
    # crossings, which is where roads actually fail.
    crosses_drainage: Mapped[bool | None] = mapped_column(Boolean)

    # --- scoring output (P5) ---
    susceptibility: Mapped[float | None] = mapped_column(Float)
    susceptibility_pctile: Mapped[float | None] = mapped_column(Float)
    features_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    city: Mapped[City] = relationship(back_populates="segments")
    risks: Mapped[list[SegmentRisk]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # The router and the /v1/segments bbox query both filter on this pair.
        Index("ix_road_segments_city_pctile", "city_id", "susceptibility_pctile"),
        Index("ix_road_segments_osm_way", "osm_way_id"),
    )

    def __repr__(self) -> str:
        return f"<RoadSegment {self.id} {self.name or self.highway_class}>"


class SegmentRisk(Base):
    """Daily risk per segment. One row per (segment, date)."""

    __tablename__ = "segment_risk"

    segment_id: Mapped[int] = mapped_column(
        ForeignKey("road_segments.id", ondelete="CASCADE"), primary_key=True
    )
    valid_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False)

    rain_7d_mm: Mapped[float | None] = mapped_column(Float)
    rain_24h_forecast_mm: Mapped[float | None] = mapped_column(Float)

    # Per-factor breakdown. Drives the "Why" panel's numeric chips and the
    # deterministic template explanations — no LLM in the loop.
    contributions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    segment: Mapped[RoadSegment] = relationship(back_populates="risks")

    __table_args__ = (
        # "Today's highest-risk corridors" and the alert sweep both hit this.
        Index("ix_segment_risk_date_score", "valid_date", "risk_score"),
    )

    def __repr__(self) -> str:
        return f"<SegmentRisk seg={self.segment_id} {self.valid_date} {self.risk_score:.1f}>"


class Subscription(Base):
    """A user's Corridor Watch: a route plus a risk threshold."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False
    )
    corridor: Mapped[Any] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=True), nullable=False
    )
    corridor_name: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    webhook_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    alerts: Mapped[list[Alert]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Subscription {self.id} {self.email} @{self.threshold}>"


class Alert(Base):
    """A fired threshold crossing. Debounced to 6h per subscription in P9."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("road_segments.id", ondelete="SET NULL")
    )
    triggered_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    risk_score: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    subscription: Mapped[Subscription] = relationship(back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert {self.id} sub={self.subscription_id} {self.risk_score}>"


class IngestionRun(Base):
    """Audit trail for every pipeline stage.

    This is a demo asset, not just bookkeeping — we show it to judges to prove
    the ingestion is automated and repeatable rather than hand-run once. Every
    stage writes a row, including failures. P7 also records CHIRPS gap-filling
    from Open-Meteo in `notes`, which is what we disclose in the pitch.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="CASCADE"))
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    records: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<IngestionRun {self.source} {self.status} n={self.records}>"
