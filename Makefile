PY ?= .venv/bin/python
export PYDANTIC_AI_NO_BANNER=1

.PHONY: test test-service evaluate bakeoff gate gate-accept lock heldout readme demo console serve migrate

test:            ## fast feedback: everything but the Postgres tests
	$(PY) -m pytest -q

test-service:    ## needs STEEL_TEST_DATABASE_URL (Postgres with pgvector)
	$(PY) -m pytest -q tests/test_service.py

evaluate:        ## dev months, every design -> reports/dev.json
	$(PY) -m steeleval.evaluate dev

bakeoff:         ## extraction models side by side -> reports/bakeoff.json
	$(PY) -m steeleval.evaluate bakeoff

gate:            ## replay the corrected cases with the current prompt and route
	$(PY) -m steeleval.evalset gate

gate-accept:     ## the same, and make a passing result the baseline
	$(PY) -m steeleval.evalset accept

lock:            ## fingerprint the platform before the held-out run
	$(PY) -m steeleval.evaluate lock

heldout:         ## held-out months, once
	$(PY) -m steeleval.evaluate heldout

readme:          ## write the results block in README.md from reports/
	$(PY) -m steeleval.evaluate readme

demo:            ## record a simulated morning for the console's demo mode
	$(PY) -m steeleval.export_demo

console:
	cd console && npm ci && npm test && npm run build

migrate:
	.venv/bin/alembic upgrade head

serve:           ## the service on a simulated month (needs Postgres)
	$(PY) -m steeleval.serve
