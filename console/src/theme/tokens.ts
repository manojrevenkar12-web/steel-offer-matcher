// Motion and layout constants used from TypeScript. Colours live in tokens.css.
export const motionTokens = {
  reveal: { duration: 0.42, ease: [0.22, 1, 0.36, 1] as const },
  quick: { duration: 0.18, ease: [0.4, 0, 0.2, 1] as const },
  stagger: 0.035,
  rise: 10,
} as const;

export const status = {
  pass: { label: "Offer", tone: "pass" },
  review: { label: "Needs a decision", tone: "review" },
  fail: { label: "Does not fit", tone: "fail" },
  ready: { label: "Ready to send", tone: "pass" },
  not_rfq: { label: "Not an enquiry", tone: "steel" },
  sent: { label: "Sent", tone: "pass" },
  queued: { label: "Queued", tone: "steel" },
  failed: { label: "Failed", tone: "fail" },
} as const;

export type StatusKey = keyof typeof status;
