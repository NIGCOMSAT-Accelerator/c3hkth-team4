/**
 * The single typed entry point to the API. No component calls fetch directly.
 *
 * Errors arrive from the API as {error:{code,message,detail}}; we surface
 * `message` verbatim because the API was written to say what went wrong and
 * what to do about it, and rewriting that in the UI would lose the advice.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly detail?: unknown,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      "network_unreachable",
      "Could not reach the ClimatePass API. Check that it is running on port 8000, then retry.",
    );
  }

  const text = await response.text();
  const body = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const err = body?.error;
    throw new ApiError(
      err?.code ?? "http_error",
      err?.message ?? `Request failed with status ${response.status}.`,
      err?.detail,
      response.status,
    );
  }
  return body as T;
}

// ---------------------------------------------------------------- types

export interface LatLon {
  lat: number;
  lon: number;
}

export interface GeocodeResult {
  name: string;
  lat: number;
  lon: number;
  source: "gazetteer" | "osm";
}

export interface SegmentFeature {
  type: "Feature";
  id: number;
  geometry: { type: "LineString"; coordinates: [number, number][] };
  properties: {
    name: string | null;
    highway_class: string | null;
    risk: number;
    band: string;
  };
}

export interface SegmentCollection {
  type: "FeatureCollection";
  valid_date: string;
  truncated: boolean;
  count: number;
  features: SegmentFeature[];
}

export interface RouteLeg {
  geometry: { type: "LineString"; coordinates: [number, number][] };
  segments: { type: "FeatureCollection"; features: SegmentFeature[] };
  distance_m: number;
  duration_s: number;
  route_risk: number;
  max_segment_risk: number;
  mean_segment_risk: number;
  segment_count: number;
}

export interface RouteAnalysis {
  fastest: RouteLeg;
  safest: RouteLeg;
  lambda: number;
  delay_seconds: number;
  delay_minutes: number;
  risk_reduction_pct: number;
  routes_identical: boolean;
  identical_reason?: string;
  recommendation: string;
  graph: { risk_date: string | null; nodes: number; edges: number };
}

export interface Evidence {
  label: string;
  value: number;
  unit?: string | null;
  weight?: number | null;
}

export interface PointRisk {
  segment_id: number;
  name: string | null;
  highway_class: string | null;
  risk_score: number;
  risk_band: string;
  valid_date: string;
  distance_m: number;
  hand_min: number | null;
  wofs_freq_max: number | null;
  contributions: Record<string, unknown> | null;
  explanation: string;
  evidence: Evidence[];
}

export interface AlertCluster {
  name: string;
  highway_class: string | null;
  peak_risk: number;
  mean_risk: number;
  segments: number;
  hand_min_m: number | null;
  centroid: { type: "Point"; coordinates: [number, number] };
}

export interface Landmark {
  name: string;
  lat: number;
  lon: number;
  kind: string;
}

export interface Subscription {
  id: number;
  email: string;
  corridor_name: string | null;
  threshold: number;
  channel: string;
  active: boolean;
  created_at: string;
}

// ---------------------------------------------------------------- calls

export const api = {
  geocode: (q: string) =>
    request<{ query: string; results: GeocodeResult[] }>(
      `/v1/geocode?q=${encodeURIComponent(q)}`,
    ),

  segments: (bbox: string, minRisk = 0, classes?: string) =>
    request<SegmentCollection>(
      `/v1/segments?bbox=${encodeURIComponent(bbox)}&min_risk=${minRisk}` +
        (classes ? `&classes=${encodeURIComponent(classes)}` : ""),
    ),

  riskAtPoint: (lat: number, lon: number) =>
    request<PointRisk>(`/v1/risk/point?lat=${lat}&lon=${lon}`),

  analyzeRoute: (origin: LatLon, destination: LatLon, lambda?: number) =>
    request<RouteAnalysis>("/v1/route/analyze", {
      method: "POST",
      body: JSON.stringify({ origin, destination, lambda }),
    }),

  alerts: (limit = 10) =>
    request<{ valid_date: string; count: number; clusters: AlertCluster[] }>(
      `/v1/alerts?limit=${limit}`,
    ),

  createSubscription: (payload: {
    email: string;
    corridor: LatLon[];
    corridor_name?: string;
    threshold: number;
    channel?: string;
  }) =>
    request<Subscription>("/v1/subscriptions", {
      method: "POST",
      body: JSON.stringify({ channel: "email", ...payload }),
    }),

  landmarks: (city = "abuja") =>
    request<{ city: string; count: number; landmarks: Landmark[] }>(
      `/v1/meta/landmarks?city=${encodeURIComponent(city)}`,
    ),

  modelCard: () => request<Record<string, unknown>>("/v1/meta/model"),
};
