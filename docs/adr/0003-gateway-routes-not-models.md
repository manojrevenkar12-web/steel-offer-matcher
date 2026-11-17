# 3. Agents call gateway routes, not models

## Decision
LiteLLM's router maps routes (`extract`, `extract-strong`, `mapping`, `match`, `vision`) to deployments with retries and
fallbacks, and books tokens, cost and latency per call. Pydantic AI agents reach it through an in-process bridge; with a
LiteLLM proxy the same agents run on Pydantic AI's OpenAI model pointed at the proxy.

## Consequences
Changing a model is a config change judged by the gate. Cost per enquiry is reported per route and per model.
