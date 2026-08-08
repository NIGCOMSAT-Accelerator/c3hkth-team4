"""Deterministic template explanations. No LLM anywhere near this.

Two reasons. The demo cannot depend on an external model call succeeding on
conference wifi, and a judge asking "where did that sentence come from?"
deserves an answer more precise than "the model wrote it". Every clause below
is traceable to a number in the contributions dict.

Returns both the sentence and the evidence list, so the UI can render numeric
chips beside the prose rather than asking the user to trust it.
"""

from __future__ import annotations

from api.schemas import RiskContribution


def _band_clause(band: str, score: float) -> str:
    if band == "High":
        return f"Risk is High ({score:.0f}/100)"
    if band == "Medium":
        return f"Risk is Medium ({score:.0f}/100)"
    return f"Risk is Low ({score:.0f}/100)"


def explain_segment(
    *,
    name: str | None,
    risk_score: float,
    risk_band: str,
    contributions: dict | None,
    hand_min: float | None,
    wofs_freq_max: float | None,
    crosses_drainage: bool | None,
) -> tuple[str, list[RiskContribution]]:
    contributions = contributions or {}
    road = name or "This road segment"

    clauses: list[str] = [f"{_band_clause(risk_band, risk_score)} for {road}."]
    evidence: list[RiskContribution] = []

    # Terrain position — the dominant term in the susceptibility index.
    if hand_min is not None:
        evidence.append(
            RiskContribution(label="Height above drainage", value=round(hand_min, 1), unit="m", weight=0.40)
        )
        if hand_min <= 2:
            clauses.append(
                f"It sits just {hand_min:.1f} m above the nearest drainage channel, "
                "so it floods before the surrounding area does."
            )
        elif hand_min <= 8:
            clauses.append(
                f"It sits {hand_min:.1f} m above the nearest drainage channel, low enough "
                "to be reached by a swollen channel."
            )
        else:
            clauses.append(
                f"It sits {hand_min:.1f} m above the nearest drainage channel, which is "
                "well clear of normal channel levels."
            )

    if wofs_freq_max is not None and wofs_freq_max > 0.05:
        pct = wofs_freq_max * 100
        evidence.append(
            RiskContribution(label="Observed as water", value=round(pct, 1), unit="% of 40y", weight=0.25)
        )
        clauses.append(
            f"Satellite has observed standing water within 200 m of it in {pct:.0f}% "
            "of clear Landsat views since 1984."
        )

    if crosses_drainage:
        evidence.append(RiskContribution(label="Crosses a channel", value=1.0, weight=0.05))
        clauses.append(
            "It crosses a mapped drainage channel, so a blocked culvert here closes the road."
        )

    rain_7d = contributions.get("rain_7d_mm")
    if rain_7d is not None:
        evidence.append(RiskContribution(label="Rain, last 7 days", value=float(rain_7d), unit="mm"))
        if float(rain_7d) >= 60:
            clauses.append(
                f"{float(rain_7d):.0f} mm has fallen in the past week, so the ground is "
                "already wet and further rain will run off rather than soak in."
            )
        else:
            clauses.append(f"{float(rain_7d):.0f} mm has fallen in the past week.")

    rain_24h = contributions.get("rain_24h_forecast_mm")
    if rain_24h is not None:
        evidence.append(RiskContribution(label="Rain forecast, 24h", value=float(rain_24h), unit="mm"))
        if float(rain_24h) >= 20:
            clauses.append(
                f"A further {float(rain_24h):.0f} mm is forecast in the next 24 hours."
            )

    return " ".join(clauses), evidence


def explain_route(
    *,
    delay_minutes: float,
    risk_reduction_pct: float,
    fastest_risk: float,
    safest_risk: float,
    identical: bool,
    identical_reason: str | None = None,
) -> str:
    if identical:
        return identical_reason or "The fastest route is already the safest available."

    if risk_reduction_pct <= 0:
        return (
            "No alternative lowers route risk here; the fastest route remains the "
            "best available choice."
        )

    if delay_minutes <= 0.5:
        return (
            f"A safer route is available at no meaningful cost in time, cutting route "
            f"risk {risk_reduction_pct:.0f}% (from {fastest_risk:.0f} to {safest_risk:.0f})."
        )

    return (
        f"Taking the safer route costs {delay_minutes:.0f} extra minutes and cuts route "
        f"risk {risk_reduction_pct:.0f}%, from {fastest_risk:.0f} to {safest_risk:.0f}."
    )
