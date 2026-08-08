import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import type { RouteLeg, SegmentCollection } from "@/api/client";
import { RISK_PAINT_EXPRESSION } from "@/lib/risk";

/**
 * The map has no basemap, and that is deliberate.
 *
 * Tile servers need network and usually an API key, which would break the
 * offline demo requirement. But we already hold the road network as GeoJSON,
 * so the data draws the map: 20k segments on a dark field, coloured by risk.
 * It is self-contained, it cannot rate-limit us on stage, and it looks like
 * the hydrology chart this product wants to be rather than a consumer app
 * with pins on Google Maps.
 *
 * Layers are GeoJSON sources with data-driven styling. Segments are never
 * React components — 20k of those would melt the browser.
 */

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export interface MapLayers {
  segments?: SegmentCollection | null;
  fastest?: RouteLeg | null;
  safest?: RouteLeg | null;
  showRisk?: boolean;
  onMoveEnd?: (bbox: string, zoom: number) => void;
  onClickPoint?: (lat: number, lon: number) => void;
}

/** WebGL is not universal: disabled by policy, blocked by drivers, absent in
 *  headless. Detect it up front so we can degrade instead of throwing. */
function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(
      canvas.getContext("webgl2") ??
        canvas.getContext("webgl") ??
        canvas.getContext("experimental-webgl"),
    );
  } catch {
    return false;
  }
}

export function RiskMap({
  segments,
  fastest,
  safest,
  showRisk = true,
  onMoveEnd,
  onClickPoint,
  className = "",
}: MapLayers & { className?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const ready = useRef(false);
  const [failed, setFailed] = useState(false);
  const moveCb = useRef(onMoveEnd);
  const clickCb = useRef(onClickPoint);
  moveCb.current = onMoveEnd;
  clickCb.current = onClickPoint;

  useEffect(() => {
    if (!container.current || map.current) return;

    // Degrade, never crash. The gauge, the recommendation and the evidence
    // panel are the analysis; the map illustrates it. Losing the illustration
    // must not lose the answer.
    if (!webglAvailable()) {
      setFailed(true);
      return;
    }

    let instance: MlMap;
    try {
      instance = new maplibregl.Map({
        container: container.current,
        style: {
          version: 8,
          sources: {},
          layers: [{ id: "bg", type: "background", paint: { "background-color": "#0B0E11" } }],
        },
        center: [7.4551, 9.0555],
        zoom: 10.4,
        attributionControl: false,
      } as maplibregl.MapOptions);
    } catch {
      setFailed(true);
      return;
    }

    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    instance.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution: "Roads © OpenStreetMap · Terrain © Copernicus · Water: DE Africa WOfS",
      }),
      "bottom-right",
    );

    instance.on("load", () => {
      instance.addSource("segments", { type: "geojson", data: EMPTY });
      instance.addSource("fastest", { type: "geojson", data: EMPTY });
      instance.addSource("safest", { type: "geojson", data: EMPTY });

      // The network itself, drawn faintly — this is the basemap.
      instance.addLayer({
        id: "segments-base",
        type: "line",
        source: "segments",
        paint: {
          "line-color": "#2b3541",
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.5, 14, 2.2],
        },
      });

      // Risk on top, data-driven from the same ramp the gauge uses.
      instance.addLayer({
        id: "segments-risk",
        type: "line",
        source: "segments",
        paint: {
          "line-color": RISK_PAINT_EXPRESSION as never,
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1.1, 14, 4.5],
          "line-opacity": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "risk"], 0],
            0, 0.25,
            40, 0.6,
            70, 0.95,
          ] as never,
        },
      });

      instance.addLayer({
        id: "route-fastest",
        type: "line",
        source: "fastest",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#cb6238", "line-width": 5, "line-opacity": 0.9 },
      });

      instance.addLayer({
        id: "route-safest",
        type: "line",
        source: "safest",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#59a197", "line-width": 5, "line-opacity": 0.95 },
      });

      ready.current = true;
      instance.fire("cp:ready");
    });

    instance.on("moveend", () => {
      const b = instance.getBounds();
      moveCb.current?.(
        `${b.getWest().toFixed(4)},${b.getSouth().toFixed(4)},${b.getEast().toFixed(4)},${b.getNorth().toFixed(4)}`,
        instance.getZoom(),
      );
    });

    instance.on("error", (e) => {
      // MapLibre surfaces context loss here; degrade rather than bubble.
      if (String((e as { error?: Error }).error?.message ?? "").includes("WebGL")) setFailed(true);
    });
    instance.on("click", (e) => clickCb.current?.(e.lngLat.lat, e.lngLat.lng));
    instance.getCanvas().style.cursor = "crosshair";

    map.current = instance;
    return () => {
      instance.remove();
      map.current = null;
      ready.current = false;
    };
  }, []);

  // --- data updates -------------------------------------------------
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const apply = () => {
      const src = instance.getSource("segments") as maplibregl.GeoJSONSource | undefined;
      src?.setData((segments as unknown as GeoJSON.FeatureCollection) ?? EMPTY);
    };
    if (ready.current) apply();
    else instance.once("cp:ready", apply);
  }, [segments]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const apply = () => {
      instance.setLayoutProperty("segments-risk", "visibility", showRisk ? "visible" : "none");
    };
    if (ready.current) apply();
    else instance.once("cp:ready", apply);
  }, [showRisk]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const apply = () => {
      const toFc = (leg?: RouteLeg | null): GeoJSON.FeatureCollection =>
        leg
          ? {
              type: "FeatureCollection",
              features: [{ type: "Feature", properties: {}, geometry: leg.geometry as never }],
            }
          : EMPTY;

      (instance.getSource("fastest") as maplibregl.GeoJSONSource | undefined)?.setData(toFc(fastest));
      (instance.getSource("safest") as maplibregl.GeoJSONSource | undefined)?.setData(toFc(safest));

      const coords = [
        ...(fastest?.geometry.coordinates ?? []),
        ...(safest?.geometry.coordinates ?? []),
      ];
      if (coords.length > 1) {
        const bounds = coords.reduce(
          (acc, c) => acc.extend(c as [number, number]),
          new maplibregl.LngLatBounds(coords[0] as [number, number], coords[0] as [number, number]),
        );
        instance.fitBounds(bounds, { padding: 70, duration: 700, maxZoom: 14 });
      }
    };
    if (ready.current) apply();
    else instance.once("cp:ready", apply);
  }, [fastest, safest]);

  if (failed) {
    return (
      <div
        className={`${className} flex items-center justify-center bg-tarmac-950 p-8`}
        role="status"
      >
        <div className="max-w-xs text-center">
          <div className="label mb-2">Map unavailable</div>
          <p className="text-xs leading-relaxed text-ash">
            This browser could not start WebGL, so the map cannot draw. Every number on
            this page is still accurate — only the illustration is missing.
          </p>
        </div>
      </div>
    );
  }

  return <div ref={container} className={className} />;
}
