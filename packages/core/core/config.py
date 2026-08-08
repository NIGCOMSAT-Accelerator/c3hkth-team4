"""Central configuration for ClimatePass AI.

Every service imports settings from here. Nothing reads os.environ directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

_CITIES_FILE = Path(__file__).parent / "cities.yaml"


class Settings(BaseSettings):
    """Runtime settings. Env vars override defaults; .env is read if present."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = (
        "postgresql+psycopg://climatepass:climatepass@db:5432/climatepass"
    )

    # Root of the shared bind mount. Everything derived or cached lives here.
    data_dir: Path = Path("/app/data")

    # DEMO_MODE=true (P10) forbids every outbound network call: caches only,
    # frozen scenario date, bundled gazetteer. Verified with the network off.
    demo_mode: bool = False
    demo_date: str | None = None

    log_level: str = "INFO"
    api_cors_origins: str = "*"

    # Alerts (P9). Unset SMTP renders .eml files to data/outbox/ instead.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    alert_from_email: str = "alerts@climatepass.ai"
    webhook_hmac_secret: str = "dev-secret-change-me"
    web_base_url: str = "http://localhost:5173"

    @property
    def cache_dir(self) -> Path:
        """Every external fetch caches here. Non-negotiable: the demo runs offline."""
        return self.data_dir / "cache"

    @property
    def derived_dir(self) -> Path:
        """Generated rasters and vectors (dem.tif, hand.tif, wofs_freq.tif, ...)."""
        return self.data_dir / "derived"

    @property
    def outbox_dir(self) -> Path:
        """Simulated email delivery target when SMTP is unconfigured."""
        return self.data_dir / "outbox"

    def city_derived_dir(self, city_slug: str) -> Path:
        return self.derived_dir / city_slug

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.derived_dir, self.outbox_dir):
            d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class City:
    slug: str
    name: str
    country: str
    bbox: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat
    centroid: tuple[float, float]  # lon, lat
    utm_epsg: int
    timezone: str
    landmarks: list[dict[str, Any]]
    aoi_variants: dict[str, Any]

    @property
    def epsg_metric(self) -> str:
        return f"EPSG:{self.utm_epsg}"

    def bbox_for(self, variant: str | None = None) -> tuple[float, float, float, float]:
        """Return the bbox for an AOI variant, or the city default."""
        if not variant:
            return self.bbox
        try:
            return tuple(self.aoi_variants[variant]["bbox"])  # type: ignore[return-value]
        except KeyError as exc:
            available = ", ".join(self.aoi_variants) or "none"
            raise KeyError(
                f"Unknown AOI variant {variant!r} for {self.slug}. Available: {available}"
            ) from exc


@lru_cache(maxsize=1)
def load_cities() -> dict[str, City]:
    raw = yaml.safe_load(_CITIES_FILE.read_text())
    cities: dict[str, City] = {}
    for slug, cfg in raw["cities"].items():
        cities[slug] = City(
            slug=slug,
            name=cfg["name"],
            country=cfg.get("country", ""),
            bbox=tuple(cfg["bbox"]),
            centroid=tuple(cfg["centroid"]),
            utm_epsg=int(cfg["utm_epsg"]),
            timezone=cfg.get("timezone", "UTC"),
            landmarks=cfg.get("landmarks", []),
            aoi_variants=cfg.get("aoi_variants", {}),
        )
    return cities


def get_city(slug: str) -> City:
    cities = load_cities()
    try:
        return cities[slug]
    except KeyError as exc:
        raise KeyError(
            f"Unknown city {slug!r}. Known: {', '.join(cities)}"
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
