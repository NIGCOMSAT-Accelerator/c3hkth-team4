"""SQLAlchemy 2.0 typed models.

P0 defines nothing but the Base import surface. P1 fills this file with
cities, road_segments, segment_risk, subscriptions, alerts and
ingestion_runs, and generates the single Alembic migration that creates them.
"""

from __future__ import annotations

from core.db import Base

__all__ = ["Base"]
