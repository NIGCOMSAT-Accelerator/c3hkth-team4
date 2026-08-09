import { useQuery } from "@tanstack/react-query";
import { api, type ModelCard } from "@/api/client";
import { riskColor } from "@/lib/risk";

/**
 * /v1/meta/model, made readable.
 *
 * The endpoint is the honest core of this product — live weights, the formula,
 * where the data came from and what it cannot see. As raw JSON only a
 * developer could read it, which rather defeats the purpose of publishing it.
 *
 * Everything on this page is fetched, never hardcoded. If someone changes a
 * weight in the pipeline, this page changes with it. A methodology page that
 * can drift from the model it describes is worse than no page at all.
 */

const FEATURE_LABEL: Record<string, string> = {
  hand_min: "Height above nearest drainage",
  wofs_freq_max: "Observed water frequency",
  slope_mean: "Slope",
  dist_to_drainage_m: "Distance to drainage",
  crosses_drainage: "Crosses a channel",
};

/** `rain_7d=47.8mm; forecast_24h=2.8mm; source=…; chirps_latency_days=54` */
function parseProvenance(raw: string | null) {
  if (!raw) return null;
  const out: Record<string, string> = {};
  for (const part of raw.split(";")) {
    const at = part.indexOf("=");
    if (at > 0) out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  return out;
}

export default function Model() {
  const card = useQuery({ queryKey: ["model-card"], queryFn: () => api.modelCard() });

  if (card.isLoading) {
    return (
      <div className="mx-auto max-w-[900px] space-y-4 px-5 py-12">
        <div className="skeleton h-8 w-2/3" />
        <div className="skeleton h-40" />
        <div className="skeleton h-64" />
      </div>
    );
  }

  if (card.isError || !card.data) {
    return (
      <div className="mx-auto max-w-md p-16 text-center">
        <div className="label mb-2">Could not load the model card</div>
        <p className="mb-6 text-xs leading-relaxed text-ash">
          {(card.error as Error)?.message}
        </p>
        <button className="btn-ghost" onClick={() => card.refetch()}>
          Retry
        </button>
      </div>
    );
  }

  const m: ModelCard = card.data;
  const prov = parseProvenance(m.rainfall_provenance);
  const chirpsLate = prov?.chirps_latency_days;

  return (
    <div className="mx-auto max-w-[900px] px-5 py-10 sm:py-14">
      {/* ------------------------------------------------------- header */}
      <header className="mb-12">
        <p className="label mb-3">Methodology · served live from the API</p>
        <h1 className="max-w-[20ch] text-3xl font-semibold leading-[1.12] tracking-tight text-bone sm:text-4xl">
          How the risk score is computed
        </h1>
        <p className="mt-4 max-w-[62ch] text-sm leading-relaxed text-ash">
          Every number on this page is read from the running model, not written into
          the page. Change a weight in the pipeline and this changes with it.
        </p>
      </header>

      {/* --------------------------------------------------- the two stages */}
      <Section
        n="01"
        title="Susceptibility — where water collects"
        blurb="Static per road segment. Recomputed when the terrain inputs change, not daily."
      >
        <div className="space-y-2.5">
          {m.susceptibility.features.map((f) => (
            <article key={f.name} className="panel p-4">
              <div className="flex items-baseline gap-3">
                <span className="tnum shrink-0 font-data text-lg text-signal">
                  {f.weight.toFixed(2)}
                </span>
                <h3 className="flex-1 text-sm text-bone">
                  {FEATURE_LABEL[f.name] ?? f.name}
                  {f.unit && f.unit !== "boolean" && (
                    <span className="ml-2 font-data text-[10px] uppercase tracking-widest text-silt">
                      {f.unit}
                    </span>
                  )}
                </h3>
              </div>

              {/* Weight as a bar: the relative importance should be visible
                  before anyone reads a word of the rationale. */}
              <div className="mt-2.5 h-1 w-full overflow-hidden rounded bg-tarmac-800">
                <div
                  className="h-1 rounded bg-signal/70"
                  style={{ width: `${f.weight * 100}%` }}
                />
              </div>

              <p className="mt-3 text-xs leading-relaxed text-ash">{f.rationale}</p>
              <p className="label mt-2.5 border-t border-tarmac-800 pt-2.5">{f.source}</p>
            </article>
          ))}
        </div>

        <p className="mt-4 text-xs leading-relaxed text-silt">{m.susceptibility.note}</p>
      </Section>

      <Section
        n="02"
        title="Daily risk — whether there is water to collect"
        blurb="Rainfall layered on top of susceptibility, recomputed every morning."
      >
        <div className="panel p-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Term
              label="Terrain"
              weight={m.daily_risk.weights.base}
              detail="Where the road sits"
            />
            <Term
              label="Ground already wet"
              weight={m.daily_risk.weights.wetness}
              detail={`Saturates at ${m.daily_risk.wetness_saturation_mm} mm over ${m.daily_risk.antecedent_days} days`}
            />
            <Term
              label="Terrain × incoming rain"
              weight={m.daily_risk.weights.interaction}
              detail={`Saturates at ${m.daily_risk.trigger_saturation_mm} mm in 24 h`}
              highlight
            />
          </div>

          <div className="mt-5 rounded border border-tarmac-800 bg-tarmac-950/60 p-3.5">
            <code className="block break-words font-data text-[11px] leading-relaxed text-ash">
              {m.daily_risk.formula}
            </code>
          </div>

          <p className="mt-4 border-t border-tarmac-800 pt-3.5 text-xs leading-relaxed text-bone">
            <span className="label mb-1.5 block">Why multiply, not add</span>
            {m.daily_risk.interaction_rationale}
          </p>
        </div>

        {/* Bands as the scale they actually are. */}
        <div className="panel mt-3 p-5">
          <div className="label mb-3">Risk bands</div>
          <div className="flex h-2 overflow-hidden rounded">
            {Object.entries(m.daily_risk.bands).map(([name, [lo, hi]]) => (
              <div
                key={name}
                style={{
                  width: `${hi - lo + 1}%`,
                  background: riskColor((lo + hi) / 2),
                }}
                title={`${name} ${lo}–${hi}`}
              />
            ))}
          </div>
          <div className="mt-2 flex justify-between">
            {Object.entries(m.daily_risk.bands).map(([name, [lo, hi]]) => (
              <div key={name} className="text-center">
                <div
                  className="font-data text-[11px] uppercase tracking-widest"
                  style={{ color: riskColor((lo + hi) / 2) }}
                >
                  {name}
                </div>
                <div className="tnum label mt-0.5">
                  {lo}–{hi}
                </div>
              </div>
            ))}
          </div>
        </div>
      </Section>

      <Section
        n="03"
        title="Route risk — scoring a whole journey"
        blurb="Not an average. One impassable point closes a road however good the rest is."
      >
        <div className="panel p-5">
          <code className="block break-words font-data text-[11px] leading-relaxed text-ash">
            {m.route_risk.formula}
          </code>
          <p className="mt-3.5 text-xs leading-relaxed text-bone">{m.route_risk.rationale}</p>

          <dl className="mt-5 grid gap-4 border-t border-tarmac-800 pt-4 sm:grid-cols-2">
            <div>
              <dt className="label mb-1.5">Choosing the safer route</dt>
              <dd className="font-data text-[11px] leading-relaxed text-ash">
                {m.route_risk.safest_route_cost}
              </dd>
            </div>
            <div>
              <dt className="label mb-1.5">
                Treated as near-impassable above{" "}
                <span className="tnum" style={{ color: riskColor(m.route_risk.severe_risk_threshold) }}>
                  {m.route_risk.severe_risk_threshold}
                </span>
              </dt>
              <dd className="text-xs leading-relaxed text-ash">
                {m.route_risk.severe_rationale}
              </dd>
            </div>
          </dl>
        </div>
      </Section>

      {/* ------------------------------------------------- data provenance */}
      <Section
        n="04"
        title="Where today's rainfall came from"
        blurb="The part of this model most worth being sceptical about."
      >
        <div className="panel p-5">
          {prov ? (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                <Stat label="Rain, last 7 days" value={prov.rain_7d ?? "—"} />
                <Stat label="Forecast, next 24 h" value={prov.forecast_24h ?? "—"} />
                <Stat
                  label="CHIRPS latency"
                  value={chirpsLate ? `${chirpsLate} days` : "—"}
                  tone={Number(chirpsLate) > 7 ? "warn" : undefined}
                />
              </div>
              <p className="mt-4 border-t border-tarmac-800 pt-3.5 font-data text-[11px] leading-relaxed text-silt">
                {prov.source ?? m.rainfall_provenance}
              </p>
            </>
          ) : (
            <p className="text-xs text-ash">No scoring run has recorded rainfall provenance yet.</p>
          )}

          {Number(chirpsLate) > 7 && (
            <p className="mt-3.5 rounded border border-laterite-600/40 bg-laterite-600/10 p-3 text-xs leading-relaxed text-laterite-300">
              CHIRPS is genuinely satellite-derived rainfall, and on this feed it runs{" "}
              {chirpsLate} days behind. It therefore contributes nothing to today's
              antecedent window, and Open-Meteo — a weather model, not a satellite —
              supplies it instead. We would rather say so here than imply otherwise.
            </p>
          )}
        </div>
      </Section>

      {/* ------------------------------------------------------ validation */}
      <Section n="05" title="Validation" blurb="What has, and has not, been tested.">
        <div className="panel p-5">
          <div className="flex items-center gap-3">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${
                m.validation.status === "not_run" ? "bg-laterite-400" : "bg-flood-400"
              }`}
            />
            <span className="font-data text-xs uppercase tracking-widest text-bone">
              {String(m.validation.status ?? "unknown").replace(/_/g, " ")}
            </span>
          </div>
          {m.validation.note && (
            <p className="mt-3 text-xs leading-relaxed text-ash">{String(m.validation.note)}</p>
          )}
          <p className="mt-3 text-xs leading-relaxed text-silt">
            The weights are physically motivated but not empirically validated against
            observed flooding. We publish that rather than an accuracy figure we cannot
            support.
          </p>
        </div>
      </Section>

      {/* ----------------------------------------------------- limitations */}
      <Section
        n="06"
        title="What this model cannot see"
        blurb="Named here so nobody has to discover them."
      >
        <ol className="space-y-2">
          {m.limitations.map((l, i) => (
            <li key={i} className="panel flex gap-3 p-4">
              <span className="tnum shrink-0 font-data text-xs text-silt">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="text-xs leading-relaxed text-ash">{l}</span>
            </li>
          ))}
        </ol>
      </Section>

      {/* ---------------------------------------------------- pipeline runs */}
      <Section
        n="07"
        title="Recent pipeline runs"
        blurb="Every stage records itself. This is the audit trail, not a status page."
      >
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead>
              <tr className="border-b border-tarmac-800">
                <th className="label px-4 py-2.5">Stage</th>
                <th className="label px-4 py-2.5">Status</th>
                <th className="label px-4 py-2.5 text-right">Records</th>
                <th className="label px-4 py-2.5">Started</th>
              </tr>
            </thead>
            <tbody>
              {m.recent_ingestion_runs.map((r, i) => (
                <tr key={i} className="border-b border-tarmac-800/60 last:border-0">
                  <td className="px-4 py-2.5 font-data text-[11px] text-bone">{r.source}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`font-data text-[10px] uppercase tracking-widest ${
                        r.status === "success"
                          ? "text-flood-400"
                          : r.status === "failed"
                            ? "text-laterite-400"
                            : "text-silt"
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="tnum px-4 py-2.5 text-right font-data text-[11px] text-ash">
                    {r.records?.toLocaleString() ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 font-data text-[10px] text-silt">
                    {r.started_at.slice(0, 16).replace("T", " ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <footer className="mt-12 border-t border-tarmac-800 pt-6">
        <p className="text-xs leading-relaxed text-silt">
          Building against this?{" "}
          <a
            href="/v1/meta/model"
            className="text-ash underline decoration-tarmac-600 underline-offset-4 hover:text-bone"
          >
            The same data as JSON
          </a>
          , and the endpoint every figure here is read from.
        </p>
      </footer>
    </div>
  );
}

function Section({
  n,
  title,
  blurb,
  children,
}: {
  n: string;
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-12">
      <div className="mb-4 flex items-baseline gap-3">
        <span className="font-data text-[11px] text-silt">{n}</span>
        <div>
          <h2 className="text-base text-bone">{title}</h2>
          <p className="mt-1 text-xs leading-relaxed text-silt">{blurb}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function Term({
  label,
  weight,
  detail,
  highlight,
}: {
  label: string;
  weight: number;
  detail: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded border p-3.5 ${
        highlight ? "border-signal/40 bg-signal/5" : "border-tarmac-800"
      }`}
    >
      <div className="tnum font-data text-xl leading-none text-bone">
        {weight.toFixed(2)}
      </div>
      <div className="mt-1.5 text-xs text-ash">{label}</div>
      <div className="label mt-1.5">{detail}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <div
        className={`tnum font-data text-lg ${tone === "warn" ? "text-laterite-300" : "text-bone"}`}
      >
        {value}
      </div>
    </div>
  );
}
