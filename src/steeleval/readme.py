"""Writes the results block of README.md from reports/. Never edit the block by hand."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START, END = "<!-- results:start -->", "<!-- results:end -->"
NAMES = {"manual": "Trader by hand", "model_decides": "Model decides", "platform": "**This platform**",
         "platform_no_agent": "Platform, no agent"}
ORDER = ["manual", "model_decides", "platform", "platform_no_agent"]


def _n(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".rstrip("0").rstrip(".") if d else f"{x:,.0f}"


def _eur(x: float) -> str:
    return f"€{x:,.4f}" if 0 < abs(x) < 1 else f"€{x:,.0f}"


ROWS = [
    ("coverage_pct", "Lines with stock that got a valid offer", lambda x: f"{_n(x)}%"),
    ("missed_with_stock", "Lines with stock left without a valid offer", _n),
    ("wrong_offers", "Offers the buyer would reject", _n),
    ("expected_claims_eur", "Expected claim cost (35% of wrong offers ordered, €2,400 each)", _eur),
    ("above_best_eur_per_t", "Above the best landed price, € per tonne covered", lambda x: f"€{x:,.2f}"),
    ("trader_minutes_per_rfq", "Trader minutes per enquiry", _n),
    ("response_median_h", "Median time to answer (business hours)", lambda x: _n(x, 2)),
    ("response_p90_h", "Slowest tenth answered within (business hours)", lambda x: _n(x, 2)),
    ("review_rate_pct", "Enquiries with a decision for a person", lambda x: f"{_n(x)}%"),
    ("model_cost_per_rfq_eur", "Model cost per enquiry", _eur),
    ("model_seconds_p95", "Model time per enquiry, p95 (s)", _n),
    ("offers_found_only_by_agent", "Offers only the matching agent found", _n),
]


def table(mean: dict) -> str:
    out = ["| Per simulated month | " + " | ".join(NAMES[d] for d in ORDER) + " |",
           "|---|" + "---:|" * len(ORDER)]
    for key, label, fmt in ROWS:
        cells = []
        for d in ORDER:
            v = mean[d].get(key)
            cell = "–" if v is None else fmt(v)
            cells.append(f"**{cell}**" if d == "platform" and v is not None else cell)
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def block() -> str:
    dev = json.loads((ROOT / "reports" / "dev.json").read_text())
    parts = [f"Means over dev seeds {', '.join(map(str, dev['seeds']))}, about {dev['mean']['platform']['rfqs']:.0f} enquiries "
             f"and {dev['mean']['platform']['lines_with_stock']:.0f} lines with matching stock per month.", "", table(dev["mean"])]
    p = dev["runs"][str(dev["seeds"][0])]["platform"]
    parts += ["", f"Why the platform's wrong offers happen (seed {dev['seeds'][0]}): "
              + ", ".join(f"{k} {v}" for k, v in p["wrong_offer_reasons"].items()) + ". "
              f"Designations learned from corrections after a clean replay: {', '.join(p['aliases_learned']) or 'none'}; "
              f"unknown designations per week: {' → '.join(map(str, p['unknown_designations_by_week']))}."]
    held = ROOT / "reports" / "heldout.json"
    if held.exists():
        h = json.loads(held.read_text())
        parts += ["", f"### Held-out months, run once (seeds {', '.join(map(str, h['seeds']))})", "",
                  "They contain a supplier layout (Italian headers, kg) and designations the dev months never had.", "",
                  table(h["mean"])]
    bake = ROOT / "reports" / "bakeoff.json"
    if bake.exists():
        b = json.loads(bake.read_text())
        parts += ["", "### Extraction models through the gateway (160 enquiries, simulated profiles)", "",
                  "| Model | Lines decided exactly | Values decided wrongly | Model's own numbers wrong | Enquiries to review | Cost / enquiry | p50 |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for name, m in b["models"].items():
            parts.append(f"| {name} | {m['lines_decided_exactly']} of {m['lines']} | {m['lines_decided_wrongly']} | "
                         f"{m['raw_model_lines_wrong']} | {m['rfqs_sent_to_review']} of {m['rfqs']} | {_eur(m['cost_per_rfq_eur'])} | "
                         f"{m['p50_ms'] / 1000:.1f} s |")
    gate = ROOT / "evals" / "baseline.json"
    if gate.exists():
        g = json.loads(gate.read_text())
        parts += ["", f"**Evaluation gate:** {g['cases']} emails traders corrected; the accepted baseline decides {g['exact']} exactly "
                  f"and {g['wrong_values']} values wrongly. A change ships only with no regression against it."]
    return "\n".join(parts)


def write() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text()
    a, b = text.index(START) + len(START), text.index(END)
    readme.write_text(text[:a] + "\n" + block() + "\n" + text[b:])


if __name__ == "__main__":
    write()
