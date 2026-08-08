import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type GeocodeResult, type Subscription } from "@/api/client";
import { PlaceInput } from "@/components/PlaceInput";
import { riskColor } from "@/lib/risk";

export default function Alerts() {
  const clusters = useQuery({ queryKey: ["alerts", 8], queryFn: () => api.alerts(8) });

  const [from, setFrom] = useState<GeocodeResult | null>(null);
  const [to, setTo] = useState<GeocodeResult | null>(null);
  const [email, setEmail] = useState("");
  const [threshold, setThreshold] = useState(60);
  const [created, setCreated] = useState<Subscription | null>(null);

  const subscribe = useMutation({
    mutationFn: () =>
      api.createSubscription({
        email,
        corridor: [
          { lat: from!.lat, lon: from!.lon },
          { lat: to!.lat, lon: to!.lon },
        ],
        corridor_name: `${from!.name} to ${to!.name}`,
        threshold,
      }),
    onSuccess: setCreated,
  });

  const ready = from && to && email.includes("@");

  return (
    <div className="mx-auto max-w-[1400px] px-5 py-10">
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,440px)] lg:gap-16">
        <section>
          <div className="mb-4 flex items-baseline justify-between">
            <h1 className="label">Active high-risk corridors</h1>
            {clusters.data && (
              <span className="font-data text-[10px] text-silt">{clusters.data.valid_date}</span>
            )}
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            {clusters.isLoading &&
              [0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-24" />)}

            {clusters.isError && (
              <div className="panel p-4 sm:col-span-2">
                <p className="text-xs text-ash">{(clusters.error as Error).message}</p>
                <button className="btn-ghost mt-3" onClick={() => clusters.refetch()}>
                  Retry
                </button>
              </div>
            )}

            {clusters.data?.clusters.map((c) => (
              <article key={c.name} className="panel p-4">
                <div className="flex items-start gap-3">
                  <div
                    className="tnum font-data text-3xl leading-none"
                    style={{ color: riskColor(c.peak_risk) }}
                  >
                    {Math.round(c.peak_risk)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-sm text-bone">{c.name}</h2>
                    <div className="label mt-1">{c.highway_class ?? "road"}</div>
                  </div>
                </div>
                <dl className="mt-3 flex gap-4 border-t border-tarmac-800 pt-2.5">
                  <div>
                    <dt className="label">Mean</dt>
                    <dd className="tnum font-data text-xs text-ash">
                      {Math.round(c.mean_risk)}
                    </dd>
                  </div>
                  <div>
                    <dt className="label">Segments</dt>
                    <dd className="tnum font-data text-xs text-ash">{c.segments}</dd>
                  </div>
                  {c.hand_min_m !== null && (
                    <div>
                      <dt className="label">Above drainage</dt>
                      <dd className="tnum font-data text-xs text-ash">{c.hand_min_m} m</dd>
                    </div>
                  )}
                </dl>
              </article>
            ))}
          </div>
        </section>

        <aside>
          <h2 className="label mb-1.5">Corridor Watch</h2>
          <p className="mb-5 text-sm leading-relaxed text-ash">
            Watch a route and we will email you when its risk crosses your threshold.
            Checked every 30 minutes, at most one alert per corridor per six hours.
          </p>

          {created ? (
            <div className="panel p-5">
              <div className="label mb-2 text-flood-400">Watch created</div>
              <p className="text-sm leading-relaxed text-bone">
                {created.corridor_name}
              </p>
              <p className="mt-2 text-xs text-ash">
                Alerting {created.email} above risk {created.threshold}.
              </p>
              <button
                className="btn-ghost mt-4"
                onClick={() => {
                  setCreated(null);
                  setFrom(null);
                  setTo(null);
                  setEmail("");
                }}
              >
                Watch another corridor
              </button>
            </div>
          ) : (
            <form
              className="panel relative z-20 space-y-4 p-5"
              onSubmit={(e) => {
                e.preventDefault();
                if (ready) subscribe.mutate();
              }}
            >
              <PlaceInput id="watch-from" label="Corridor start" value={from} onSelect={setFrom} />
              <PlaceInput id="watch-to" label="Corridor end" value={to} onSelect={setTo} />

              <div>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <label htmlFor="threshold" className="label">
                    Alert above
                  </label>
                  <span
                    className="tnum font-data text-sm"
                    style={{ color: riskColor(threshold) }}
                  >
                    {threshold}
                  </span>
                </div>
                <input
                  id="threshold"
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={threshold}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                  className="w-full accent-[#D9903F]"
                />
              </div>

              <div>
                <label htmlFor="email" className="label mb-1.5 block">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  className="field"
                  placeholder="ops@example.ng"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>

              {subscribe.isError && (
                <p className="text-xs leading-relaxed text-laterite-300">
                  {(subscribe.error as Error).message}
                </p>
              )}

              <button type="submit" className="btn-primary w-full" disabled={!ready || subscribe.isPending}>
                {subscribe.isPending ? "Creating…" : "Create Corridor Watch"}
              </button>
            </form>
          )}
        </aside>
      </div>
    </div>
  );
}
