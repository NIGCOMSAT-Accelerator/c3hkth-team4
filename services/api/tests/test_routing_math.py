"""Routing maths, isolated from the graph and the database."""

from __future__ import annotations

import networkx as nx
import pytest

from api.explain import explain_route
from api.routing.engine import (
    MAX_WEIGHT,
    MEAN_WEIGHT,
    SEVERE_RISK_MULTIPLIER,
    SEVERE_RISK_THRESHOLD,
    safety_cost,
    summarise,
)


def test_lambda_zero_ignores_risk_entirely():
    """lambda=0 must reproduce the fastest route exactly."""
    assert safety_cost(100.0, 0.0, 0.0) == 100.0
    assert safety_cost(100.0, 80.0, 0.0) == 100.0


def test_penalty_is_quadratic_not_linear():
    """Squaring tolerates mild risk and punishes severe risk sharply.

    Doubling risk must more than double the penalty, otherwise the curve is
    linear and the design rationale is false.
    """
    base = 100.0
    penalty_20 = safety_cost(base, 20.0, 3.0) - base
    penalty_40 = safety_cost(base, 40.0, 3.0) - base
    assert penalty_40 > 2 * penalty_20


def test_severe_risk_is_penalised_not_deleted():
    """Deleting edges can disconnect the graph; a huge cost cannot."""
    cost = safety_cost(100.0, SEVERE_RISK_THRESHOLD, 3.0)
    assert cost >= 100.0 * SEVERE_RISK_MULTIPLIER
    assert cost < float("inf"), "a severe edge must stay traversable as a last resort"


def test_risk_is_clamped_to_valid_range():
    assert safety_cost(10.0, -5.0, 3.0) == 10.0
    assert safety_cost(10.0, 250.0, 3.0) == safety_cost(10.0, 100.0, 3.0)


def _two_edge_graph(risk_a: float, risk_b: float) -> tuple[nx.MultiDiGraph, list[int]]:
    graph = nx.MultiDiGraph()
    for node, (x, y) in {1: (7.0, 9.0), 2: (7.01, 9.0), 3: (7.02, 9.0)}.items():
        graph.add_node(node, x=x, y=y)
    graph.add_edge(1, 2, 0, length=100.0, travel_time=10.0, risk=risk_a, name="A")
    graph.add_edge(2, 3, 0, length=300.0, travel_time=30.0, risk=risk_b, name="B")
    return graph, [1, 2, 3]


def test_route_risk_weights_the_worst_segment_over_the_mean():
    """0.6*max + 0.4*length-weighted mean, not a plain average.

    One short but impassable segment must dominate a long safe one — that is
    the entire reason this is not a mean.
    """
    graph, path = _two_edge_graph(risk_a=90.0, risk_b=10.0)
    result = summarise(graph, path)

    length_weighted_mean = (90.0 * 100 + 10.0 * 300) / 400
    assert result["mean_segment_risk"] == pytest.approx(length_weighted_mean, abs=0.1)
    assert result["max_segment_risk"] == 90.0

    expected = MAX_WEIGHT * 90.0 + MEAN_WEIGHT * length_weighted_mean
    assert result["route_risk"] == pytest.approx(expected, abs=0.1)
    # A plain mean would be 30.0 and would hide the impassable segment.
    assert result["route_risk"] > 2 * length_weighted_mean


def test_summarise_accumulates_distance_and_duration():
    graph, path = _two_edge_graph(20.0, 20.0)
    result = summarise(graph, path)
    assert result["distance_m"] == pytest.approx(400.0)
    assert result["duration_s"] == pytest.approx(40.0)
    assert result["segment_count"] == 2


def test_identical_routes_are_explained_never_silent():
    message = explain_route(
        delay_minutes=0.0,
        risk_reduction_pct=0.0,
        fastest_risk=40.0,
        safest_risk=40.0,
        identical=True,
        identical_reason="No alternative path lowers risk.",
    )
    assert "No alternative path lowers risk." == message


def test_recommendation_quotes_computed_numbers():
    message = explain_route(
        delay_minutes=12.0,
        risk_reduction_pct=43.0,
        fastest_risk=70.0,
        safest_risk=40.0,
        identical=False,
    )
    assert "12" in message and "43" in message and "70" in message and "40" in message
