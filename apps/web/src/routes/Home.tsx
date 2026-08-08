import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type GeocodeResult } from "@/api/client";
import { PlaceInput } from "@/components/PlaceInput";
import { RiskMap } from "@/components/RiskMap";
import { riskColor } from "@/lib/risk";

const CITY_BBOX = "7.25,8.90,7.62,9.22";
const MAJOR = "motorway,trunk,primary,motorway_link,trunk_link";

export default function Home() {
  const navigate = useNavigate();
  const [origin, setOrigin] = useState<GeocodeResult | null>(null);
  const [destination, setDestination] = useState<GeocodeResult | null>(null);

  const alerts = useQuery({ queryKey: ["alerts", 6], queryFn: () => api.alerts(6) });
  // The network itself, so the landing page is the instrument rather than an
  // advert for one. Major roads only: this is a city-wide view.
  const network = useQuery({
    queryKey: ["segments", CITY_BBOX, MAJOR],
    queryFn: () => api.segments(CITY_BBOX, 0, MAJOR),
  });

  const ready = origin && destination;

  const analyze = () => {
    if (!ready) return;
    navigate(
      `/results?${new URLSearchParams({
        olat: String(origin.lat), olon: String(origin.lon), oname: origin.name,
        dlat: String(destination.lat), dlon: String(destination.lon), dname: destination.name,
      })}`,
    );
  };

  return (
    <div className="mx-auto grid max-w-[1400px] gap-6 px-5 py-6 lg:grid-cols-[minmax(0,470px)_minmax(0,1fr)]">
      {/* ------------------------------------------------------ left rail */}
      <div className="flex flex-col gap-5">
        <section>
          <p className="label mb-3">Flood exposure · scored daily from satellite</p>
          <h1 className="text-[2rem] font-semibold leading-[1.08] tracking-tight text-bone sm:text-[2.6rem]">
            Know which roads flood
            <span className="block text-laterite-400">before you drive them.</span>
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-ash">
            Every arterial road segment in Abuja, scored from terrain, four decades of
            satellite water observations and rainfall.
          </p>
        </section>

        <form
          className="panel space-y-4 p-5"
          onSubmit={(e) => {
            e.preventDefault();
            analyze();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <PlaceInput id="origin" label="From" value={origin} onSelect={setOrigin} placeholder="Gwarinpa" />
            <PlaceInput id="destination" label="To" value={destination} onSelect={setDestination} placeholder="Kubwa" />
          </div>
          <button type="submit" className="btn-primary w-full" disabled={!ready}>
            Analyze route
          </button>
          {!ready && (
            <p className="text-xs text-silt">Choose a start and a destination to analyze.</p>
          )}
        </form>

        <section className="flex min-h-0 flex-1 flex-col">
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="label">Highest risk today</h2>
            {alerts.data && (
              <span className="font-data text-[10px] text-silt">{alerts.data.valid_date}</span>
            )}
          </div>

          <div className="space-y-1.5">
            {alerts.isLoading && [0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-14" />)}

            {alerts.isError && (
              <div className="panel p-4">
                <p className="text-xs leading-relaxed text-ash">{(alerts.error as Error).message}</p>
                <button className="btn-ghost mt-3" onClick={() => alerts.refetch()}>
                  Retry
                </button>
              </div>
            )}

            {alerts.data?.clusters.map((c) => {
              const colour = riskColor(c.peak_risk);
              return (
                <article key={c.name} className="panel flex items-center gap-3 px-3.5 py-2.5">
                  <div className="tnum w-9 shrink-0 font-data text-xl leading-none" style={{ color: colour }}>
                    {Math.round(c.peak_risk)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] leading-tight text-bone">{c.name}</div>
                    <div className="label mt-0.5 truncate">
                      {c.highway_class ?? "road"} · {c.segments} seg
                      {c.hand_min_m !== null && ` · ${c.hand_min_m}m above drainage`}
                    </div>
                  </div>
                  {/* Peak against mean, so identical peaks still differentiate. */}
                  <div className="hidden w-20 shrink-0 sm:block" title={`mean ${Math.round(c.mean_risk)}`}>
                    <div className="h-1 w-full rounded bg-tarmac-800">
                      <div
                        className="h-1 rounded"
                        style={{ width: `${c.mean_risk}%`, background: riskColor(c.mean_risk) }}
                      />
                    </div>
                    <div className="label mt-1">mean {Math.round(c.mean_risk)}</div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </div>

      {/* ------------------------------------------------------------ map */}
      <div className="relative min-h-[460px] overflow-hidden rounded border border-tarmac-800 lg:min-h-[680px]">
        <RiskMap className="absolute inset-0" segments={network.data} showRisk />

        <div className="panel absolute left-3 top-3 z-10 px-3 py-2.5">
          <div className="label mb-1.5">Road risk · {network.data?.valid_date ?? "—"}</div>
          <div
            className="h-1.5 w-36 rounded"
            style={{
              background:
                "linear-gradient(90deg,#2f5f5b,#3e7c76,#8a7a3c,#a9832f,#b04a2c,#7c2d1c)",
            }}
          />
          <div className="mt-1 flex justify-between font-data text-[9px] text-silt">
            <span>0 low</span>
            <span>100 high</span>
          </div>
          <div className="label mt-2 border-t border-tarmac-800 pt-2">
            {network.data ? `${network.data.count.toLocaleString()} segments shown` : "loading"}
          </div>
        </div>
      </div>
    </div>
  );
}
