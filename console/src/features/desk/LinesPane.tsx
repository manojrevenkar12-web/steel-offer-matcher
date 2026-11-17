import { useState } from "react";
import type { Line, Rfq } from "../../core/types";
import { dims, eur, fieldName, num } from "../../shared/format";
import { EmptyState } from "../../shared/States";
import { CoilTag } from "./CoilTag";

const stopWords: Record<string, string> = {
  proposal: "it stopped once it had its picks",
  "no new results": "it stopped when searches found nothing new",
  budget: "it stopped at its request budget",
  "model unavailable": "the model was unavailable, so only the literal search ran",
};

export function LinesPane({ rfq, active, onActive }: { rfq: Rfq; active?: string; onActive: (k?: string) => void }) {
  const r = rfq.result;
  if (!r) return <EmptyState title="Not processed yet">The worker has not picked this email up.</EmptyState>;
  if (r.status === "not_rfq")
    return <EmptyState title="Not an enquiry">The extractor read this as an order confirmation, invoice or notice. Nothing to quote.</EmptyState>;
  const review = r.extraction.review;
  return (
    <section aria-label="Lines and offers" className="h-full overflow-y-auto border-l border-rule p-[var(--spacing-pane)]">
      {review.length > 0 && (
        <div className="mb-5 rounded-[var(--radius-panel)] border border-review/40 bg-review-soft p-3">
          <h2 className="text-sm font-medium text-review">Open points for you</h2>
          <ul className="mt-1.5 space-y-1 text-sm">
            {review.map((it, i) => (
              <li key={i}>
                <span className="font-medium">{it.line ? `Line ${it.line}, ` : ""}{fieldName[it.field] ?? it.field}:</span> {it.reason}
                {it.proposals.length > 0 && <span className="text-steel"> Possible: {it.proposals.join(", ")}.</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.lines.length === 0 && review.length > 0 && (
        <EmptyState title="No line could be read with certainty">Fix the open points and the lines will be matched again.</EmptyState>
      )}
      <ol className="space-y-8">
        {r.lines.map((l) => (
          <LineBlock key={l.spec.line} line={l} evidence={r.extraction.evidence} active={active} onActive={onActive} />
        ))}
      </ol>
      <footer className="mt-8 border-t border-rule pt-3 text-xs text-steel">
        {r.model_calls} model calls, {eur(r.cost_eur)} · prompts {Object.values(r.prompt_versions).join(", ")}
      </footer>
    </section>
  );
}

function LineBlock({ line, evidence, active, onActive }: {
  line: Line;
  evidence: Record<string, { quote: string; source: string }>;
  active?: string;
  onActive: (k?: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const s = line.spec;
  const n = s.line;
  const facts: [string, string][] = [
    [`${n}.grade`, s.grade_id + (s.grade_text && s.grade_text !== s.grade_id ? ` (written ${s.grade_text})` : "")],
    [`${n}.thickness_mm`, `${num(s.thickness_mm)} mm`],
    [`${n}.width_mm`, `${num(s.width_mm)} mm`],
    ...(s.length_mm ? [[`${n}.length_mm`, `${num(s.length_mm)} mm`] as [string, string]] : []),
    [`${n}.quantity_t`, `${num(s.quantity_t)} t`],
    ...(s.max_price_eur_t ? [[`${n}.max_price_eur_t`, `${eur(s.max_price_eur_t)}/t`] as [string, string]] : []),
  ];
  const searches = line.steps.filter((x) => x.tool === "search_inventory").length;
  const offers = line.offers.length ? line.offers : line.review_offers;
  return (
    <li>
      <h2 className="font-display text-lg leading-tight">
        Line {n}: {s.form}, {s.finish.replace("_", " ")} · {dims(s.thickness_mm, s.width_mm, s.length_mm)}
      </h2>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
        {facts.map(([key, value]) => (
          <div
            key={key}
            className={`contents ${evidence[key] ? "cursor-help" : ""}`}
            onMouseEnter={() => evidence[key] && onActive(key)}
            onMouseLeave={() => onActive(undefined)}
          >
            <dt className="text-steel">{fieldName[key.split(".")[1]]}</dt>
            <dd className={`rounded-sm px-1 ${active === key ? "bg-chalk" : ""}`}>{value}</dd>
          </div>
        ))}
      </dl>
      {line.reasons.length > 0 && <p className="mt-2 text-sm text-review">{line.reasons.join(" ")}</p>}
      {offers.length === 0 ? (
        <p className="mt-3 text-sm text-steel">No lot on the current lists fits this line. The draft tells the buyer so.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {offers.map((o, i) => (
            <CoilTag key={o.item_id} offer={o} rank={i + 1} found={line.agent_found.includes(o.item_id)} />
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-steel">
        {searches > 0
          ? `The agent ran ${searches} extra ${searches === 1 ? "search" : "searches"}; ${stopWords[line.stop_reason] ?? line.stop_reason}.`
          : line.stop_reason === "no agent" ? "Literal search only." : `The agent added no search; ${stopWords[line.stop_reason] ?? line.stop_reason}.`}
      </p>
      <details className="mt-3 rounded-[var(--radius-panel)] border border-rule bg-panel">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Draft reply</summary>
        <pre className="whitespace-pre-wrap px-3 pb-3 font-sans text-sm leading-6">{line.text}</pre>
        <button
          onClick={() => navigator.clipboard?.writeText(line.text).then(() => setCopied(true))}
          className="mx-3 mb-3 rounded bg-action px-3 py-1.5 text-sm font-medium text-action-ink"
        >
          {copied ? "Copied" : "Copy draft"}
        </button>
      </details>
    </li>
  );
}
