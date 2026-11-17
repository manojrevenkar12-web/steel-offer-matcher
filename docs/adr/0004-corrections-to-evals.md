# 4. Corrections become eval cases, aliases need a replay

## Decision
Each correction in the console is stored, turned into an eval case, and sent to Langfuse as a CORRECTION score and a
dataset item. The gate replays all cases before any prompt, route or rule change. Repeated corrections of one
designation become a learned alias only if a replay over every stored case shows no regression.

## Consequences
The eval set grows from real failures. A wrong alias proposed by contradictory corrections is rejected, not learned.
