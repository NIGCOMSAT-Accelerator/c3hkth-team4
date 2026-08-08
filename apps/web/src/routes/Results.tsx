import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { RiskGauge } from "@/components/RiskGauge";
import { RiskMap } from "@/components/RiskMap";
import { formatDistance, formatDuration, riskColor } from "@/lib/risk";

const NO_LANDMARKS: never[] = [];

export default function Results() {
  const [params] = useSearchParams();
  const [lambda, setLambda] = useState(3);
  const [showRisk, setShowRisk] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [bbox, setBbox] = useState("7.25,8.90,7.62,9.22");
  const [zoom, setZoom] = useState(10.4);

  // Shed detail as the view widens, exactly as a paper map does. Without this
  // the 5000-feature cap returns a scattered subset that draws as confetti
  // instead of a road network.
  const classesForZoom =
    zoom < 11.5
      ? "motorway,trunk,primary,motorway_link,trunk_link"
      : zoom < 13
        ? "motorway,trunk,primary,secondary,motorway_link,trunk_link,primary_link"
        : undefined;

  const origin = { lat: Number(params.get("olat")), lon: Number(params.get("olon")) };
  const destination = { lat: Number(params.get("dlat")), lon: Number(params.get("dlon")) };
  const originName = params.get("oname") ?? "Origin";
  const destinationName = params.get("dname") ?? "Destination";
  const valid = Number.isFinite(origin.lat) && Number.isFinite(destination.lat);

  const route = useQuery({
    queryKey: ["route", origin, destination, lambda],
    queryFn: () => api.analyzeRoute(origin, destination, lambda),
    enabled: valid,
    placeholderData: (prev) => prev,
  });

  const places = useQuery({ queryKey: ["landmarks"], queryFn: () => api.landmarks() });

  const segments = useQuery({
    queryKey: ["segments", bbox, classesForZoom],
    queryFn: () => api.segments(bbox, 0, classesForZoom),
    enabled: showRisk,
    placeholderData: (prev) => prev,
  });

  if (!valid) {
    return (
      <div className="mx-auto max-w-md p-16 text-center">
        <div className="label mb-2">No journey selected</div>
        <p className="mb-6 text-sm text-ash">Choose a start and destination to analyze.</p>
        <Link to="/" className="btn-primary">
          Plan a route
        </Link>
      </div>
    );
  }

  const data = route.data;
  const safest = data?.safest;
  const fastest = data?.fastest;

  return (
    <div className="mx-auto grid max-w-[1400px] gap-6 px-5 py-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      {/* ---------------------------------------------------- left rail */}
      <div className="space-y-5">
        <div>
          <div className="label mb-1.5">Route analyzed</div>
          <h1 className="text-lg leading-snug text-bone">
            {originName} <span className="text-silt">→</span> {destinationName}
          </h1>
          {data && (
            <p className="label mt-1.5">Valid {data.graph.risk_date ?? "—"}</p>
          )}
        </div>

        <section className="panel px-5 py-7">
          {route.isLoading && !data ? (
            <div className="flex flex-col items-center gap-4">
              <div className="skeleton h-[200px] w-[200px] rounded-full" />
              <div className="skeleton h-4 w-32" />
            </div>
          ) : route.isError ? (
            <div className="text-center">
              <div className="label mb-2">Could not analyze</div>
              <p className="mb-5 text-xs leading-relaxed text-ash">
                {(route.error as Error).message}
              </p>
              <button className="btn-ghost" onClick={() => route.refetch()}>
                Retry
              </button>
            </div>
          ) : (
            safest && <RiskGauge value={safest.route_risk} label="Route risk · safest" />
          )}
        </section>

        {data && fastest && safest && (
          <>
            <section className="panel p-5">
              <div className="label mb-3">Recommendation</div>
              <p className="text-sm leading-relaxed text-bone">{data.recommendation}</p>

              {!data.routes_identical && (
                <dl className="mt-5 grid grid-cols-3 gap-3 border-t border-tarmac-800 pt-4">
                  <Stat
                    label="Extra time"
                    value={`+${Math.round(data.delay_minutes)}`}
                    unit="min"
                  />
                  <Stat
                    label="Risk cut"
                    value={`${Math.round(data.risk_reduction_pct)}%`}
                    tone={data.risk_reduction_pct > 0 ? "good" : undefined}
                  />
                  <Stat label="Detour" value={formatDistance(safest.distance_m - fastest.distance_m)} />
                </dl>
              )}
            </section>

            <section className="panel p-5">
              <div className="label mb-3.5">Why this score</div>
              <div className="space-y-3">
                <Evidence
                  label="Worst segment on route"
                  value={`${Math.round(safest.max_segment_risk)}`}
                  weight="0.6 of route risk"
                  colour={riskColor(safest.max_segment_risk)}
                />
                <Evidence
                  label="Length-weighted mean"
                  value={`${Math.round(safest.mean_segment_risk)}`}
                  weight="0.4 of route risk"
                  colour={riskColor(safest.mean_segment_risk)}
                />
                <Evidence
                  label="Segments traversed"
                  value={String(safest.segment_count)}
                  weight={formatDistance(safest.distance_m)}
                />
              </div>
              <p className="mt-4 border-t border-tarmac-800 pt-3.5 text-xs leading-relaxed text-silt">
                Route risk weights the worst segment above the average, because one
                impassable point closes a road however good the rest of it is.
              </p>
            </section>

            <section className="panel p-5">
              <div className="mb-3 flex items-baseline justify-between">
                <label htmlFor="lambda" className="label">
                  Risk aversion
                </label>
                <span className="tnum font-data text-sm text-signal">λ {lambda.toFixed(1)}</span>
              </div>
              <input
                id="lambda"
                type="range"
                min={0}
                max={10}
                step={0.5}
                value={lambda}
                onChange={(e) => setLambda(Number(e.target.value))}
                className="w-full accent-[#D9903F]"
              />
              <div className="mt-2 flex justify-between font-data text-[10px] uppercase tracking-widest text-silt">
                <span>Fastest</span>
                <span>Safest</span>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-silt">
                At λ 0 you get the fastest route regardless of risk. Raising it trades
                minutes for safety.
              </p>
            </section>

            <section className="grid grid-cols-2 gap-3">
              <RouteCard
                title="Fastest"
                colour="#cb6238"
                leg={fastest}
                active={data.routes_identical}
              />
              <RouteCard title="Safest" colour="#59a197" leg={safest} active />
            </section>
          </>
        )}
      </div>

      {/* --------------------------------------------------------- map */}
      <div className="relative min-h-[520px] overflow-hidden rounded border border-tarmac-800 lg:min-h-0">
        <RiskMap
          className="absolute inset-0"
          segments={segments.data}
          fastest={fastest}
          safest={safest}
          landmarks={places.data?.landmarks ?? NO_LANDMARKS}
          origin={{ ...origin, name: originName }}
          destination={{ ...destination, name: destinationName }}
          showRisk={showRisk}
          showLabels={showLabels}
          trackUser
          onMoveEnd={(b, z) => {
            setBbox(b);
            setZoom(z);
          }}
        />

        <div className="panel absolute left-3 top-3 z-10 p-3">
          <div className="label mb-2">Layers</div>
          <label className="flex cursor-pointer items-center gap-2 text-xs text-ash">
            <input
              type="checkbox"
              checked={showRisk}
              onChange={(e) => setShowRisk(e.target.checked)}
              className="accent-[#D9903F]"
            />
            Road risk
            {segments.data?.truncated && (
              <span className="label text-laterite-400">capped</span>
            )}
          </label>
          <label className="mt-1.5 flex cursor-pointer items-center gap-2 text-xs text-ash">
            <input
              type="checkbox"
              checked={showLabels}
              onChange={(e) => setShowLabels(e.target.checked)}
              className="accent-[#D9903F]"
            />
            Place &amp; street names
          </label>
          <div className="mt-3 border-t border-tarmac-800 pt-2.5">
            <div className="label mb-1.5">Routes</div>
            <Legend colour="#cb6238" text="Fastest" />
            <Legend colour="#59a197" text="Safest" />
            <div className="mt-2 flex items-center gap-2 text-xs text-ash">
              <span className="grid h-4 w-4 place-items-center rounded-full border-2 border-tarmac-950 bg-bone font-data text-[9px] font-semibold text-tarmac-950">
                A
              </span>
              <span className="grid h-4 w-4 place-items-center rounded-full border-2 border-bone bg-tarmac-950 font-data text-[9px] font-semibold text-bone">
                B
              </span>
              <span>Start / destination</span>
            </div>
          </div>
          <div className="mt-3 border-t border-tarmac-800 pt-2.5">
            <div className="label mb-1.5">Risk</div>
            <div
              className="h-1.5 w-32 rounded"
              style={{
                background:
                  "linear-gradient(90deg,#2f5f5b,#3e7c76,#8a7a3c,#a9832f,#b04a2c,#7c2d1c)",
              }}
            />
            <div className="mt-1 flex justify-between font-data text-[9px] text-silt">
              <span>0</span>
              <span>100</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  unit,
  tone,
}: {
  label: string;
  value: string;
  unit?: string;
  tone?: "good";
}) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <div
        className={`tnum font-data text-xl leading-none ${
          tone === "good" ? "text-flood-400" : "text-bone"
        }`}
      >
        {value}
        {unit && <span className="ml-1 text-xs text-silt">{unit}</span>}
      </div>
    </div>
  );
}

function Evidence({
  label,
  value,
  weight,
  colour,
}: {
  label: string;
  value: string;
  weight: string;
  colour?: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div
        className="tnum w-11 shrink-0 rounded border px-1.5 py-1 text-center font-data text-sm"
        style={{
          color: colour ?? "#E9E5DE",
          borderColor: colour ? `${colour}55` : "#252E38",
        }}
      >
        {value}
      </div>
      <div className="min-w-0">
        <div className="truncate text-xs text-ash">{label}</div>
        <div className="label mt-0.5">{weight}</div>
      </div>
    </div>
  );
}

function Legend({ colour, text }: { colour: string; text: string }) {
  return (
    <div className="flex items-center gap-2 py-0.5 text-xs text-ash">
      <span className="h-0.5 w-5 rounded" style={{ background: colour }} />
      {text}
    </div>
  );
}

function RouteCard({
  title,
  colour,
  leg,
  active,
}: {
  title: string;
  colour: string;
  leg: { distance_m: number; duration_s: number; route_risk: number };
  active?: boolean;
}) {
  return (
    <div
      className="panel p-4"
      style={active ? { borderColor: `${colour}66` } : undefined}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="h-0.5 w-4 rounded" style={{ background: colour }} />
        <span className="label">{title}</span>
      </div>
      <div className="tnum font-data text-lg text-bone">{formatDuration(leg.duration_s)}</div>
      <div className="label mt-1">{formatDistance(leg.distance_m)}</div>
      <div className="tnum mt-2 font-data text-xs" style={{ color: riskColor(leg.route_risk) }}>
        risk {Math.round(leg.route_risk)}
      </div>
    </div>
  );
}
