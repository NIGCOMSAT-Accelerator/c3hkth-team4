"""Risk-aware routing. The demo's centrepiece.

Two routes between the same pair of points: the fastest, and the one a driver
who has seen a road flood would actually take. The delay and the risk reduction
are computed from the pair, never hardcoded.
"""

from __future__ import annotations

import networkx as nx

from api.routing.graph import nearest_node
from core.logging import get_logger

log = get_logger("routing.engine")

DEFAULT_LAMBDA = 3.0

# Risk at or above this is treated as near-impassable. We multiply its cost
# rather than deleting the edge: deletion can disconnect the graph and return
# no route at all, and "no route" is a worse answer than "here is a bad route,
# clearly marked".
SEVERE_RISK_THRESHOLD = 85.0
SEVERE_RISK_MULTIPLIER = 50.0

# route_risk = MAX_WEIGHT * worst segment + MEAN_WEIGHT * length-weighted mean.
# Not a plain mean: one impassable segment ruins a route, and averaging hides
# exactly the thing the user needs to know.
MAX_WEIGHT = 0.6
MEAN_WEIGHT = 0.4


class RoutingError(Exception):
    """Raised when no route can be produced between two points."""


def _edge_data(graph: nx.MultiDiGraph, u: int, v: int) -> dict:
    """The cheapest parallel edge between u and v, by travel time."""
    candidates = graph.get_edge_data(u, v)
    return min(candidates.values(), key=lambda d: d.get("travel_time", float("inf")))


def safety_cost(travel_time: float, risk: float, lam: float) -> float:
    """Cost of traversing an edge under risk aversion `lam`.

    travel_time * (1 + lam * (risk/100)^2)

    The square is deliberate. A linear penalty treats a 20-risk road as
    meaningfully worse than a 10-risk one, which drivers do not; squaring
    tolerates mild risk and then rises sharply, which matches how people
    actually behave — they ignore a puddle and refuse a flooded underpass.
    """
    normalised = max(0.0, min(risk, 100.0)) / 100.0
    cost = travel_time * (1.0 + lam * normalised**2)
    if risk >= SEVERE_RISK_THRESHOLD:
        cost *= SEVERE_RISK_MULTIPLIER
    return cost


def summarise(graph: nx.MultiDiGraph, path: list[int]) -> dict:
    """Geometry, timings and risk for one path."""
    coordinates: list[tuple[float, float]] = []
    features: list[dict] = []
    total_time = total_length = 0.0
    weighted_risk = 0.0
    max_risk = 0.0

    for u, v in zip(path[:-1], path[1:], strict=True):
        data = _edge_data(graph, u, v)
        length = float(data.get("length", 0.0))
        travel_time = float(data.get("travel_time", 0.0))
        risk = float(data.get("risk", 0.0))

        geometry = data.get("geometry")
        if geometry is not None:
            points = list(geometry.coords)
        else:
            points = [
                (graph.nodes[u]["x"], graph.nodes[u]["y"]),
                (graph.nodes[v]["x"], graph.nodes[v]["y"]),
            ]
        if coordinates and points and coordinates[-1] == points[0]:
            coordinates.extend(points[1:])
        else:
            coordinates.extend(points)

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [list(p) for p in points]},
                "properties": {
                    "name": data.get("name") if isinstance(data.get("name"), str) else None,
                    "highway": data.get("highway") if isinstance(data.get("highway"), str) else None,
                    "length_m": round(length, 1),
                    "travel_time_s": round(travel_time, 1),
                    "risk": round(risk, 1),
                },
            }
        )

        total_time += travel_time
        total_length += length
        weighted_risk += risk * length
        max_risk = max(max_risk, risk)

    mean_risk = weighted_risk / total_length if total_length else 0.0
    route_risk = MAX_WEIGHT * max_risk + MEAN_WEIGHT * mean_risk

    return {
        "geometry": {"type": "LineString", "coordinates": [list(c) for c in coordinates]},
        "segments": {"type": "FeatureCollection", "features": features},
        "distance_m": round(total_length, 1),
        "duration_s": round(total_time, 1),
        "route_risk": round(route_risk, 1),
        "max_segment_risk": round(max_risk, 1),
        "mean_segment_risk": round(mean_risk, 1),
        "segment_count": len(features),
    }


def analyze(
    graph: nx.MultiDiGraph,
    origin: tuple[float, float],
    destination: tuple[float, float],
    lam: float = DEFAULT_LAMBDA,
) -> dict:
    """Fastest and safest routes, with the delay and risk reduction between them.

    origin/destination are (lat, lon).
    """
    origin_node = nearest_node(*origin)
    destination_node = nearest_node(*destination)

    if origin_node == destination_node:
        raise RoutingError(
            "Origin and destination resolve to the same point on the road network. "
            "Choose locations further apart."
        )

    try:
        fastest_path = nx.shortest_path(graph, origin_node, destination_node, weight="travel_time")
    except nx.NetworkXNoPath as exc:
        raise RoutingError(
            "No road connects these points in the mapped arterial network."
        ) from exc

    def risk_weight(u, v, data_dict):
        data = min(data_dict.values(), key=lambda d: d.get("travel_time", float("inf")))
        return safety_cost(
            float(data.get("travel_time", 0.0)), float(data.get("risk", 0.0)), lam
        )

    safest_path = nx.shortest_path(
        graph, origin_node, destination_node, weight=risk_weight
    )

    fastest = summarise(graph, fastest_path)
    safest = summarise(graph, safest_path)

    identical = fastest_path == safest_path
    delay_seconds = round(safest["duration_s"] - fastest["duration_s"], 1)
    if fastest["route_risk"] > 0:
        reduction = 100.0 * (fastest["route_risk"] - safest["route_risk"]) / fastest["route_risk"]
    else:
        reduction = 0.0

    result = {
        "fastest": fastest,
        "safest": safest,
        "lambda": lam,
        "delay_seconds": delay_seconds,
        "delay_minutes": round(delay_seconds / 60.0, 1),
        "risk_reduction_pct": round(reduction, 1),
        "routes_identical": identical,
        "origin_node": int(origin_node),
        "destination_node": int(destination_node),
    }

    if identical:
        # Never return two identical routes silently — say why.
        result["identical_reason"] = (
            "The fastest route is already the safest available: no alternative path "
            f"between these points lowers route risk at lambda={lam}. Its risk is "
            f"{fastest['route_risk']}, peaking at {fastest['max_segment_risk']}."
        )

    log.info(
        "route_analyzed",
        lam=lam,
        fastest_risk=fastest["route_risk"],
        safest_risk=safest["route_risk"],
        delay_s=delay_seconds,
        reduction_pct=round(reduction, 1),
        identical=identical,
    )
    return result
