import { createRootRoute, createRoute, createRouter, createHashHistory, Link, Outlet } from "@tanstack/react-router";
import { mode } from "../core/api";
import { DeskPage } from "../features/desk/DeskPage";
import { EvaluationPage } from "../features/evaluation/EvaluationPage";
import { ModelsPage } from "../features/models/ModelsPage";
import { ToolsPage } from "../features/tools/ToolsPage";

const nav = [
  { to: "/", label: "Desk" },
  { to: "/evaluation", label: "Evaluation" },
  { to: "/models", label: "Models" },
  { to: "/tools", label: "Tools" },
] as const;

function Shell() {
  return (
    <div className="min-h-screen">
      <header className="flex h-14 items-center gap-8 border-b border-rule bg-panel px-5">
        <span className="font-display text-xl tracking-tight">Steel offer desk</span>
        <nav className="flex gap-1" aria-label="Sections">
          {nav.map((n) => (
            <Link key={n.to} to={n.to} activeOptions={{ exact: true }}
              className="rounded px-3 py-1.5 text-sm text-steel hover:text-ink [&.active]:bg-galvanised [&.active]:text-ink">
              {n.label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto text-xs text-steel">{mode === "demo" ? "Recorded simulated morning" : "Live desk"}</span>
      </header>
      <main><Outlet /></main>
    </div>
  );
}

const root = createRootRoute({ component: Shell });
const desk = createRoute({ getParentRoute: () => root, path: "/", component: DeskPage,
  validateSearch: (s: Record<string, unknown>) => ({ rfq: typeof s.rfq === "string" ? s.rfq : undefined }) });
const evaluation = createRoute({ getParentRoute: () => root, path: "/evaluation", component: EvaluationPage });
const models = createRoute({ getParentRoute: () => root, path: "/models", component: ModelsPage });
const tools = createRoute({ getParentRoute: () => root, path: "/tools", component: ToolsPage });

export const router = createRouter({ routeTree: root.addChildren([desk, evaluation, models, tools]), history: createHashHistory() });

declare module "@tanstack/react-router" {
  interface Register { router: typeof router }
}
