/** Risk vocabulary shared by the map, the gauge and every chip. */

export interface RiskStop {
  at: number;
  color: string;
}

export const RISK_STOPS: RiskStop[] = [
  { at: 0, color: "#2f5f5b" },
  { at: 33, color: "#3e7c76" },
  { at: 50, color: "#8a7a3c" },
  { at: 67, color: "#a9832f" },
  { at: 80, color: "#b04a2c" },
  { at: 100, color: "#7c2d1c" },
];

export type Band = "Low" | "Medium" | "High";

export function bandOf(risk: number): Band {
  if (risk <= 33) return "Low";
  if (risk <= 66) return "Medium";
  return "High";
}

export function riskColor(risk: number): string {
  const value = Math.max(0, Math.min(100, risk));
  let previous = RISK_STOPS[0];
  for (const stop of RISK_STOPS) {
    if (value <= stop.at) {
      if (stop.at === previous.at) return stop.color;
      const t = (value - previous.at) / (stop.at - previous.at);
      return mix(previous.color, stop.color, t);
    }
    previous = stop;
  }
  return RISK_STOPS[RISK_STOPS.length - 1].color;
}

function mix(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const ch = (shift: number) => {
    const va = (pa >> shift) & 255;
    const vb = (pb >> shift) & 255;
    return Math.round(va + (vb - va) * t);
  };
  return `rgb(${ch(16)}, ${ch(8)}, ${ch(0)})`;
}

/** MapLibre data-driven interpolate expression for the same ramp. */
export const RISK_PAINT_EXPRESSION: unknown[] = [
  "interpolate",
  ["linear"],
  ["coalesce", ["get", "risk"], 0],
  ...RISK_STOPS.flatMap((s) => [s.at, s.color]),
];

export const BAND_COPY: Record<Band, { blurb: string }> = {
  Low: { blurb: "Passable. Normal caution." },
  Medium: { blurb: "Standing water likely at low points." },
  High: { blurb: "Expect impassable sections. Consider an alternative." },
};

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds / 60);
  if (total < 60) return `${total} min`;
  return `${Math.floor(total / 60)}h ${String(total % 60).padStart(2, "0")}m`;
}

export function formatDistance(metres: number): string {
  return `${(metres / 1000).toFixed(1)} km`;
}
