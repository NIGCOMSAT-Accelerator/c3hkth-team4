import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import type { Landmark, RouteLeg, SegmentCollection } from "@/api/client";
import { RISK_PAINT_EXPRESSION } from "@/lib/risk";

/**
 * The map has no basemap, and that is deliberate.
 *
 * Tile servers need network and usually an API key, which would break the
 * offline demo requirement. We already hold the road network as GeoJSON, so
 * the data draws the map: segments on a dark field, coloured by risk.
 *
 * LABELS. MapLibre's `text-field` needs a glyph server to render text, which
 * would put a network dependency straight back in. So place and street names
 * are an HTML overlay instead, projected onto the map on every move. Street
 * names come from our own road data and places from the bundled gazetteer, so
 * the map reads like a map with nothing fetched from anyone.
 *
 * Layers are GeoJSON sources with data-driven styling. Segments are never
 * React components — 20k of those would melt the browser.
 */

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

// Enough to orient, not so many that the map becomes a word search.
const MAX_PLACE_LABELS = 22;
const MAX_ROAD_LABELS = 16;

const ROAD_RANK: Record<string, number> = {
  motorway: 0, trunk: 1, primary: 2, secondary: 3, tertiary: 4, unclassified: 5,
};

interface ScreenLabel {
  key: string;
  text: string;
  x: number;
  y: number;
  kind: "place" | "road";
}

export interface MapLayers {
  segments?: SegmentCollection | null;
  fastest?: RouteLeg | null;
  safest?: RouteLeg | null;
  landmarks?: Landmark[];
  showRisk?: boolean;
  showLabels?: boolean;
  trackUser?: boolean;
  focus?: { lat: number; lon: number; zoom?: number } | null;
  origin?: { lat: number; lon: number; name?: string } | null;
  destination?: { lat: number; lon: number; name?: string } | null;
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
  landmarks = [],
  showRisk = true,
  showLabels = true,
  trackUser = false,
  focus = null,
  origin = null,
  destination = null,
  onMoveEnd,
  onClickPoint,
  className = "",
}: MapLayers & { className?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const ready = useRef(false);
  const userMarker = useRef<maplibregl.Marker | null>(null);
  const endpointMarkers = useRef<maplibregl.Marker[]>([]);
  const watchId = useRef<number | null>(null);

  const [failed, setFailed] = useState(false);
  const [labels, setLabels] = useState<ScreenLabel[]>([]);
  const [userPos, setUserPos] = useState<{ lat: number; lon: number; accuracy: number } | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);

  const moveCb = useRef(onMoveEnd);
  const clickCb = useRef(onClickPoint);
  moveCb.current = onMoveEnd;
  clickCb.current = onClickPoint;

  /** One label per named road, most important road first. */
  const roadAnchors = useMemo(() => {
    if (!segments?.features?.length) return [] as { name: string; lon: number; lat: number; rank: number }[];
    const seen = new Map<string, { lon: number; lat: number; rank: number }>();
    for (const f of segments.features) {
      const name = f.properties.name;
      if (!name) continue;
      const rank = ROAD_RANK[f.properties.highway_class ?? ""] ?? 9;
      const existing = seen.get(name);
      if (existing && existing.rank <= rank) continue;
      const coords = f.geometry.coordinates;
      const mid = coords[Math.floor(coords.length / 2)];
      if (mid) seen.set(name, { lon: mid[0], lat: mid[1], rank });
    }
    return [...seen.entries()]
      .sort((a, b) => a[1].rank - b[1].rank)
      .slice(0, MAX_ROAD_LABELS)
      .map(([name, p]) => ({ name, ...p }));
  }, [segments]);

  const hasFitted = useRef(false);
  const originRef = useRef(origin);
  const destinationRef = useRef(destination);
  originRef.current = origin;
  destinationRef.current = destination;

  const anchorsRef = useRef(roadAnchors);
  anchorsRef.current = roadAnchors;
  const landmarksRef = useRef(landmarks);
  landmarksRef.current = landmarks;
  const showLabelsRef = useRef(showLabels);
  showLabelsRef.current = showLabels;

  /** Project anchors into screen space. Cheap enough to run on every move. */
  const recomputeLabels = useCallback(() => {
    const instance = map.current;
    if (!instance || !showLabelsRef.current) {
      setLabels([]);
      return;
    }
    const { clientWidth: w, clientHeight: h } = instance.getContainer();
    // Before layout settles the container reports 0x0, and every label would
    // fail the bounds test below and disappear without a trace. Bail and wait
    // for the next event rather than publishing an empty set.
    if (w === 0 || h === 0) return;
    const zoom = instance.getZoom();
    const out: ScreenLabel[] = [];
    const placed: { x: number; y: number }[] = [];

    // Collision avoidance: drop anything landing on a label already placed.
    const fits = (x: number, y: number) =>
      placed.every((p) => Math.abs(p.x - x) > 78 || Math.abs(p.y - y) > 16);

    // Endpoint pins carry their own captions. Seed the collision list with
    // them so the gazetteer does not label the same place twice — "A GWARINPA"
    // sitting on top of "GWARINPA" reads as a rendering bug, not a map.
    for (const end of [originRef.current, destinationRef.current]) {
      if (!end) continue;
      const p = instance.project([end.lon, end.lat]);
      placed.push({ x: p.x, y: p.y });
    }

    for (const lm of landmarksRef.current.slice(0, MAX_PLACE_LABELS)) {
      const p = instance.project([lm.lon, lm.lat]);
      if (p.x < 6 || p.y < 6 || p.x > w - 6 || p.y > h - 6) continue;
      if (!fits(p.x, p.y)) continue;
      placed.push({ x: p.x, y: p.y });
      out.push({ key: `p:${lm.name}`, text: lm.name, x: p.x, y: p.y, kind: "place" });
    }

    // Street names only once you are close enough for them to mean anything.
    if (zoom >= 11.5) {
      for (const road of anchorsRef.current) {
        const p = instance.project([road.lon, road.lat]);
        if (p.x < 6 || p.y < 6 || p.x > w - 6 || p.y > h - 6) continue;
        if (!fits(p.x, p.y)) continue;
        placed.push({ x: p.x, y: p.y });
        out.push({ key: `r:${road.name}`, text: road.name, x: p.x, y: p.y, kind: "road" });
      }
    }
    setLabels(out);
  }, []);

  // ------------------------------------------------------------- init
  useEffect(() => {
    if (!container.current || map.current) return;

    // Degrade, never crash. The gauge and the evidence are the analysis; the
    // map only illustrates it. Losing the illustration must not lose the answer.
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

      instance.addLayer({
        id: "segments-risk",
        type: "line",
        source: "segments",
        paint: {
          "line-color": RISK_PAINT_EXPRESSION as never,
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1.1, 14, 4.5],
          "line-opacity": [
            "interpolate", ["linear"], ["coalesce", ["get", "risk"], 0],
            0, 0.25, 40, 0.6, 70, 0.95,
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
      recomputeLabels();
      instance.fire("cp:ready");
    });

    instance.on("move", recomputeLabels);
    instance.on("idle", recomputeLabels);
    instance.on("resize", recomputeLabels);
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
    // Belt and braces: if the container was still sizing when the first
    // events fired, catch up shortly after mount.
    const settle = window.setTimeout(recomputeLabels, 400);
    return () => {
      window.clearTimeout(settle);
      instance.remove();
      map.current = null;
      ready.current = false;
    };
  }, [recomputeLabels]);

  // -------------------------------------------------------- data updates
  const whenReady = (fn: () => void) => {
    const instance = map.current;
    if (!instance) return;
    if (ready.current) fn();
    else instance.once("cp:ready", fn);
  };

  useEffect(() => {
    whenReady(() => {
      const src = map.current?.getSource("segments") as maplibregl.GeoJSONSource | undefined;
      src?.setData((segments as unknown as GeoJSON.FeatureCollection) ?? EMPTY);
      recomputeLabels();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments]);

  useEffect(() => {
    whenReady(() =>
      map.current?.setLayoutProperty("segments-risk", "visibility", showRisk ? "visible" : "none"),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showRisk]);

  useEffect(() => {
    recomputeLabels();
  }, [showLabels, landmarks, roadAnchors, recomputeLabels]);

  useEffect(() => {
    whenReady(() => {
      const instance = map.current!;
      const toFc = (leg?: RouteLeg | null): GeoJSON.FeatureCollection =>
        leg
          ? {
              type: "FeatureCollection",
              features: [{ type: "Feature", properties: {}, geometry: leg.geometry as never }],
            }
          : EMPTY;

      (instance.getSource("fastest") as maplibregl.GeoJSONSource | undefined)?.setData(toFc(fastest));
      (instance.getSource("safest") as maplibregl.GeoJSONSource | undefined)?.setData(toFc(safest));

      // Include the A/B markers in the fit, not just the route geometry.
      // A route starts and ends at the nearest graph NODE, which can sit a few
      // hundred metres from the point the user actually asked about — so
      // fitting the line alone can push an endpoint marker off screen.
      const coords: [number, number][] = [
        ...((fastest?.geometry.coordinates ?? []) as [number, number][]),
        ...((safest?.geometry.coordinates ?? []) as [number, number][]),
        ...(originRef.current ? ([[originRef.current.lon, originRef.current.lat]] as [number, number][]) : []),
        ...(destinationRef.current
          ? ([[destinationRef.current.lon, destinationRef.current.lat]] as [number, number][])
          : []),
      ];
      if (coords.length > 1) {
        const bounds = coords.reduce(
          (acc, c) => acc.extend(c as [number, number]),
          new maplibregl.LngLatBounds(coords[0] as [number, number], coords[0] as [number, number]),
        );
        // The first fit is instant. Nobody wants to watch the map fly on page
        // load, and an animated initial fit also means the view is wrong for
        // 700ms — long enough for a screenshot, or a slow frame, to catch an
        // endpoint marker still outside the viewport.
        const reduced =
          typeof window !== "undefined" &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        instance.fitBounds(bounds, {
          // Asymmetric: the endpoint captions extend to the right of their pin,
          // and the layers panel occupies the top-left.
          padding: { top: 80, bottom: 60, left: 70, right: 120 },
          duration: hasFitted.current && !reduced ? 700 : 0,
          maxZoom: 14,
        });
        hasFitted.current = true;
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fastest, safest]);

  // -------------------------------------------------------------- focus
  useEffect(() => {
    if (!focus) return;
    whenReady(() =>
      map.current?.flyTo({
        center: [focus.lon, focus.lat],
        zoom: focus.zoom ?? 13,
        duration: 900,
        essential: true,
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.lat, focus?.lon, focus?.zoom]);

  // --------------------------------------------------- A / B endpoints
  useEffect(() => {
    whenReady(() => {
      const instance = map.current;
      if (!instance) return;

      for (const marker of endpointMarkers.current) marker.remove();
      endpointMarkers.current = [];

      const build = (
        point: { lat: number; lon: number; name?: string },
        letter: "A" | "B",
      ) => {
        const el = document.createElement("div");
        // Filled origin, hollow destination — the old cartographic convention,
        // and it reads at a glance without relying on colour, which the route
        // lines have already spent.
        el.className = `cp-endpoint cp-endpoint--${letter.toLowerCase()}`;

        const pin = document.createElement("span");
        pin.className = "cp-endpoint__pin";
        pin.textContent = letter;
        el.appendChild(pin);

        if (point.name) {
          const name = document.createElement("span");
          name.className = "cp-endpoint__name";
          name.textContent = point.name;
          el.appendChild(name);
        }

        const marker = new maplibregl.Marker({ element: el, anchor: "center" })
          .setLngLat([point.lon, point.lat])
          .addTo(instance);

        // MapLibre stamps its own aria-label="Map marker" onto the element
        // during construction, which tells a screen-reader user nothing.
        // Overwrite it afterwards with something that names the place.
        marker
          .getElement()
          .setAttribute(
            "aria-label",
            `${letter === "A" ? "Start" : "Destination"}: ${point.name ?? "selected point"}`,
          );
        return marker;
      };

      if (origin) endpointMarkers.current.push(build(origin, "A"));
      if (destination) endpointMarkers.current.push(build(destination, "B"));
    });

    return () => {
      for (const marker of endpointMarkers.current) marker.remove();
      endpointMarkers.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin?.lat, origin?.lon, origin?.name, destination?.lat, destination?.lon, destination?.name]);

  // ------------------------------------------------------ user location
  useEffect(() => {
    if (!trackUser) return;
    if (!("geolocation" in navigator)) {
      setGeoError("This browser does not provide location.");
      return;
    }
    watchId.current = navigator.geolocation.watchPosition(
      (pos) => {
        setGeoError(null);
        setUserPos({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        });
      },
      (err) => {
        setGeoError(
          err.code === err.PERMISSION_DENIED
            ? "Location permission denied. The map still works; you just will not see yourself on it."
            : "Could not get your location. Check that location services are on.",
        );
      },
      { enableHighAccuracy: true, maximumAge: 15_000, timeout: 15_000 },
    );
    return () => {
      if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
    };
  }, [trackUser]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !userPos) return;
    if (!userMarker.current) {
      const el = document.createElement("div");
      el.className = "cp-user-dot";
      el.setAttribute("aria-label", "Your location");
      userMarker.current = new maplibregl.Marker({ element: el });
    }
    userMarker.current.setLngLat([userPos.lon, userPos.lat]).addTo(instance);
  }, [userPos]);

  const recenterOnUser = () => {
    if (userPos && map.current) {
      map.current.flyTo({ center: [userPos.lon, userPos.lat], zoom: 14, duration: 800 });
    }
  };

  // Outside the Abuja municipal AOI we have no data, and saying so is kinder
  // than showing someone a dot in a city we do not cover.
  const userOutsideAoi =
    userPos !== null &&
    (userPos.lon < 7.25 || userPos.lon > 7.62 || userPos.lat < 8.9 || userPos.lat > 9.22);

  if (failed) {
    return (
      <div className={`${className} flex items-center justify-center bg-tarmac-950 p-8`} role="status">
        <div className="max-w-xs text-center">
          <div className="label mb-2">Map unavailable</div>
          <p className="text-xs leading-relaxed text-ash">
            This browser could not start WebGL, so the map cannot draw. Every number on this
            page is still accurate — only the illustration is missing.
          </p>
        </div>
      </div>
    );
  }

  // No `relative` here. Callers pass `absolute inset-0`, and Tailwind emits
  // .relative after .absolute, so adding it would win the cascade, strip the
  // wrapper of its inset-derived size, collapse it to zero height, and take
  // both the canvas and every projected label down with it.
  return (
    <div className={className}>
      <div ref={container} className="absolute inset-0" />

      {/* Labels as an HTML overlay: no glyph server, therefore no network. */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {labels.map((l) => (
          <span
            key={l.key}
            className={
              l.kind === "place"
                ? "cp-label absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap font-data text-[10px] uppercase tracking-widest text-bone/85"
                : "cp-label absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap font-ui text-[10.5px] text-ash/80"
            }
            style={{ left: l.x, top: l.y }}
          >
            {l.text}
          </span>
        ))}
      </div>

      {trackUser && (
        <div className="absolute bottom-3 left-3 z-10 flex max-w-[16rem] flex-col items-start gap-2">
          {userPos && (
            <button
              type="button"
              onClick={recenterOnUser}
              className="panel px-3 py-2 font-data text-[10px] uppercase tracking-widest text-ash transition-colors hover:text-bone"
            >
              ◎ My location
              <span className="ml-2 text-silt">±{Math.round(userPos.accuracy)} m</span>
            </button>
          )}
          {(geoError || userOutsideAoi) && (
            <div className="panel px-3 py-2 text-[11px] leading-relaxed text-ash">
              {geoError ??
                "You are outside the Abuja area this build covers, so your position shows but no risk applies to it."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
