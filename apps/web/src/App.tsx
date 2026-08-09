import { Component, type ReactNode } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import Home from "./routes/Home";
import Results from "./routes/Results";
import Alerts from "./routes/Alerts";
import Model from "./routes/Model";

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto max-w-lg p-10 text-center">
        <div className="label mb-3">Interface error</div>
        <p className="mb-6 text-sm text-ash">
          This view stopped responding. Your data is unaffected — reloading will restore it.
        </p>
        <button className="btn-primary" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}

const NAV = [
  { to: "/", label: "Analyze", end: true },
  { to: "/alerts", label: "Corridor Watch", end: false },
  { to: "/model", label: "How it works", end: false },
];

export default function App() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 border-b border-tarmac-800 bg-tarmac-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-5 py-3.5">
          <NavLink to="/" className="group flex items-baseline gap-2.5">
            <span className="font-data text-sm font-medium tracking-widest text-bone">
              CLIMATEPASS
            </span>
            <span className="font-data text-[10px] uppercase tracking-widest text-laterite-400">
              Abuja FCT
            </span>
          </NavLink>

          <nav className="ml-auto flex items-center gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded px-3 py-1.5 font-data text-[11px] uppercase tracking-widest transition-colors ${
                    isActive
                      ? "bg-tarmac-800 text-bone"
                      : "text-silt hover:text-ash"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/results" element={<Results />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/model" element={<Model />} />
            <Route
              path="*"
              element={
                <div className="p-16 text-center">
                  <div className="label mb-2">No such page</div>
                  <NavLink to="/" className="text-sm text-signal underline">
                    Return to route analysis
                  </NavLink>
                </div>
              }
            />
          </Routes>
        </ErrorBoundary>
      </main>

      <footer className="border-t border-tarmac-800 px-5 py-4">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-5 gap-y-1.5 font-data text-[10px] uppercase tracking-widest text-silt">
          <span>Copernicus DEM GLO-30</span>
          <span className="text-tarmac-600">/</span>
          <span>DE Africa WOfS</span>
          <span className="text-tarmac-600">/</span>
          <span>OpenStreetMap</span>
          <NavLink
            to="/model"
            className="ml-auto text-silt underline decoration-tarmac-600 underline-offset-4 hover:text-ash"
          >
            How the score is computed
          </NavLink>
        </div>
      </footer>
    </div>
  );
}
