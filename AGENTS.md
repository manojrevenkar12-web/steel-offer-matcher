# AGENTS.md

How to work in this repository, for people and coding agents.

## Fast feedback

- `make test`: everything but Postgres, about a minute, no network or keys.
- `make test-service` with `STEEL_TEST_DATABASE_URL` set: migrations, search contract, intake, review loop, MCP.
- `make gate`: the corrected cases against the accepted baseline. Run it after touching prompts, routes or rules.
- `cd console && npm test`.

## Invariants

1. **The truth stays in the world.** `src/steelworld` knows what buyers accept and what delivery costs. The platform
   (`src/steelmatch`) never imports it. Only the simulated models read the world's record of a document, standing in for a
   model reading it.
2. **The world does not read the matcher's settings.** No file in `src/steelworld` reads `config/matcher.json`,
   `config/models.json` or `knowledge/`.
3. **Same luck for every design.** Randomness is keyed by seed and by what it is about (`steelworld.rand.of`), so every
   design meets the same documents and the same model mistakes.
4. **The model reads and code decides.** A value the model reports counts only through text it quoted from the document;
   code reads the number, resolves the grade, checks the policy words, checks fit and ranks.
5. **Identifiers decide and names propose.** A grade resolves only through an identifier the knowledge base lists (or an
   alias learned after a replay). Similar names produce proposals for a person.
6. **No change without the gate.** A prompt, route or rule change passes `make gate` before it ships. The baseline moves
   only after a pass (`make gate-accept`).
7. **Settings in config.** Thresholds, limits, routes and prices live in `config/`, not in code.
8. **Generated reports.** `reports/*.json` and the README results block come from `make evaluate`, `make heldout`,
   `make bakeoff` and `make readme`. Never edit them by hand.

## Held-out rule

The held-out seeds run once (`make lock`, then `make heldout`). After that, `src/steelmatch`, its config, prompts and
knowledge are frozen for this evaluation: a test compares the fingerprint. Improvements after the held-out run are judged on
new held-out seeds, not on the old ones.
