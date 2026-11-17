# steel-offer-matcher

Agentic steel sourcing for a digital trading desk. Buyers send enquiries as emails, PDFs, spreadsheets and photos;
suppliers send stock lists in their own layouts. This platform reads both, resolves steel grades across EN, DIN, ASTM and
material numbers, finds the lots that really fit (slitting a wider coil, cutting a coil to sheets, a higher grade when the
buyer allows it) and ranks them by landed cost, with an explanation for every check and a person deciding where a
decision is needed.

Built on Pydantic AI, LiteLLM and Langfuse, with an MCP server and client, Postgres with pgvector hybrid search,
FastAPI/Pydantic/async SQLAlchemy/Alembic, a Pub/Sub-style intake and a React/TypeScript review console.

![The review desk: the enquiry with every value it was read from marked, and ranked offers drawn as coil tags with their checks](docs/desk.png)

## Results

Everything below is measured on simulated months: about 450 enquiries and 1,350 supplier lots each, rendered as real
emails, PDFs, xlsx/csv stock lists and photographed enquiries. The world keeps its own truth of what each buyer accepts
and what delivery really costs; the platform never reads it. Every design faces the same documents and the same model
mistakes. The numbers are written by `make readme` from `reports/`.

<!-- results:start -->
Means over dev seeds 11, 23, 37, about 445 enquiries and 395 lines with matching stock per month.

| Per simulated month | Trader by hand | Model decides | **This platform** | Platform, no agent |
|---|---:|---:|---:|---:|
| Lines with stock that got a valid offer | 76.4% | 51.2% | **93.4%** | 89% |
| Lines with stock left without a valid offer | 93.7 | 193.3 | **25.7** | 43.3 |
| Offers the buyer would reject | 50.3 | 626.3 | **13.3** | 10.7 |
| Expected claim cost (35% of wrong offers ordered, €2,400 each) | €42,280 | €526,120 | **€11,200** | €8,960 |
| Above the best landed price, € per tonne covered | €4.97 | €8.17 | **€4.31** | €4.37 |
| Trader minutes per enquiry | 35.8 | 3 | **5.1** | 4.9 |
| Median time to answer (business hours) | 0.41 | 0.02 | **0.04** | 0.03 |
| Slowest tenth answered within (business hours) | 1.03 | 0.04 | **0.12** | 0.12 |
| Enquiries with a decision for a person | 0% | 0% | **36.2%** | 33.2% |
| Model cost per enquiry | €0 | €0.0028 | **€0.0077** | €0.0057 |
| Model time per enquiry, p95 (s) | 0 | 6.7 | **18.6** | 10 |
| Offers only the matching agent found | 0 | 0 | **51** | 0 |

Why the platform's wrong offers happen (seed 11): late 3, origin 5, price 4, width 1. Designations learned from corrections after a clean replay: S235, ST37, ST52, DC01AM, 430, DX51; unknown designations per week: 8 → 5 → 2 → 3.

### Held-out months, run once (seeds 9104, 9209)

They contain a supplier layout (Italian headers, kg) and designations the dev months never had.

| Per simulated month | Trader by hand | Model decides | **This platform** | Platform, no agent |
|---|---:|---:|---:|---:|
| Lines with stock that got a valid offer | 75% | 46.6% | **92.6%** | 87.5% |
| Lines with stock left without a valid offer | 101.5 | 216.5 | **30** | 50.5 |
| Offers the buyer would reject | 58.5 | 560 | **15.5** | 14.5 |
| Expected claim cost (35% of wrong offers ordered, €2,400 each) | €49,140 | €470,400 | **€13,020** | €12,180 |
| Above the best landed price, € per tonne covered | €5.32 | €10.90 | **€2.96** | €3.12 |
| Trader minutes per enquiry | 35.6 | 3 | **5** | 4.8 |
| Median time to answer (business hours) | 0.42 | 0.02 | **0.03** | 0.02 |
| Slowest tenth answered within (business hours) | 1.02 | 0.04 | **0.11** | 0.11 |
| Enquiries with a decision for a person | 0% | 0% | **38.2%** | 32.9% |
| Model cost per enquiry | €0 | €0.0028 | **€0.0077** | €0.0057 |
| Model time per enquiry, p95 (s) | 0 | 6.5 | **18.3** | 10.3 |
| Offers only the matching agent found | 0 | 0 | **50.5** | 0 |

### Extraction models through the gateway (160 enquiries, simulated profiles)

| Model | Lines decided exactly | Values decided wrongly | Model's own numbers wrong | Enquiries to review | Cost / enquiry | p50 |
|---|---:|---:|---:|---:|---:|---:|
| fast-commercial | 164 of 232 | 0 | 41 | 67 of 160 | €0.0009 | 1.5 s |
| open-weights-8b | 108 of 232 | 0 | 97 | 114 of 160 | €0.0001 | 0.6 s |
| strong-commercial | 195 of 232 | 0 | 26 | 36 of 160 | €0.0076 | 3.4 s |

**Evaluation gate:** 60 emails traders corrected; the accepted baseline decides 42 exactly and 0 values wrongly. A change ships only with no regression against it.
<!-- results:end -->

## What it does

```mermaid
flowchart LR
  A[Inbound email<br/>Pub/Sub push] --> Q[(jobs, idempotent<br/>per message id)]
  S[Supplier stock list<br/>xlsx / csv] --> M[Column mapping agent<br/>once per layout]
  M -->|code checks values| I[(stock_items<br/>tsvector + pgvector)]
  Q --> W[Worker]
  W --> T[Transcriber<br/>vision route]
  W --> X[Extractor agent<br/>quotes every value]
  X -->|code reads numbers from quotes,<br/>grades from identifiers| L[Lines + buyer policy]
  L --> H[Matching harness:<br/>literal search, then agent]
  H -->|search_inventory, check_fit| I
  H --> F[Fit rules + ranking<br/>landed cost per usable tonne]
  F --> R[Review console]
  R -->|corrections| E[(eval cases)] --> G{Eval gate}
  R -->|repeated grade corrections| K[Alias learner<br/>after replay]
```

**The model reads, code decides.** The extractor returns each value together with the exact text it read it from.
Code drops any quote that is not in the email, reads the number from the quote rather than taking the model's number
("1.500" is 1500 mm wide but 1.5 mm thick), resolves the grade only through an identifier the knowledge base knows
(S235JR, 1.0038, St37-2), and accepts "a higher grade is fine" only when the quoted words say so ("Gleichwertige Güte"
does not). Everything the code cannot decide becomes an open point for a person. In the bake-off, no model got a value
decided wrongly; weaker models cost review time instead.

**Fit rules a trader would apply.** Thickness tolerance by EN 10051/10131 bands (simplified), slitting with edge trim and
loss, coil cut to sheets, cross-cutting, upgrade rules per grade family (higher strength and toughness, stainless 316L for
304 when allowed, carbon-equivalent warning), coating mass and system, surface finish, certificate level (EN 10204),
second choice, origin exclusions and EU sanctions on Russian and Belarusian steel, partial lots, delivery date with
transit, and target price on landed cost. Unknown is never a pass: a list without a surface or origin column sends the
lot to review when the buyer cares.

**An agent harness, not a loop.** The matching agent (Pydantic AI) only widens the search the literal filter starts
with: alternatives the buyer named, designations a supplier wrote differently, higher grades. It sees the code's verdict
through `check_fit` and never makes one. The harness bounds it (requests, tool calls, input tokens, searches that find
nothing new), retries model failures, falls back to the literal search when the model is unavailable, and journals each
step so a restarted run does not pay for the same model calls twice. The ablation column above is the agent's measured
worth.

**LiteLLM as the gateway.** Agents call routes (`extract`, `extract-strong`, `mapping`, `match`, `vision`), never models.
The gateway handles retries, fallbacks (a failing route falls back to a stronger one), and books tokens, cost and latency
per call. Extraction escalates to the strong route only when the cheap one left quotes or required values unreadable.
`config/models.production.example.json` shows the same routes on commercial and self-hosted (vLLM) models.

**Langfuse for prompts, traces and evaluations.** Prompts live in `prompts/` and are pushed to Langfuse as versions with
the label `staging`; agents read `production` and fall back to the repository copy when Langfuse is down. Every enquiry is
a trace with one generation per model call and the prompt versions used. A correction in the console becomes a
Langfuse `CORRECTION` score on that trace and a dataset item.

**Corrections become evaluations.** `evals/cases/` holds emails traders corrected, with the confirmed values. The gate
(`make gate`) replays them and fails on any case that gets worse than the accepted baseline; the baseline moves only after
a pass. Repeated corrections of the same designation ("St37" means S235JR) become an alias only after a replay over every
stored case shows no regression.

**MCP server and client.** `/mcp/` exposes `search_inventory`, `get_lots`, `resolve_grade`, `check_lots`, `get_rfq` and
`record_correction` over streamable HTTP with bearer tokens and scopes per client (trader desk, ERP connector, catalogue
bot). Every call is logged with its outcome (`ok`, `denied`, `error`) and latency. `tools/mcp_client.py` lets agents use the
same tools through the server; a test checks it gives the same offers as the direct database path.

**Search.** Hard filters (finish, form family, grade set, thickness window, minimum width) in SQL, then a full-text ranking
(tsvector, GIN) and a vector ranking (pgvector, HNSW, cosine) fused with reciprocal rank fusion. The in-memory search used
by the evaluation runs the same steps; a contract test keeps the two in step.

## Stack

| Area | Used here |
|---|---|
| Agents | Pydantic AI 2 (tools, structured output, usage limits), a bridge to LiteLLM's router |
| Gateway | LiteLLM Router: routes, retries, fallbacks, cost; a custom provider runs the simulated models offline |
| Prompts, traces, evals | Langfuse SDK: prompt versions and labels, traces and generations, CORRECTION scores, datasets |
| Tools | MCP Python SDK 2: server with token verification and scopes, client used by agents |
| Data | Postgres 16, pgvector (HNSW), full-text search, async SQLAlchemy 2, Alembic |
| Service | FastAPI, Pydantic, Pub/Sub push intake, a leased job queue (Cloud Tasks-style), Docker |
| Console | React 19, TypeScript, TanStack Query and Router, Tailwind 4, Motion |
| CI | GitHub Actions: tests, gate, held-out guard, Postgres+pgvector service tests, console, image |

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
make test                 # 73 tests, about a minute; no network, no keys
make gate                 # replay the corrected cases against the baseline
make evaluate             # three simulated months, four designs (~9 min)
make bakeoff              # extraction models side by side

# the service on Postgres with pgvector
docker compose up         # http://localhost:8000, MCP at /mcp/
curl -X POST "localhost:8000/sim/replay?day=0"          # loads the day's stock lists, pushes its emails
curl -H "Authorization: Bearer demo-trader-token" localhost:8000/api/rfqs

# the console (demo mode reads console/public/demo.json; set VITE_API_URL for live)
cd console && npm ci && npm run dev
```

Service tests: `STEEL_TEST_DATABASE_URL=postgresql+asyncpg://steel:steel@127.0.0.1:5433/steeltest make test-service`.

## Held-out rule

`make lock` fingerprints the platform (`src/steelmatch`, its config, prompts and knowledge). `make heldout` runs the
held-out months once, only if the fingerprint matches, and refuses a second run. A test fails if the platform changes
after the held-out run. The held-out months contain a supplier layout and designations the dev months never had.

## Limits

- Buyers, suppliers, documents and the models' mistakes are simulated; the model profiles are not measurements of
  named commercial models. The bake-off shows how the platform behaves across a range of model quality, not which vendor
  to buy.
- Tolerance tables, freight and processing costs are simplified illustrations, not the standards or a price list.
- Lists without an origin or availability column remain the main source of wrong offers (origin exclusions, sanctions,
  late delivery); the fix is supplier data, not a model.
- Photographed enquiries are transcribed by a vision route; numbers from a transcription always go to a person.
- No connection to a real mailbox or ERP; intake is a Pub/Sub push endpoint and the ERP side is an MCP client with a
  read scope.

## Layout

```
src/steelmatch/   the platform: domain rules, agents, harness, gateway, Langfuse, MCP, service
src/steelworld/   the simulated market and the simulated models (never imported by the platform)
src/steeleval/    designs, scoring, held-out runs, eval set, demo export, README writer
config/           matcher.json (platform), models.json (gateway), world.json (world only), desks.json (clients)
knowledge/        grades, tolerances, freight, places
prompts/          agent prompts (pushed to Langfuse)
evals/            corrected cases and the accepted gate baseline
console/          React review console
docs/adr/         decisions
```
