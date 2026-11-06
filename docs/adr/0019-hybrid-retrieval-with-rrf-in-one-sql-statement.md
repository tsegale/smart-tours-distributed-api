# 19. Hybrid retrieval with RRF in one SQL statement

Date: 2026-11-06 · Status: Accepted

## Context
Tour search has to answer two kinds of question that fail in opposite ways. "Abandoned town swallowed by the desert" has no
word in common with the tour it means, while "Kolmanskop" is a name, and an embedding can miss it. It also has to honour
filters: a place, a price, dates, seats, a duration.

The AI service owns a read model built from catalogue events (ADR 0001, 0008), so all of that can live in one database, with
pgvector, PostGIS and full-text search side by side.

## Decision
**One SQL statement: two candidate lists over the same filters, fused by rank.**

- **Filters apply inside each list,** not to the results afterwards. A filter applied afterwards can leave a page nearly empty
  while plenty of matches sit further down. `hnsw.iterative_scan = strict_order` keeps an index scan going until enough rows
  pass, for when a catalogue grows large enough for the planner to choose the index.
- **Reciprocal Rank Fusion:** a tour scores `weight / (k + rank)` in each list it appears in, with `k = 60`. A cosine distance and
  a text rank aren't comparable, and RRF needs only their order, so there is nothing to calibrate or to drift.
- **The lexical list gets half a vote (`DEFAULT_LEXICAL_WEIGHT = 0.5`).** It ORs its terms, because the implied AND returns nothing
  for a natural sentence. So it is sharp on a name and noisy on a sentence. Equal weights let that noise hurt the sentences, and
  no lexical list at all fails on names.
- **Both stemmers on the query.** A tour is stemmed in its own language, and a query can be in either.
- **Query understanding first** (`app/query_parser.py`): place, price, date and duration phrases become filters, in English and
  German, relative to Windhoek's clock, and the response says what was understood. No model involved.
- **`explain=true`** returns each result's dense rank, lexical rank and fused score.

The numbers are in [search-quality](../benchmarks/search-quality.md). nDCG@10 with the chosen embedding model, on the three
query shapes (vague descriptions / filter phrases / exact names): dense with parser 0.781 / 0.895 / 0.577; equal-weight hybrid with
parser 0.736 / 0.830 / 0.902; weighted hybrid with parser 0.752 / 0.880 / 0.878. Lexical alone: 0.459 / 0.476 / 0.876. The vague
scores use the labels the owner reviewed on 2026-11-06, which changed no decision here.

## Consequences
- **This is a compromise, and the numbers show what it costs.** On vague descriptions, dense with the parser is ahead by 0.029, with an
  interval that includes zero, [-0.020, +0.079]. On exact names, it loses by 0.301 [-0.449, -0.160]. A small loss on
  one shape buys protection from a large one on the other.
- **The weight was chosen on the same queries it is reported on,** from four coarse values. Treat it as a direction, not an
  optimum, and re-check it when real queries exist. `k` barely mattered, so it stayed at the conventional 60.
- **It only wins because a test set was added to challenge it.** The first results said dense beat hybrid outright, because the
  curated queries had been written to avoid the tours' wording. A known-item set reversed that. Which shapes real users type,
  and in what mix, isn't known, and the balance depends on it.
- **No index scan at this size.** At 2,505 tours the planner rightly chooses an exact scan, so these results are exact and
  recall isn't reduced by the index. HNSW behaviour at scale is not measured here. The statement is tested to be able to use the
  index, because a window function ordering by distance, as first written, could not: it sorted every row, at 65 ms against 6.6.
- **Search latency is mostly the query embedding** and well inside budget: 24 ms p50 and 31 ms p95 on the curated set, on an
  8 GB laptop, with Postgres in a container.
