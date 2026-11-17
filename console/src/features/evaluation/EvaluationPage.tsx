import { motion } from "motion/react";
import { useDesk } from "../../core/api";
import type { DesignKey, Metrics } from "../../core/types";
import { eur, mean, num } from "../../shared/format";
import { EmptyState, ErrorState, Metric, Skeleton } from "../../shared/States";
import { motionTokens } from "../../theme/tokens";

const designs: { key: DesignKey; name: string; what: string }[] = [
  { key: "manual", name: "Trader by hand", what: "reads every list and email, searches literally" },
  { key: "model_decides", name: "Model decides", what: "a model reads and picks offers, they go out" },
  { key: "platform", name: "This platform", what: "the model reads and quotes, code decides, people review" },
  { key: "platform_no_agent", name: "Platform, no agent", what: "the same without the matching agent" },
];

const rows: { key: string; label: string; fmt: (x: number) => string; better: "high" | "low" }[] = [
  { key: "coverage_pct", label: "Lines with stock that got a valid offer", fmt: (x) => `${num(x)} %`, better: "high" },
  { key: "wrong_offers", label: "Offers the buyer would reject", fmt: mean, better: "low" },
  { key: "expected_claims_eur", label: "Expected claim cost", fmt: eur, better: "low" },
  { key: "above_best_eur_per_t", label: "Above the best landed price, per tonne", fmt: (x) => `${eur(x)}/t`, better: "low" },
  { key: "trader_minutes_per_rfq", label: "Trader minutes per enquiry", fmt: mean, better: "low" },
  { key: "response_median_h", label: "Median time to answer, hours", fmt: num, better: "low" },
  { key: "response_p90_h", label: "Slowest tenth answered within, hours", fmt: num, better: "low" },
  { key: "review_rate_pct", label: "Enquiries with a decision for a person", fmt: (x) => `${num(x)} %`, better: "low" },
  { key: "model_cost_per_rfq_eur", label: "Model cost per enquiry", fmt: eur, better: "low" },
  { key: "offers_found_only_by_agent", label: "Offers only the agent found", fmt: mean, better: "high" },
];

function Table({ mean }: { mean: Record<DesignKey, Metrics> }) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-panel)] border border-rule bg-panel">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-rule text-left">
            <th className="p-3 font-medium text-steel">Per month</th>
            {designs.map((d) => (
              <th key={d.key} className={`p-3 align-bottom font-medium ${d.key === "platform" ? "bg-galvanised" : ""}`}>
                <div>{d.name}</div>
                <div className="text-xs font-normal text-steel">{d.what}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <motion.tr key={r.key} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...motionTokens.reveal, delay: i * motionTokens.stagger }}
              className="border-b border-rule last:border-0">
              <td className="p-3 text-steel">{r.label}</td>
              {designs.map((d) => (
                <td key={d.key} className={`p-3 tabular-nums ${d.key === "platform" ? "bg-galvanised font-medium" : ""}`}>
                  {mean[d.key]?.[r.key] === undefined ? "–" : r.fmt(mean[d.key][r.key])}
                </td>
              ))}
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EvaluationPage() {
  const desk = useDesk();
  if (desk.isPending) return <Skeleton label="Loading results" lines={12} />;
  if (desk.isError) return <ErrorState message={desk.error.message} onRetry={() => desk.refetch()} />;
  const { dev, heldout } = desk.data.reports;
  if (!dev) return <EmptyState title="No evaluation yet">Run <code>make evaluate</code> to compare the designs on simulated months.</EmptyState>;
  const p = dev.mean.platform;
  const m = dev.mean.manual;
  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6">
      <header className="max-w-3xl">
        <h1 className="font-display text-2xl leading-tight">The same simulated months, four ways of running the desk</h1>
        <p className="mt-2 text-steel">
          Means over dev seeds {dev.seeds.join(", ")}. Every design sees the same emails, stock lists and model mistakes. Buyers,
          suppliers and the models' errors are simulated; what a buyer accepts is decided by the world, not by the platform's rules.
        </p>
      </header>
      <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
        <Metric value={`${num(p.coverage_pct)} %`} label="lines with stock answered correctly" note={`by hand ${num(m.coverage_pct)} %`} />
        <Metric value={mean(p.wrong_offers)} label="wrong offers a month" note={`by hand ${mean(m.wrong_offers)}`} />
        <Metric value={`${mean(p.trader_minutes_per_rfq)} min`} label="trader time per enquiry" note={`by hand ${mean(m.trader_minutes_per_rfq)} min`} />
        <Metric value={eur(p.model_cost_per_rfq_eur)} label="model cost per enquiry" />
      </div>
      <Table mean={dev.mean} />
      {heldout ? (
        <section>
          <h2 className="font-display text-xl">Held-out months, run once</h2>
          <p className="mb-3 mt-1 text-sm text-steel">Seeds {heldout.seeds.join(", ")}, with a supplier layout and designations the dev months never had.</p>
          <Table mean={heldout.mean} />
        </section>
      ) : (
        <EmptyState title="Held-out months not run yet">They run once, after the platform is locked.</EmptyState>
      )}
    </div>
  );
}
