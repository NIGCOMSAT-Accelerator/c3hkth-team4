import { useQuery } from "@tanstack/react-query";
import { api, type AlertCluster } from "@/api/client";
import { riskColor } from "@/lib/risk";

/**
 * What you get when you click a corridor: the same evidence the API serves for
 * a point, resolved at that corridor's centroid.
 *
 * A row in a list that cannot be opened is a dead end — the whole reason to
 * rank corridors is so someone can ask "why that one?" and get an answer.
 */
export function CorridorDetail({
  cluster,
  onClose,
}: {
  cluster: AlertCluster;
  onClose: () => void;
}) {
  const [lon, lat] = cluster.centroid.coordinates;

  const detail = useQuery({
    queryKey: ["risk-point", lat, lon],
    queryFn: () => api.riskAtPoint(lat, lon),
  });

  const colour = riskColor(cluster.peak_risk);

  return (
    <section className="panel p-5" aria-live="polite">
      <div className="flex items-start gap-3">
        <div className="tnum font-data text-3xl leading-none" style={{ color: colour }}>
          {Math.round(cluster.peak_risk)}
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm leading-tight text-bone">{cluster.name}</h3>
          <div className="label mt-1">
            {cluster.highway_class ?? "road"} · {cluster.segments} segments · peak risk
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded px-2 py-1 font-data text-[10px] uppercase tracking-widest text-silt hover:text-bone"
          aria-label="Close corridor detail"
        >
          Close
        </button>
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-tarmac-800 pt-3.5">
        <div>
          <dt className="label">Peak</dt>
          <dd className="tnum font-data text-sm" style={{ color: colour }}>
            {Math.round(cluster.peak_risk)}
          </dd>
        </div>
        <div>
          <dt className="label">Mean</dt>
          <dd className="tnum font-data text-sm text-ash">{Math.round(cluster.mean_risk)}</dd>
        </div>
        <div>
          <dt className="label">Above drainage</dt>
          <dd className="tnum font-data text-sm text-ash">
            {cluster.hand_min_m !== null ? `${cluster.hand_min_m} m` : "—"}
          </dd>
        </div>
      </dl>

      <div className="mt-4 border-t border-tarmac-800 pt-3.5">
        <div className="label mb-2">Why this corridor</div>

        {detail.isLoading && (
          <div className="space-y-2">
            <div className="skeleton h-3 w-full" />
            <div className="skeleton h-3 w-4/5" />
          </div>
        )}

        {detail.isError && (
          <div>
            <p className="text-xs leading-relaxed text-ash">{(detail.error as Error).message}</p>
            <button className="btn-ghost mt-3" onClick={() => detail.refetch()}>
              Retry
            </button>
          </div>
        )}

        {detail.data && (
          <>
            <p className="text-xs leading-relaxed text-bone">{detail.data.explanation}</p>
            {detail.data.evidence.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-1.5">
                {detail.data.evidence.map((e) => (
                  <li
                    key={e.label}
                    className="rounded border border-tarmac-700 px-2 py-1 font-data text-[10px] text-ash"
                  >
                    <span className="text-silt">{e.label}</span>{" "}
                    <span className="tnum text-bone">
                      {e.value}
                      {e.unit ? ` ${e.unit}` : ""}
                    </span>
                    {e.weight ? <span className="ml-1 text-silt">w{e.weight}</span> : null}
                  </li>
                ))}
              </ul>
            )}
            <p className="label mt-3">
              Nearest segment {detail.data.distance_m} m away · valid {detail.data.valid_date}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
