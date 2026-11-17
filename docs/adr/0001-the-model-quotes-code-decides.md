# 1. The model quotes, code decides

## Context
Steel enquiries mix German and English number formats ("1.500", "2,0 x 1250 x C", "20.000 kg"). A model that returns
numbers directly gets some of them wrong, and a wrong thickness or width becomes a wrong offer.

## Decision
The extractor returns each value with the exact text it read it from. Code keeps a value only when the quote is in the
document, reads the number from the quote with field-aware plausibility, resolves grades through identifiers, and accepts
policy flags only with matching words. Anything else becomes an open point for a person.

## Consequences
A model's misread number cannot reach an offer. A model that paraphrases costs review time. Model choice moves review
rate and cost, not correctness, which is what the bake-off shows.
