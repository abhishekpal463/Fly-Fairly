# Approach Memo — Airport Autocomplete Prototype

## What we built
We implemented a lightweight airport and city autocomplete prototype with:
- a Node.js + Express backend,
- Elasticsearch-backed search, and
- an evaluation harness for golden queries.

The core flow is:
1. Normalize user input and detect code-like queries.
2. Use exact-code short-circuiting for IATA/ICAO-style inputs.
3. Fall back to ranked fuzzy/prefix matching over airport and city names.
4. Return a compact JSON result set suitable for a search box UI.

## Data and search approach
The prototype uses OurAirports-style canonical airport data and a SQLite staging layer for local development. This keeps the pipeline deterministic and easy to iterate on. The search layer uses Elasticsearch because it is well suited to prefix, fuzzy, and phrase-based autocomplete at low cost for a prototype.

We intentionally kept the ranking simple but practical:
- exact code matches are prioritized first;
- name and alias prefix matches are boosted;
- popularity and scheduled-service signals are used as soft ranking factors;
- geo proximity can be applied when coordinates are provided.

This is a pragmatic trade-off: good enough for a working prototype, while still aligning with the real production requirements for disambiguation and typo tolerance.

## What worked well
- Fast iteration with local tooling and a simple API contract.
- Reliable live verification of the real runtime route.
- An evaluation harness that can be reused for regression testing.
- A clear separation between ETL, indexing, and search logic.

## What we would improve with more time
- Add richer multilingual and region-aware ranking.
- Improve city/metro clustering and multi-airport city handling.
- Expand the golden test suite with real failure cases such as Florida, Bali, London ambiguity, and CJK queries.
- Add observability, logging, and a stronger baseline comparison for precision and recall.

## LLM and tooling usage
This work used Copilot as the main implementation aid for scaffolding, debugging, and code refinement. The main value came from rapid iteration on the search logic, the route wiring, and the test/evaluation path rather than from heavy manual coding.

## Recommendation
This prototype is a solid foundation for a v1 product: it demonstrates the end-to-end autocomplete flow, verifies the live backend route, and gives a reproducible evaluation path. It is intentionally narrow and practical, which matches the goal of a working prototype rather than a fully productionized search platform.
