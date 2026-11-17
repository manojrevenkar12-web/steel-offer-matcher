# Repeatable tasks

## Add a grade or an alias
1. Add it to `knowledge/grades.json` (an alias only if it names the same material; otherwise `comparable`).
2. Add a resolution case to `tests/test_domain.py`.
3. `make test && make gate`.

## Change a prompt or a route
1. Edit `prompts/<agent>.md` or `config/models.json`.
2. `make gate`. If it fails, the change does not ship. If it passes and the change is wanted, `make gate-accept`.
3. With Langfuse configured, push the prompt as a `staging` version; promote to `production` after the gate.

## Add a supplier list layout
Nothing to code: the mapping agent maps it once and code checks it on the values. If it is held, map it in the console;
the layout is remembered.

## Add a fit rule
1. Add the check to `src/steelmatch/domain/fit.py` with a reason a trader can read, and a test in `tests/test_domain.py`.
2. Its thresholds go in `config/matcher.json`.
3. `make test && make gate && make evaluate`; note the change in the README limits if it moves a number.

## Re-run the evaluation
`make evaluate && make bakeoff && make readme && make demo`. Held-out seeds already used stay used.
