export interface Mark { key: string; quote: string }
export type Piece = { text: string; key?: string };

/** Splits the email body into plain text and marked quotes. Overlapping quotes keep the first one found. */
export function pieces(body: string, marks: Mark[]): Piece[] {
  const spans: { start: number; end: number; key: string }[] = [];
  const lower = body.toLowerCase();
  for (const m of marks) {
    const q = m.quote.trim().toLowerCase();
    if (!q) continue;
    const at = lower.indexOf(q);
    if (at < 0) continue;
    const end = at + q.length;
    if (spans.some((s) => at < s.end && end > s.start)) continue;
    spans.push({ start: at, end, key: m.key });
  }
  spans.sort((a, b) => a.start - b.start);
  const out: Piece[] = [];
  let pos = 0;
  for (const s of spans) {
    if (s.start > pos) out.push({ text: body.slice(pos, s.start) });
    out.push({ text: body.slice(s.start, s.end), key: s.key });
    pos = s.end;
  }
  if (pos < body.length) out.push({ text: body.slice(pos) });
  return out;
}
