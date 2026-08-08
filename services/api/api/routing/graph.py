"""Routing graph: built once, cached, held in memory, refreshed on a timer.

Never rebuilt per request. Loading the 233k-edge FCT graphml and filtering it
costs ~15 s, which is fine once at startup and absurd on every route call.
The built graph is cached to disk so a restart is seconds, not a rebuild.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox
from shapely.geometry import box
from sqlalchemy import text

from core.config import City as CityConfig
from core.config import get_city, settings
from core.db import session_scope
from core.logging import get_logger

log = get_logger("routing.graph")

# Mirrors processing.ingest.roads.ARTERIAL_CLASSES. Duplicated rather than
# imported because services/api must not depend on services/processing — the
# three-layer separation is a hard requirement of the brief.
ARTERIAL_CLASSES = frozenset(
    {
        "motorway", "motorway_link", "trunk", "trunk_link",
        "primary", "primary_link", "secondary", "secondary_link",
        "tertiary", "tertiary_link", "unclassified",
    }
)

RISK_REFRESH_SECONDS = 15 * 60

_lock = threading.Lock()
_graph: nx.MultiDiGraph | None = None
_risk_loaded_at: float = 0.0
_risk_date: dt.date | None = None
_median_risk: float = 0.0

# Node index for nearest-node lookup, built once with the graph.
_node_ids: np.ndarray | None = None
_node_lat: np.ndarray | None = None
_node_lon: np.ndarray | None = None


def _build_node_index(graph: nx.MultiDiGraph) -> None:
    """Flat arrays of node coordinates for vectorised nearest-node search.

    osmnx.nearest_nodes needs scikit-learn to search an unprojected graph, and
    the API layer has no other use for a 50 MB ML dependency. At ~10k nodes a
    numpy scan is sub-millisecond, so we do it ourselves.
    """
    global _node_ids, _node_lat, _node_lon
    ids, lats, lons = [], [], []
    for node, data in graph.nodes(data=True):
        ids.append(node)
        lats.append(float(data["y"]))
        lons.append(float(data["x"]))
    _node_ids = np.asarray(ids, dtype=np.int64)
    _node_lat = np.asarray(lats, dtype=np.float64)
    _node_lon = np.asarray(lons, dtype=np.float64)
    log.info("node_index_built", nodes=len(ids))


def nearest_node(lat: float, lon: float) -> int:
    """Graph node closest to a point.

    Equirectangular approximation: at city scale the error is far smaller than
    the spacing between road nodes, so it cannot change which node wins.
    """
    if _node_ids is None:
        raise RuntimeError("Node index not built — call get_graph() first.")
    dlat = _node_lat - lat
    dlon = (_node_lon - lon) * np.cos(np.radians(lat))
    return int(_node_ids[int(np.argmin(dlat * dlat + dlon * dlon))])


def _first(value):
    return value[0] if isinstance(value, list) else value


def _routing_cache_path(cfg: CityConfig, variant: str | None) -> Path:
    suffix = f"_{variant}" if variant else ""
    return settings.cache_dir / "osm" / f"{cfg.slug}{suffix}_routing.graphml"


def build_routing_graph(cfg: CityConfig, variant: str | None = "municipal") -> nx.MultiDiGraph:
    """Arterial edges inside the AOI, largest strongly connected component.

    Strongly connected, not weakly: this is a directed graph with one-way
    streets, and a weakly connected component can contain node pairs with no
    legal path between them.
    """
    cache = _routing_cache_path(cfg, variant)
    if cache.exists():
        log.info("routing_graph_cache_hit", path=str(cache))
        return ox.load_graphml(cache)

    source = settings.cache_dir / "osm" / f"{cfg.slug}.graphml"
    if not source.exists():
        raise FileNotFoundError(
            f"{source} missing. Run processing.ingest.roads to populate the OSM cache."
        )

    log.info("routing_graph_build_start", source=str(source))
    started = time.time()
    graph = ox.load_graphml(source)
    aoi = box(*cfg.bbox_for(variant))

    keep = []
    for u, v, k, data in graph.edges(keys=True, data=True):
        if _first(data.get("highway")) not in ARTERIAL_CLASSES:
            continue
        geometry = data.get("geometry")
        if geometry is not None and not geometry.intersects(aoi):
            continue
        keep.append((u, v, k))

    subgraph = graph.edge_subgraph(keep).copy()
    largest = max(nx.strongly_connected_components(subgraph), key=len)
    routing = subgraph.subgraph(largest).copy()

    routing = ox.add_edge_speeds(routing)
    routing = ox.add_edge_travel_times(routing)

    cache.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(routing, cache)
    log.info(
        "routing_graph_built",
        nodes=routing.number_of_nodes(),
        edges=routing.number_of_edges(),
        seconds=round(time.time() - started, 1),
        cached=str(cache),
    )
    return routing


def attach_risk(graph: nx.MultiDiGraph, city_slug: str, valid_date: dt.date | None = None) -> dict:
    """Write today's risk onto every edge.

    road_segments are ~100 m children of OSM edges and retain their parent
    (u_node, v_node), so edge risk is an aggregate over the children. We take
    the MAX: a route is only as passable as its worst point, and averaging a
    flooded dip out of existence is exactly the failure we are trying to avoid.
    """
    global _median_risk

    from api.dates import resolve_valid_date

    with session_scope() as session:
        # Same resolver the endpoints use, so the map and the router can never
        # disagree about which day they are showing.
        target = resolve_valid_date(session, valid_date)
        if target is None:
            log.warning("no_risk_data", city=city_slug, note="edges default to 0 risk")
            return {"date": None, "edges_with_risk": 0, "median": 0.0}

        rows = session.execute(
            text(
                "SELECT s.u_node, s.v_node, max(sr.risk_score) AS risk"
                " FROM segment_risk sr"
                " JOIN road_segments s ON s.id = sr.segment_id"
                " JOIN cities c ON c.id = s.city_id"
                " WHERE c.slug = :slug AND sr.valid_date = :d"
                "   AND s.u_node IS NOT NULL AND s.v_node IS NOT NULL"
                " GROUP BY s.u_node, s.v_node"
            ),
            {"slug": city_slug, "d": target},
        ).fetchall()

        median = session.scalar(
            text(
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY risk_score)"
                " FROM segment_risk WHERE valid_date = :d"
            ),
            {"d": target},
        ) or 0.0

    lookup = {(int(u), int(v)): float(r) for u, v, r in rows}
    _median_risk = float(median)

    hit = 0
    for u, v, key, data in graph.edges(keys=True, data=True):
        risk = lookup.get((u, v))
        if risk is None:
            # The 0.1% of edges with no scored children take the city median
            # rather than 0 — an unscored edge must not look like a safe one.
            risk = _median_risk
        else:
            hit += 1
        data["risk"] = risk

    log.info(
        "risk_attached",
        date=str(target),
        edges=graph.number_of_edges(),
        matched=hit,
        coverage_pct=round(100 * hit / max(graph.number_of_edges(), 1), 1),
        median=round(_median_risk, 1),
    )
    return {"date": target, "edges_with_risk": hit, "median": _median_risk}


def get_graph(city_slug: str = "abuja", variant: str | None = "municipal") -> nx.MultiDiGraph:
    """The shared graph. Builds on first call, refreshes risk on a timer."""
    global _graph, _risk_loaded_at, _risk_date

    with _lock:
        if _graph is None:
            cfg = get_city(city_slug)
            _graph = build_routing_graph(cfg, variant)
            _build_node_index(_graph)
            info = attach_risk(_graph, city_slug)
            _risk_loaded_at = time.time()
            _risk_date = info["date"]
        elif time.time() - _risk_loaded_at > RISK_REFRESH_SECONDS:
            # Topology is stable; only the risk values need refreshing.
            info = attach_risk(_graph, city_slug)
            _risk_loaded_at = time.time()
            _risk_date = info["date"]
        return _graph


def graph_status() -> dict:
    return {
        "loaded": _graph is not None,
        "nodes": _graph.number_of_nodes() if _graph else 0,
        "edges": _graph.number_of_edges() if _graph else 0,
        "risk_date": str(_risk_date) if _risk_date else None,
        "risk_age_seconds": round(time.time() - _risk_loaded_at, 1) if _risk_loaded_at else None,
        "median_risk": round(_median_risk, 2),
    }
