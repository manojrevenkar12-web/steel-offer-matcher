import { motion } from "motion/react";
import { useMemo, useState } from "react";
import type { Rfq } from "../../core/types";
import { shortSender } from "../../shared/format";
import { StatusMark } from "../../shared/StatusMark";
import { EmptyState } from "../../shared/States";
import { motionTokens } from "../../theme/tokens";

const filters = [
  { key: "all", label: "All" },
  { key: "review", label: "Needs a decision" },
  { key: "ready", label: "Ready" },
  { key: "not_rfq", label: "Not an enquiry" },
] as const;

export function Inbox({ rfqs, selected, onSelect }: { rfqs: Rfq[]; selected?: string; onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState<(typeof filters)[number]["key"]>("all");
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rfqs.length };
    for (const r of rfqs) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rfqs]);
  const shown = filter === "all" ? rfqs : rfqs.filter((r) => r.status === filter);
  return (
    <nav aria-label="Enquiries" className="flex h-full flex-col border-r border-rule bg-panel">
      <div className="flex flex-wrap gap-1.5 border-b border-rule p-3">
        {filters.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            aria-pressed={filter === f.key}
            className={`rounded-full px-2.5 py-1 text-xs ${filter === f.key ? "bg-ink text-panel" : "bg-galvanised text-steel hover:text-ink"}`}
          >
            {f.label} <span className="tabular-nums opacity-70">{counts[f.key] ?? 0}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <EmptyState title="Nothing here">No enquiries with this status in the current inbox.</EmptyState>
      ) : (
        <ul className="flex-1 overflow-y-auto">
          {shown.map((r, i) => {
            const lines = r.result?.lines.length ?? 0;
            const offers = r.result?.lines.reduce((n, l) => n + l.offers.length, 0) ?? 0;
            return (
              <motion.li
                key={r.message_id}
                initial={{ opacity: 0, y: motionTokens.rise }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...motionTokens.reveal, delay: Math.min(i, 12) * motionTokens.stagger }}
              >
                <button
                  onClick={() => onSelect(r.message_id)}
                  aria-current={selected === r.message_id}
                  className={`block w-full border-b border-rule px-4 py-3 text-left transition-colors ${
                    selected === r.message_id ? "bg-galvanised" : "hover:bg-galvanised/60"
                  }`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium">{shortSender(r.sender)}</span>
                    <span className="shrink-0 text-xs text-steel tabular-nums">{r.received.slice(5).replace("-", ".")}</span>
                  </div>
                  <div className="truncate text-sm text-steel">{r.subject}</div>
                  <div className="mt-1.5 flex items-center gap-2">
                    <StatusMark value={r.status} />
                    {lines > 0 && (
                      <span className="text-xs text-steel">
                        {lines} {lines === 1 ? "line" : "lines"}, {offers} {offers === 1 ? "offer" : "offers"}
                      </span>
                    )}
                  </div>
                </button>
              </motion.li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
