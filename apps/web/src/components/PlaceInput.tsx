import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GeocodeResult } from "@/api/client";

/** Place input with autocomplete. Falls back to the bundled gazetteer offline. */
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

  useEffect(() => setText(value?.name ?? ""), [value]);

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  const query = useQuery({
    queryKey: ["geocode", text],
    queryFn: () => api.geocode(text),
    enabled: open && text.trim().length >= 2,
  });

  const results = query.data?.results ?? [];

  const choose = (place: GeocodeResult) => {
    onSelect(place);
    setText(place.name);
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <label htmlFor={id} className="label mb-1.5 block">
        {label}
      </label>
      <input
        id={id}
        className="field"
        autoComplete="off"
        placeholder={placeholder}
        value={text}
        aria-expanded={open}
        aria-controls={`${id}-list`}
        role="combobox"
        aria-autocomplete="list"
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
          if (value) onSelect(null);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (!open || results.length === 0) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((i) => (i + 1) % results.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => (i - 1 + results.length) % results.length);
          } else if (e.key === "Enter") {
            e.preventDefault();
            choose(results[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />

      {open && text.trim().length >= 2 && (
        <ul
          id={`${id}-list`}
          role="listbox"
          className="panel absolute z-30 mt-1 max-h-64 w-full overflow-auto p-1"
        >
          {query.isLoading && <li className="skeleton m-1 h-8" />}
          {!query.isLoading && results.length === 0 && (
            <li className="px-3 py-2.5 text-xs text-silt">
              No match. Try a district — Wuse, Maitama, Lugbe, Kubwa.
            </li>
          )}
          {results.map((place, i) => (
            <li key={`${place.name}-${i}`} role="option" aria-selected={i === active}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(place)}
                className={`flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm transition-colors ${
                  i === active ? "bg-tarmac-800 text-bone" : "text-ash hover:bg-tarmac-800/60"
                }`}
              >
                <span className="truncate">{place.name}</span>
                <span className="label shrink-0">{place.source}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
