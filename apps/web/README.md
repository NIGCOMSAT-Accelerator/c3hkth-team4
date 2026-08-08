# apps/web — ClimatePass AI frontend

Scaffolded in **P8** (track C), starting hour 3 against Stage-A (v1) scoring data.

```bash
npm create vite@latest . -- --template react-ts
```

Stack: React + TypeScript + Vite + MapLibre GL JS (react-map-gl) + TanStack Query + Tailwind.

**Three routes only:** Home, Results, Alerts. No separate Map page — hazard layers live on the Results map.

API base URL is `http://localhost:8000` in dev. Use one typed API client; no scattered `fetch` calls.

Map layers must be GeoJSON sources with data-driven styling. Do **not** render 20,000 segments as React components.
