import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import type { Offer } from "../../core/types";
import { dims, eur, num } from "../../shared/format";
import { motionTokens } from "../../theme/tokens";

const edge: Record<string, string> = { pass: "bg-pass", review: "bg-review", fail: "bg-fail" };
const mark: Record<string, string> = { pass: "text-pass", review: "text-review", fail: "text-fail" };
const glyph: Record<string, string> = { pass: "✓", review: "?", fail: "✕" };

/** An offer drawn like the label on a coil: dimensions first, then grade, weight and the landed price. */
export function CoilTag({ offer, rank, found }: { offer: Offer; rank: number; found: boolean }) {
  const [open, setOpen] = useState(false);
  const it = offer.item;
  if (!it) return null;
  return (
    <motion.li layout className="relative overflow-hidden rounded-[var(--radius-tag)] border border-rule bg-panel">
      <span aria-hidden className={`absolute inset-y-0 left-0 w-1 ${edge[offer.status]}`} />
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="block w-full py-3 pl-8 pr-4 text-left">
        <span aria-hidden className="absolute left-3.5 top-3.5 size-2.5 rounded-full border border-rule-strong bg-galvanised" />
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-display text-xl leading-none tracking-tight">{dims(it.thickness_mm, it.width_mm, it.length_mm)}</span>
          <span className="font-display text-xl leading-none">{eur(offer.landed_eur_t)}<span className="text-sm text-steel">/t</span></span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="font-medium">{it.grade_id ?? it.grade_text}</span>
          {it.grade_id && it.grade_text !== it.grade_id && <span className="text-steel">listed as {it.grade_text}</span>}
          <span className="text-steel">{num(offer.usable_t)} t usable</span>
          <span className="text-steel">from {it.country}</span>
          <span className="text-steel">{offer.lead_days} days</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className="text-xs text-steel">Option {rank}</span>
          {offer.processing.map((p) => (
            <span key={p} className="rounded bg-galvanised px-1.5 py-0.5 text-xs text-steel">{p}</span>
          ))}
          {offer.grade_match === "upgrade" && <span className="rounded bg-review-soft px-1.5 py-0.5 text-xs text-review">higher grade</span>}
          {offer.partial && <span className="rounded bg-review-soft px-1.5 py-0.5 text-xs text-review">part of the quantity</span>}
          {found && <span className="rounded bg-chalk-soft px-1.5 py-0.5 text-xs text-ink">found by the agent</span>}
        </div>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ul
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={motionTokens.quick}
            className="border-t border-rule bg-galvanised/50 px-8 text-sm"
          >
            {offer.checks.map((c) => (
              <li key={c.name} className="flex gap-3 border-b border-rule/70 py-1.5 last:border-0">
                <span aria-label={c.status} className={`w-4 text-center font-medium ${mark[c.status]}`}>{glyph[c.status]}</span>
                <span className="w-24 shrink-0 capitalize text-steel">{c.name}</span>
                <span>{c.detail}</span>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </motion.li>
  );
}
