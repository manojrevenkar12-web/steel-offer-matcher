import type { ReactNode } from "react";

export function Skeleton({ lines = 6, label }: { lines?: number; label: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-3 p-[var(--spacing-pane)]">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-rule" style={{ width: `${92 - ((i * 17) % 40)}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="m-[var(--spacing-pane)] rounded-[var(--radius-panel)] border border-fail/40 bg-fail-soft p-4">
      <p className="text-sm text-ink">{message}</p>
      <button onClick={onRetry} className="mt-3 rounded bg-action px-3 py-1.5 text-sm font-medium text-action-ink">
        Try again
      </button>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="m-[var(--spacing-pane)] rounded-[var(--radius-panel)] border border-dashed border-rule-strong p-6 text-center">
      <p className="font-display text-lg text-ink">{title}</p>
      {children && <div className="mt-1 text-sm text-steel">{children}</div>}
    </div>
  );
}

export function Metric({ value, label, note }: { value: string; label: string; note?: string }) {
  return (
    <div className="border-l-2 border-rule-strong pl-3">
      <div className="font-display text-xl leading-tight text-ink">{value}</div>
      <div className="text-sm text-steel">{label}</div>
      {note && <div className="text-xs text-steel/80">{note}</div>}
    </div>
  );
}
