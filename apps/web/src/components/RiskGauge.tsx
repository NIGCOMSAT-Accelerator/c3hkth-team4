import { useEffect, useRef, useState } from "react";
import { BAND_COPY, bandOf, riskColor } from "@/lib/risk";

/**
 * The risk score reveal. This is the one element the product should be
 * remembered by, so it is built as an instrument rather than a badge: a swept
 * arc with real tick marks, tabular figures, and a needle that settles into
 * position the way a gauge does.
 *
 * The sweep respects prefers-reduced-motion by snapping straight to value.
 */
export function RiskGauge({
  value,
  label = "Route risk",
  sublabel,
  size = 260,
}: {
  value: number;
  label?: string;
  sublabel?: string;
  size?: number;
}) {
  const [shown, setShown] = useState(0);
  const frame = useRef<number>();

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setShown(value);
      return;
    }
    const start = performance.now();
    const from = shown;
    const duration = 900;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      // Ease-out cubic: quick sweep, unhurried settle.
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(from + (value - from) * eased);
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current) cancelAnimationFrame(frame.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const band = bandOf(value);
  const colour = riskColor(shown);

  // 240 degrees of sweep, opening downward like a pressure dial.
  const START = 150;
  const SWEEP = 240;
  const r = size / 2 - 26;
  const cx = size / 2;
  const cy = size / 2;

  const polar = (deg: number, radius: number) => {
    const rad = (deg * Math.PI) / 180;
    return [cx + radius * Math.cos(rad), cy + radius * Math.sin(rad)];
  };

  const arcPath = (fromPct: number, toPct: number, radius: number) => {
    const a0 = START + SWEEP * fromPct;
    const a1 = START + SWEEP * toPct;
    const [x0, y0] = polar(a0, radius);
    const [x1, y1] = polar(a1, radius);
    return `M ${x0} ${y0} A ${radius} ${radius} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`;
  };

  const pct = Math.max(0, Math.min(100, shown)) / 100;

  return (
    <figure className="flex flex-col items-center">
      <svg
        width={size}
        height={size * 0.82}
        viewBox={`0 0 ${size} ${size * 0.82}`}
        role="img"
        aria-label={`${label}: ${Math.round(value)} out of 100, ${band}`}
      >
        {/* Track */}
        <path
          d={arcPath(0, 1, r)}
          fill="none"
          stroke="#252E38"
          strokeWidth={10}
          strokeLinecap="round"
        />
        {/* Value */}
        <path
          d={arcPath(0, Math.max(pct, 0.001), r)}
          fill="none"
          stroke={colour}
          strokeWidth={10}
          strokeLinecap="round"
          style={{ filter: `drop-shadow(0 0 10px ${colour}55)` }}
        />
        {/* Ticks every 10, longer at band boundaries */}
        {Array.from({ length: 11 }, (_, i) => {
          const t = i / 10;
          const major = i === 0 || i === 10 || i * 10 === 30 || i * 10 === 70;
          const [x0, y0] = polar(START + SWEEP * t, r - 12);
          const [x1, y1] = polar(START + SWEEP * t, r - (major ? 21 : 17));
          return (
            <line
              key={i}
              x1={x0}
              y1={y0}
              x2={x1}
              y2={y1}
              stroke={major ? "#5B6B7A" : "#33404D"}
              strokeWidth={major ? 1.6 : 1}
            />
          );
        })}
      </svg>

      <figcaption className="-mt-[26%] flex flex-col items-center">
        <div
          className="tnum font-data text-6xl font-medium leading-none sm:text-7xl"
          style={{ color: colour }}
        >
          {Math.round(shown)}
        </div>
        <div className="label mt-2">{label}</div>
        <div
          className="mt-3 rounded-full border px-3 py-1 font-data text-[11px] uppercase tracking-widest"
          style={{ borderColor: `${colour}66`, color: colour }}
        >
          {band}
        </div>
        <p className="mt-3 max-w-[24ch] text-center text-xs leading-relaxed text-ash">
          {sublabel ?? BAND_COPY[band].blurb}
        </p>
      </figcaption>
    </figure>
  );
}
