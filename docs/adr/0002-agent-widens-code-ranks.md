# 2. The matching agent widens the search; code checks and ranks

## Context
A literal filter (requested grade, thickness band, minimum width) misses lots a trader would find: an alternative
thickness the buyer named, a designation a supplier wrote differently, a higher grade the buyer allows.

## Decision
The harness runs the literal search first, then lets the agent search further with bounded tools. Every lot it finds is
checked by the same fit rules and ranked by landed cost per usable tonne. The agent never decides fit or price.

## Consequences
The agent's value is measurable as an ablation (platform with and without it). A model outage degrades to the literal
search, never to no answer.
