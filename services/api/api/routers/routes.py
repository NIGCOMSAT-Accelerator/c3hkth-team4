"""Risk-aware routing endpoint — the demo's centrepiece."""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import ApiError
from api.explain import explain_route
from api.routing.engine import DEFAULT_LAMBDA, RoutingError, analyze
from api.routing.graph import get_graph, graph_status
from api.schemas import RouteRequest

router = APIRouter(prefix="/v1", tags=["routing"])


@router.post(
    "/route/analyze",
    summary="Compare the fastest route with a flood-risk-aware alternative",
    description=(
        "Returns two routes between the same points: the fastest by travel time, "
        "and the safest under a risk-aversion parameter `lambda`. The delay and "
        "the risk reduction are computed from the pair, not hardcoded.\n\n"
        "`lambda` = 0 reproduces the fastest route; higher values trade time for "
        "safety. Route risk is 0.6 x the worst segment + 0.4 x the length-weighted "
        "mean, because one impassable segment ruins a route and a plain average "
        "would hide it."
    ),
)
def analyze_route(request: RouteRequest) -> dict:
    graph = get_graph()
    lam = request.lambda_ if request.lambda_ is not None else DEFAULT_LAMBDA

    try:
        result = analyze(
            graph,
            (request.origin.lat, request.origin.lon),
            (request.destination.lat, request.destination.lon),
            lam=lam,
        )
    except RoutingError as exc:
        raise ApiError(
            code="no_route",
            message=str(exc),
            status_code=422,
            detail={
                "origin": request.origin.model_dump(),
                "destination": request.destination.model_dump(),
                "hint": "Both points must fall within the mapped Abuja arterial network.",
            },
        ) from exc

    result["recommendation"] = explain_route(
        delay_minutes=result["delay_minutes"],
        risk_reduction_pct=result["risk_reduction_pct"],
        fastest_risk=result["fastest"]["route_risk"],
        safest_risk=result["safest"]["route_risk"],
        identical=result["routes_identical"],
        identical_reason=result.get("identical_reason"),
    )
    result["graph"] = graph_status()
    return result
