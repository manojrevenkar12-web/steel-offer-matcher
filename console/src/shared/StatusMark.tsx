import { status, type StatusKey } from "../theme/tokens";

const tone: Record<string, string> = {
  pass: "bg-pass-soft text-pass",
  review: "bg-review-soft text-review",
  fail: "bg-fail-soft text-fail",
  steel: "bg-galvanised text-steel",
};

export function StatusMark({ value }: { value: string }) {
  const s = status[value as StatusKey] ?? { label: value, tone: "steel" };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${tone[s.tone]}`}>
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {s.label}
    </span>
  );
}
