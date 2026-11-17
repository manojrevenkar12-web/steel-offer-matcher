import type { Rfq } from "../../core/types";
import { fieldName, shortSender } from "../../shared/format";
import { pieces } from "./highlight";

export function EmailPane({ rfq, active, onActive }: { rfq: Rfq; active?: string; onActive: (k?: string) => void }) {
  const evidence = rfq.result?.extraction.evidence ?? {};
  const marks = Object.entries(evidence).map(([key, e]) => ({ key, quote: e.quote }));
  const fromBody = pieces(rfq.body, marks);
  const elsewhere = Object.entries(evidence).filter(([, e]) => !e.source.startsWith("email"));
  return (
    <article className="h-full overflow-y-auto bg-panel p-[var(--spacing-pane)]">
      <header className="border-b border-rule pb-3">
        <h1 className="font-display text-xl leading-tight">{rfq.subject}</h1>
        <p className="mt-1 text-sm text-steel">
          {shortSender(rfq.sender)}, received {rfq.received}
          {rfq.attachments.length > 0 && <> · attached {rfq.attachments.join(", ")}</>}
        </p>
      </header>
      <pre className="mt-4 whitespace-pre-wrap font-sans text-[15px] leading-7 text-ink">
        {fromBody.map((p, i) =>
          p.key ? (
            <mark
              key={i}
              tabIndex={0}
              onMouseEnter={() => onActive(p.key)}
              onMouseLeave={() => onActive(undefined)}
              onFocus={() => onActive(p.key)}
              onBlur={() => onActive(undefined)}
              title={fieldName[p.key.split(".").slice(1).join(".")] ?? fieldName[p.key] ?? p.key}
              className={`rounded-sm px-0.5 text-ink transition-colors ${active === p.key ? "bg-chalk" : "bg-chalk-soft"}`}
            >
              {p.text}
            </mark>
          ) : (
            <span key={i}>{p.text}</span>
          ),
        )}
      </pre>
      {elsewhere.length > 0 && (
        <section className="mt-6 border-t border-rule pt-3">
          <h2 className="text-sm font-medium text-steel">Read from attachments</h2>
          <ul className="mt-2 space-y-1.5">
            {elsewhere.map(([key, e]) => (
              <li key={key} className="text-sm" onMouseEnter={() => onActive(key)} onMouseLeave={() => onActive(undefined)}>
                <mark className={`rounded-sm px-1 ${active === key ? "bg-chalk" : "bg-chalk-soft"}`}>{e.quote}</mark>
                <span className="ml-2 text-xs text-steel">{e.source}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
