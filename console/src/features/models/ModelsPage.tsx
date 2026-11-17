import { useDesk } from "../../core/api";
import { eur, num } from "../../shared/format";
import { EmptyState, ErrorState, Metric, Skeleton } from "../../shared/States";

export function ModelsPage() {
  const desk = useDesk();
  if (desk.isPending) return <Skeleton label="Loading model results" lines={10} />;
  if (desk.isError) return <ErrorState message={desk.error.message} onRetry={() => desk.refetch()} />;
  const { bakeoff, gate } = desk.data.reports;
  const gw = Object.entries(desk.data.gateway);
  return (
    <div className="mx-auto max-w-6xl space-y-10 p-6">
      <section>
        <h1 className="font-display text-2xl leading-tight">Which model reads the enquiries</h1>
        <p className="mt-2 max-w-3xl text-steel">
          The same 160 enquiries, read by each extraction model behind the LiteLLM gateway. Code decides every value from the quoted
          text, so a weaker model costs review time, not wrong values. {bakeoff?.note}
        </p>
        {bakeoff ? (
          <div className="mt-4 overflow-x-auto rounded-[var(--radius-panel)] border border-rule bg-panel">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-rule text-left text-steel">
                <th className="p-3 font-medium">Model</th><th className="p-3 font-medium">Lines decided exactly</th>
                <th className="p-3 font-medium">Values decided wrongly</th><th className="p-3 font-medium">Model's own numbers wrong</th>
                <th className="p-3 font-medium">To review</th><th className="p-3 font-medium">Cost per enquiry</th><th className="p-3 font-medium">p50 / p95</th>
              </tr></thead>
              <tbody>
                {Object.entries(bakeoff.models).map(([name, m]) => (
                  <tr key={name} className="border-b border-rule last:border-0 tabular-nums">
                    <td className="p-3 font-medium">{name}</td>
                    <td className="p-3">{m.lines_decided_exactly} of {m.lines}</td>
                    <td className="p-3">{m.lines_decided_wrongly}</td>
                    <td className="p-3">{m.raw_model_lines_wrong}</td>
                    <td className="p-3">{m.rfqs_sent_to_review} of {m.rfqs}</td>
                    <td className="p-3">{eur(m.cost_per_rfq_eur)}</td>
                    <td className="p-3">{num(m.p50_ms / 1000)} s / {num(m.p95_ms / 1000)} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No bake-off yet">Run <code>make bakeoff</code>.</EmptyState>}
      </section>
      <section>
        <h2 className="font-display text-xl">The evaluation gate</h2>
        <p className="mt-1 max-w-3xl text-sm text-steel">
          Built from emails traders corrected. A prompt, model or rule change ships only if no case gets worse than the accepted baseline.
        </p>
        {gate ? (
          <div className="mt-4 grid grid-cols-2 gap-6 md:grid-cols-4">
            <Metric value={gate.passed ? "Passing" : "Failing"} label="current baseline" />
            <Metric value={`${gate.exact} of ${gate.cases}`} label="cases decided exactly" />
            <Metric value={num(gate.wrong_values)} label="values decided wrongly" />
            <Metric value={num(gate.regressions.length)} label="regressions" />
          </div>
        ) : <EmptyState title="No baseline yet">Run <code>make gate-accept</code>.</EmptyState>}
      </section>
      <section>
        <h2 className="font-display text-xl">Gateway traffic in this inbox</h2>
        {gw.length === 0 ? <EmptyState title="No calls recorded" /> : (
          <ul className="mt-3 grid gap-3 md:grid-cols-2">
            {gw.map(([model, s]) => (
              <li key={model} className="rounded-[var(--radius-panel)] border border-rule bg-panel p-4">
                <div className="font-medium">{model}</div>
                <div className="mt-1 text-sm text-steel tabular-nums">
                  {s.calls} calls · {s.fallbacks} served as fallback · {eur(s.cost_eur)} · p50 {num(s.p50_ms / 1000)} s
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
