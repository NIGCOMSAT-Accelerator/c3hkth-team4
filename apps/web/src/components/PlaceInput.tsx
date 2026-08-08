import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GeocodeResult, type Landmark } from "@/api/client";

/**
 * Place picker: a browsable list AND a search box.
 *
 * Typing alone was not enough — with a bundled gazetteer of 40 places, a user
 * who does not already know Abuja's district names had no way to discover
 * them. Opening the field now shows the whole list; typing filters it; and a
 * query that matches nothing locally still falls through to the geocoder so
 * arbitrary addresses keep working.
 *
 * The local filter matters offline too: under DEMO_MODE the geocoder serves
 * the gazetteer only, and filtering in the browser means the list stays
 * instant with no request at all.
 */

const KIND_LABEL: Record<string, string> = {
  district: "district",
  landmark: "landmark",
  transport: "airport",
  water: "water",
  junction: "junction",
};

export function PlaceInput({
  id,
  label,
  value,
  onSelect,
  placeholder,
}: {
  id: string;
  label: string;
  value: GeocodeResult | null;
  onSelect: (place: GeocodeResult | null) => void;
  placeholder?: string;
}) {
  const [text, setText] = useState(value?.name ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => setText(value?.name ?? ""), [value]);

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  // Fetched once and shared across every PlaceInput via the query cache.
  const places = useQuery({
    queryKey: ["landmarks"],
    queryFn: () => api.landmarks(),
    staleTime: Infinity,
  });

  const query = text.trim().toLowerCase();
  const isBrowsing = query.length === 0 || query === value?.name.toLowerCase();

  const local: Landmark[] = useMemo(() => {
    const all = places.data?.landmarks ?? [];
    if (isBrowsing) return all;
    return all.filter((p) => p.name.toLowerCase().includes(query));
  }, [places.data, query, isBrowsing]);

  // Only ask the geocoder when the gazetteer has nothing — it is the fallback,
  // not the first resort, and under DEMO_MODE it has nothing extra to offer.
  const remote = useQuery({
    queryKey: ["geocode", query],
    queryFn: () => api.geocode(query),
    enabled: open && query.length >= 2 && !isBrowsing && local.length === 0,
  });

  const options: GeocodeResult[] = useMemo(() => {
    if (local.length > 0) {
      return local.map((p) => ({ name: p.name, lat: p.lat, lon: p.lon, source: "gazetteer" as const, kind: p.kind }));
    }
    return (remote.data?.results ?? []).map((r) => ({ ...r }));
  }, [local, remote.data]);

  useEffect(() => setActive(0), [query, open]);

  // Keep the highlighted option in view when arrowing through 40 entries.
  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const choose = (place: GeocodeResult) => {
    onSelect(place);
    setText(place.name);
    setOpen(false);
  };

  const loading = places.isLoading || remote.isLoading;

  return (
    <div ref={box} className="relative">
      <label htmlFor={id} className="label mb-1.5 block">
        {label}
      </label>

      <div className="relative">
        <input
          id={id}
          className="field pr-9"
          autoComplete="off"
          placeholder={placeholder}
          value={text}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={open && options[active] ? `${id}-opt-${active}` : undefined}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
            if (value) onSelect(null);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && !open) {
              setOpen(true);
              return;
            }
            if (!open || options.length === 0) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((i) => (i + 1) % options.length);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => (i - 1 + options.length) % options.length);
            } else if (e.key === "Enter") {
              e.preventDefault();
              choose(options[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />

        {/* The affordance that says "this is a list, not just a text box". */}
        <button
          type="button"
          tabIndex={-1}
          aria-label={open ? "Hide places" : "Show all places"}
          onClick={() => {
            setOpen((o) => !o);
            document.getElementById(id)?.focus();
          }}
          className="absolute inset-y-0 right-0 grid w-9 place-items-center text-silt transition-colors hover:text-bone"
        >
          <svg
            width="10"
            height="6"
            viewBox="0 0 10 6"
            aria-hidden
            className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`}
          >
            <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {open && (
        <ul
          id={`${id}-list`}
          ref={listRef}
          role="listbox"
          aria-label={`${label} suggestions`}
          className="panel-solid absolute z-30 mt-1 max-h-72 w-full overflow-auto p-1"
        >
          {loading && (
            <>
              <li className="skeleton m-1 h-8" />
              <li className="skeleton m-1 h-8" />
            </>
          )}

          {!loading && options.length === 0 && (
            <li className="px-3 py-2.5 text-xs leading-relaxed text-silt">
              No match for “{text.trim()}”. Try a district — Wuse, Maitama, Lugbe, Kubwa.
            </li>
          )}

          {!loading && isBrowsing && options.length > 0 && (
            <li className="label border-b border-tarmac-800 px-3 pb-2 pt-1.5">
              {options.length} places in Abuja
            </li>
          )}

          {options.map((place, i) => (
            <li key={`${place.name}-${i}`}>
              <button
                type="button"
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={i === active}
                data-index={i}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(place)}
                className={`flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm transition-colors ${
                  i === active ? "bg-tarmac-800 text-bone" : "text-ash hover:bg-tarmac-800/60"
                }`}
              >
                <span className="truncate">{place.name}</span>
                <span className="label shrink-0">
                  {(place as GeocodeResult & { kind?: string }).kind
                    ? KIND_LABEL[(place as GeocodeResult & { kind?: string }).kind!] ?? "place"
                    : place.source}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
