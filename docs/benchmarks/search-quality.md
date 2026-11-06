# Search quality: which retrieval, which model, which runtime

Every choice in [ADR 0019](../adr/0019-hybrid-retrieval-with-rrf-in-one-sql-statement.md) and
[ADR 0020](../adr/0020-embedding-model-and-runtime-by-evaluation.md) rests on the tables here. They are written from
`ai-recommendation-service/eval/results/` by `python -m eval.write_reports`, so nothing is typed by hand.

**Decision: `paraphrase-multilingual-MiniLM-L12-v2`, int8, on ONNX Runtime. Hybrid retrieval with query understanding and a
lexical weight of 0.5, `k` = 60. No cross-encoder.** The owner's review of the curated labels changed none of this
([before and after](#the-owners-review-before-and-after)).

## How to read this

**The corpus is synthetic.** `datasets/tours_v1`: 2,505 tours, 105 of them individually written and 2,400 composed from 172
sentences ([datasheet](../datasets.md)). **Nothing here has met a real user.** Three query sets, chosen because they fail in
different ways, plus a fourth that decides nothing. They are reported apart, because a single average would hide what decides
this:

| Set | Queries | Corpus | Shape | Labels |
| --- | --- | --- | --- | --- |
| Curated | 50, EN and DE | 105 written tours | Vague descriptions, deliberately avoiding the tours' wording | Reviewed: proposed by Claude, revised in a second Claude pass, contested calls decided and the set signed off by the owner on 2026-11-06 |
| Rule | 106, EN and DE | 2,400 composed tours | Activity, area, price and family phrases | Computed from the tours' generating attributes |
| Known-item | 26, EN and DE | 105 written tours | One exact, rare word | Computed: 3 if the term is in the name, 2 if in the description |
| [Coverage](#coverage-the-tours-the-reviewed-set-misses) | 12, EN and DE | 105 written tours | Conversational, aimed at tours the curated set never labels | Proposed by Claude, marked `needs_review`. **Not reviewed, and no decision rests on them** |

The curated set disfavours lexical search by construction and the known-item set favours it, also by construction. Neither is
"the truth". How often real people type each shape is unknown, and it would move the balance.

Metrics: nDCG@10 on grades 0 to 3, recall@20, MRR, with 95% bootstrap intervals over queries. Comparisons between systems use a
paired bootstrap, query by query, which is far tighter than comparing two separate intervals.

Machine: an 8 GB Windows laptop, Postgres in a container. Search latencies are one client, end to end, including embedding the
query. At 2,505 tours the planner chooses an exact scan, so results are exact and HNSW isn't exercised: **behaviour at a larger
scale isn't measured.**

## The owner's review: before and after

The owner reviewed the curated labels on 2026-11-06. A second Claude pass had already made 31 grade changes on 19 queries, taking
the set from 122 labels to 142, and the owner decided the contested calls (the reasoning is in `eval/curated.py`). The full
evaluation was then run again. Rule and known-item labels didn't change, and neither did a single score on those sets, which
`eval.write_reports` checks.

**No decision changed: retrieval mode, `k`, embedding model, ONNX and the cross-encoder all stay as they were.** The chosen system
went from 0.751 to 0.756.

Each decision's comparison on the curated set, as a paired difference:

<!-- table:decisions -->
| Against the chosen system | Before | After | Verdict changed |
| --- | --- | --- | --- |
| Lexical only | -0.290, worse | -0.297 [-0.374, -0.221], worse | no |
| Dense + parser | +0.041, no clear difference | +0.039 [-0.003, +0.082], no clear difference | no |
| Equal weights | -0.024, worse | -0.026 [-0.047, -0.009], worse | no |
| With EN-DE cross-encoder | -0.028, no clear difference | -0.038 [-0.110, +0.034], no clear difference | no |
| With EN cross-encoder | -0.036, no clear difference | -0.047 [-0.113, +0.013], no clear difference | no |
| Float instead of int8 | -0.006, no clear difference | -0.007 [-0.026, +0.010], no clear difference | no |
| e5-small int8 | -0.041, no clear difference | -0.050 [-0.097, -0.004], worse | **yes** |
| bge-small-en | -0.098, worse | -0.104 [-0.158, -0.052], worse | no |
| all-MiniLM-L6-v2 | -0.121, worse | -0.129 [-0.189, -0.070], worse | no |
| k = 10 against k = 60, equal weights | +0.023, better | +0.022 [+0.007, +0.042], better | no |
<!-- /table:decisions -->

- **Retrieval mode.** Dense + parser is still ahead on vague queries by an amount the interval can't separate from nothing, and
  still 0.290 behind on exact names, so hybrid stays. Lexical alone stays far behind.
- **`k`.** The same trade as before: 10 is clearly ahead on the curated set, by about the same amount, and 60 is ahead on filter queries
  (0.710 against 0.680). Neither wins on both, so the conventional 60 stays.
- **Embedding model.** The same winner, and on firmer ground. `e5-small` had been the close call, with no clear difference on vague
  queries. It is now clearly worse on them as well as on filter queries, so its only clear win is exact names. This is the one
  verdict the review moved, and it moved towards the existing choice. The upper end of that interval is -0.004, so it is clear, but not by much.
- **ONNX int8.** No clear difference from float, as before. The runtime decision was never about these labels: float and torch
  produce the same vectors.
- **Cross-encoders.** Both still lower the chosen system's score on vague queries, by an amount the interval can't separate from nothing.
  They stay off.

Recall@20 fell for the chosen system, from 0.942 to 0.909, because there are more labelled tours to find. The rankings
themselves are the same: only the labels changed.

Every system on the curated set. "Before" is the same code under Claude's first labels:

<!-- table:review -->
| System | Before | After [95% CI] | Change |
| --- | --- | --- | --- |
| lexical | 0.462 | 0.459 [0.374, 0.549] | -0.003 |
| dense:minilm-l6 | 0.605 | 0.599 [0.524, 0.671] | -0.006 |
| hybrid:minilm-l6:k10 | 0.621 | 0.617 [0.552, 0.681] | -0.004 |
| hybrid:minilm-l6:k60 | 0.614 | 0.610 [0.542, 0.676] | -0.004 |
| hybrid:minilm-l6:w0.5 | 0.620 | 0.616 [0.552, 0.681] | -0.004 |
| hybrid+parser:minilm-l6 | 0.624 | 0.621 [0.550, 0.687] | -0.003 |
| dense+parser:minilm-l6 | 0.616 | 0.611 [0.533, 0.683] | -0.005 |
| hybrid+parser:minilm-l6:w0.5 | 0.631 | 0.627 [0.559, 0.693] | -0.004 |
| hybrid+parser+rerank:minilm-l6:ms-marco-l6-int8 | 0.696 | 0.688 [0.622, 0.751] | -0.007 |
| hybrid+parser+rerank:minilm-l6:en-de-l6-int8 | 0.686 | 0.683 [0.612, 0.749] | -0.003 |
| dense:bge-small-en | 0.648 | 0.642 [0.568, 0.711] | -0.007 |
| hybrid:bge-small-en:k10 | 0.640 | 0.633 [0.565, 0.698] | -0.007 |
| hybrid:bge-small-en:k60 | 0.633 | 0.627 [0.556, 0.695] | -0.007 |
| hybrid:bge-small-en:w0.5 | 0.639 | 0.635 [0.563, 0.704] | -0.004 |
| hybrid+parser:bge-small-en | 0.648 | 0.644 [0.571, 0.711] | -0.004 |
| dense+parser:bge-small-en | 0.663 | 0.658 [0.584, 0.730] | -0.004 |
| hybrid+parser:bge-small-en:w0.5 | 0.654 | 0.652 [0.578, 0.722] | -0.002 |
| hybrid+parser+rerank:bge-small-en:ms-marco-l6-int8 | 0.699 | 0.692 [0.623, 0.755] | -0.007 |
| hybrid+parser+rerank:bge-small-en:en-de-l6-int8 | 0.664 | 0.664 [0.588, 0.736] | +0.001 |
| dense:paraphrase-ml | 0.817 | 0.819 [0.746, 0.884] | +0.002 |
| hybrid:paraphrase-ml:k10 | 0.769 | 0.770 [0.710, 0.824] | +0.001 |
| hybrid:paraphrase-ml:k60 | 0.747 | 0.749 [0.681, 0.806] | +0.002 |
| hybrid:paraphrase-ml:w0.5 | 0.765 | 0.769 [0.707, 0.825] | +0.004 |
| hybrid+parser:paraphrase-ml | 0.728 | 0.729 [0.659, 0.794] | +0.002 |
| dense+parser:paraphrase-ml | 0.776 | 0.779 [0.700, 0.853] | +0.002 |
| hybrid+parser:paraphrase-ml:w0.5 | 0.745 | 0.749 [0.682, 0.811] | +0.004 |
| hybrid+parser+rerank:paraphrase-ml:ms-marco-l6-int8 | 0.711 | 0.704 [0.640, 0.765] | -0.006 |
| hybrid+parser+rerank:paraphrase-ml:en-de-l6-int8 | 0.734 | 0.730 [0.663, 0.788] | -0.003 |
| dense:paraphrase-ml-int8 | 0.813 | 0.815 [0.742, 0.880] | +0.002 |
| hybrid:paraphrase-ml-int8:k10 | 0.761 | 0.762 [0.696, 0.818] | +0.002 |
| hybrid:paraphrase-ml-int8:k60 | 0.738 | 0.740 [0.670, 0.799] | +0.002 |
| hybrid:paraphrase-ml-int8:w0.5 | 0.759 | 0.764 [0.697, 0.822] | +0.004 |
| hybrid+parser:paraphrase-ml-int8 | 0.728 | 0.730 [0.658, 0.794] | +0.002 |
| dense+parser:paraphrase-ml-int8 | 0.793 | 0.795 [0.725, 0.863] | +0.003 |
| hybrid+parser:paraphrase-ml-int8:w0.5 | 0.751 | 0.756 [0.687, 0.818] | +0.004 |
| hybrid+parser+rerank:paraphrase-ml-int8:ms-marco-l6-int8 | 0.716 | 0.709 [0.643, 0.771] | -0.007 |
| hybrid+parser+rerank:paraphrase-ml-int8:en-de-l6-int8 | 0.723 | 0.717 [0.643, 0.782] | -0.006 |
| dense:e5-small-int8 | 0.709 | 0.704 [0.643, 0.764] | -0.005 |
| hybrid:e5-small-int8:k10 | 0.687 | 0.684 [0.623, 0.739] | -0.004 |
| hybrid:e5-small-int8:k60 | 0.680 | 0.678 [0.613, 0.738] | -0.003 |
| hybrid:e5-small-int8:w0.5 | 0.701 | 0.697 [0.636, 0.753] | -0.004 |
| hybrid+parser:e5-small-int8 | 0.692 | 0.689 [0.623, 0.748] | -0.003 |
| dense+parser:e5-small-int8 | 0.728 | 0.722 [0.663, 0.780] | -0.006 |
| hybrid+parser:e5-small-int8:w0.5 | 0.711 | 0.706 [0.644, 0.763] | -0.005 |
| hybrid+parser+rerank:e5-small-int8:ms-marco-l6-int8 | 0.717 | 0.710 [0.645, 0.769] | -0.007 |
| hybrid+parser+rerank:e5-small-int8:en-de-l6-int8 | 0.714 | 0.711 [0.642, 0.777] | -0.003 |
<!-- /table:review -->

## 1. Retrieval modes

With the chosen model. nDCG@10 [95% CI]. Latency is on the curated set, from the run before the review: labels can't change it,
and the later run shared a busy laptop (133 minutes, with the chosen system's p50 at 115 ms rather than 24).

<!-- table:modes -->
| System | Curated: vague | Rule: filters | Known: exact name | ms p50 / p95 |
| --- | --- | --- | --- | --- |
| Lexical only | 0.459 [0.374, 0.549] | 0.476 [0.405, 0.550] | 0.876 [0.731, 0.984] | 5 / 8 |
| Dense only | 0.815 [0.742, 0.880] | 0.747 [0.695, 0.795] | 0.554 [0.401, 0.712] | 22 / 27 |
| Hybrid, equal weights, k = 60 | 0.740 [0.670, 0.799] | 0.710 [0.650, 0.764] | 0.886 [0.767, 0.976] | 24 / 35 |
| Hybrid, equal weights, k = 10 | 0.762 [0.696, 0.818] | 0.680 [0.624, 0.731] | 0.890 [0.773, 0.980] | 28 / 65 |
| Hybrid, lexical weight 0.5 | 0.764 [0.697, 0.822] | 0.757 [0.702, 0.808] | 0.868 [0.747, 0.968] | 25 / 30 |
| Dense + parser | 0.795 [0.725, 0.863] | 0.898 [0.861, 0.932] | 0.571 [0.426, 0.725] | 24 / 35 |
| Hybrid + parser, equal weights | 0.730 [0.658, 0.794] | 0.836 [0.785, 0.884] | 0.885 [0.781, 0.967] | 29 / 38 |
| **Hybrid + parser, lexical weight 0.5 (chosen)** | 0.756 [0.687, 0.818] | 0.886 [0.841, 0.924] | 0.861 [0.750, 0.961] | 24 / 31 |
| ... + EN-DE cross-encoder | 0.717 [0.643, 0.782] | 0.869 [0.826, 0.907] | 0.812 [0.677, 0.926] | 515 / 658 |
| ... + EN cross-encoder | 0.709 [0.643, 0.771] | 0.829 [0.777, 0.879] | 0.915 [0.816, 0.984] | 767 / 1025 |
<!-- /table:modes -->

What the numbers decided:

- **Hybrid stays, though the first run said otherwise.** On the curated set alone, dense beat equal-weight hybrid outright.
  That set was written to avoid the tours' wording, so a known-item set was added to challenge it: dense then fell to about 0.57
  on exact names, where it often can't find a word it has never seen in that sense.
- **Equal weights were too much for the lexical list.** It ORs its terms, so a whole sentence matches much of the catalogue.
  Half a vote recovered most of the loss on sentences and kept most of the gain on names. **The weight was picked on these same
  queries, from four coarse values.** It is a direction, not a tuned optimum.
- **Query understanding is the largest single gain on filter-style queries** and costs nothing measurable. It slightly hurts
  vague queries, whose words sometimes read as filters.
- **`k` didn't matter enough to move** from the conventional 60: 10 is better on one set and worse on another.
- **Cross-encoders are off.** On this model they add no clear gain anywhere, and at least 500 ms at p50 on this laptop against
  about 24 ms. They did help the weaker models (next section's comparison was run with them too, in `models.json`), which is
  why this took measuring rather than assuming. The code stays behind `RERANK_MODEL`, so that a better cross-encoder can be
  tried with one setting.

Each alternative against the chosen system, as a paired difference in nDCG@10:

<!-- table:paired -->
| Against the chosen system | Curated | Rule | Known |
| --- | --- | --- | --- |
| Lexical only | -0.297 [-0.374, -0.221], worse | -0.410 [-0.484, -0.336], worse | +0.015 [-0.115, +0.150], no clear difference |
| Dense + parser | +0.039 [-0.003, +0.082], no clear difference | +0.012 [-0.019, +0.046], no clear difference | -0.290 [-0.431, -0.155], worse |
| Equal weights | -0.026 [-0.047, -0.009], worse | -0.050 [-0.085, -0.020], worse | +0.024 [+0.000, +0.071], no clear difference |
| With EN-DE cross-encoder | -0.038 [-0.110, +0.034], no clear difference | -0.017 [-0.050, +0.016], no clear difference | -0.050 [-0.161, +0.045], no clear difference |
| With EN cross-encoder | -0.047 [-0.113, +0.013], no clear difference | -0.057 [-0.094, -0.019], worse | +0.054 [+0.002, +0.117], better |
| Float instead of int8 | -0.007 [-0.026, +0.010], no clear difference | -0.005 [-0.013, +0.001], no clear difference | +0.002 [-0.006, +0.013], no clear difference |
| e5-small int8 | -0.050 [-0.097, -0.004], worse | -0.066 [-0.105, -0.029], worse | +0.070 [+0.009, +0.149], better |
| bge-small-en | -0.104 [-0.158, -0.052], worse | -0.095 [-0.141, -0.053], worse | +0.064 [+0.002, +0.144], better |
| all-MiniLM-L6-v2 | -0.129 [-0.189, -0.070], worse | -0.126 [-0.182, -0.077], worse | +0.022 [-0.034, +0.089], no clear difference |
<!-- /table:paired -->

## 2. Embedding models

All 384 dimensions, so an index costs the same whichever is chosen. RSS and query latency are from a process of its own in a
Linux container (`eval/footprint.py`).

<!-- table:models -->
| Model (hybrid + parser, weight 0.5) | Languages | Curated nDCG@10 | EN | DE | Rule | Known | Curated recall@20 | RSS MB | Query ms p50 | File MB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all-MiniLM-L6-v2 | EN | 0.627 [0.559, 0.693] | 0.686 | 0.523 | 0.760 | 0.884 | 0.644 | 202 | 3.8 | 90 |
| bge-small-en-v1.5 | EN | 0.652 [0.578, 0.722] | 0.737 | 0.501 | 0.791 | 0.925 | 0.694 | - | - | 133 |
| paraphrase-multilingual-MiniLM-L12-v2 | 50 | 0.749 [0.682, 0.811] | 0.779 | 0.696 | 0.881 | 0.864 | 0.916 | 823 | 7.6 | 470 |
| same, int8 | 50 | 0.756 [0.687, 0.818] | 0.781 | 0.711 | 0.886 | 0.861 | 0.909 | 489 | 3.7 | 118 |
| multilingual-e5-small, int8 | 94 | 0.706 [0.644, 0.763] | 0.729 | 0.665 | 0.820 | 0.931 | 0.794 | - | - | 118 |
<!-- /table:models -->

What the numbers decided: **German decides this.** The two English models are competitive on English and fall away on German
queries, and the product has to serve both languages. Between the two multilingual models,
`paraphrase-multilingual` is clearly ahead on vague and filter queries and ahead on recall, and `e5-small` is ahead on exact names.

`paraphrase-multilingual` was a sentence-similarity model, and its `max_seq_length` of 128 tokens truncates long descriptions.
That didn't stop it winning here, where descriptions are short. It should be re-checked against longer real text.

## Coverage: the tours the reviewed set misses

**Not reviewed: Claude's labels, marked `needs_review`, in `eval/curated_coverage.py` so that the signed-off 50 stay as signed.
These numbers decide nothing, and aren't in the CI gate.**

The curated set labels 82 of the 105 written tours, so a system could return anything for the other 23 without the evaluation
noticing. These six EN/DE pairs reach 20 of them. "Tigerfish on the Zambezi", "Photo boat with swivel seats" and "Stone church
and a railway past" are still unlabelled: six natural queries can't take in everything.

<!-- table:coverage -->
| System | nDCG@10 | EN | DE | Recall@20 |
| --- | --- | --- | --- | --- |
| Lexical only | 0.156 [0.080, 0.243] | 0.198 | 0.114 | 0.124 |
| Dense only | 0.503 [0.400, 0.600] | 0.522 | 0.484 | 0.621 |
| Hybrid, equal weights, k = 60 | 0.379 [0.257, 0.498] | 0.370 | 0.387 | 0.537 |
| Hybrid, equal weights, k = 10 | 0.405 [0.287, 0.516] | 0.411 | 0.399 | 0.553 |
| Hybrid, lexical weight 0.5 | 0.396 [0.268, 0.520] | 0.391 | 0.401 | 0.563 |
| Dense + parser | 0.451 [0.337, 0.554] | 0.545 | 0.356 | 0.588 |
| Hybrid + parser, equal weights | 0.337 [0.222, 0.456] | 0.370 | 0.303 | 0.511 |
| **Hybrid + parser, lexical weight 0.5 (chosen)** | 0.368 [0.248, 0.487] | 0.401 | 0.335 | 0.537 |
| ... + EN-DE cross-encoder | 0.281 [0.195, 0.375] | 0.298 | 0.263 | 0.451 |
| ... + EN cross-encoder | 0.261 [0.130, 0.407] | 0.377 | 0.145 | 0.425 |
| Float instead of int8 | 0.386 [0.267, 0.504] | 0.417 | 0.356 | 0.535 |
| e5-small int8 | 0.281 [0.179, 0.397] | 0.314 | 0.249 | 0.392 |
| bge-small-en | 0.249 [0.139, 0.370] | 0.343 | 0.154 | 0.337 |
| all-MiniLM-L6-v2 | 0.218 [0.109, 0.336] | 0.315 | 0.120 | 0.296 |
<!-- /table:coverage -->

**Every system scores much lower here than on the curated set,** and the chosen one reaches 0.368 against 0.756. Two things could
be behind that, and these labels can't separate them. The queries are harder: long, conversational sentences, one with a
negation ("not sitting in a safari car"), which the lexical list reads as a request for safaris. And the labels are unreviewed.

**Dense retrieval does better than the chosen hybrid here**, clearly so without the parser (+0.135) and not clearly with it (+0.083), the
same direction as on the curated set. If the owner's review confirms these labels, this is the next thing to look at, because it
suggests the lexical list costs more on conversational queries than the weight of 0.5 allows for. It doesn't overturn hybrid:
dense's weakness on exact names is unchanged.

## 3. Runtimes

Same model, in Linux containers on this machine, no CPU limit. `torch` ran in the previous image; `onnx` in the new one. RSS is
after embedding 100 documents. The torch row's load time for the chosen model included downloading its weights.

<!-- table:runtimes -->
| Model | Runtime | RSS MB | Query p50 ms | Query p95 ms | ms per document | Load s |
| --- | --- | --- | --- | --- | --- | --- |
| paraphrase-ml-int8 | onnx | 489 | 3.7 | 4.8 | 9.3 | 5.2 |
| paraphrase-ml | onnx | 823 | 7.6 | 11.6 | 18.7 | 10.7 |
| minilm-l6-int8 | onnx | 128 | 1.9 | 2.8 | 7.2 | 2.9 |
| minilm-l6 | onnx | 202 | 3.8 | 5.5 | 8.8 | 3.1 |
| minilm-l6 | torch | 532 | 10.5 | 14.5 | 17.8 | 14.7 |
| paraphrase-ml | torch | 901 | 18.0 | 21.6 | 47.9 | download included |
<!-- /table:runtimes -->

| | Before: torch | After: ONNX int8 |
| --- | --- | --- |
| Image | 2.16 GB | 615 MB |

Vector agreement on the chosen model, as mean cosine over test queries: ONNX float against torch **1.0000**, int8 against float
**0.9979**.

What the numbers decided:

- **ONNX float and torch produce the same vectors,** so they have the same quality by identity. The difference is purely cost:
  ONNX is about twice as fast per query and uses a little less memory. Torch left the runtime image and CI.
- **int8 costs no measurable quality** (no clear difference on any set in the table above), halves latency again and
  nearly halves memory against float, so it is the one that runs.
- **Most of the chosen model's memory isn't the network.** The int8 file is 118 MB and the process holds about 490 MB, against
  about 130 MB for MiniLM int8. A smaller English model would save 360 MB and lose German: not worth it, but it is the first
  thing to revisit if memory gets tight.

The file used is `model_qint8_avx512_vnni.onnx`, the only int8 export some of these models publish. It runs on any x86-64 CPU
and fastest on those with VNNI. A CPU without VNNI, or an ARM server, would see different latencies: not measured.

## 4. Memory of the `ai` profile

`docker stats`, idle, 3 minutes after start, on this laptop, with 300 tours embedded. "Before" is torch with MiniLM, from
[the earlier budget](notifications.md#memory-budget).

| Container | Before: torch, MiniLM | After: ONNX int8, chosen model | With the EN-DE cross-encoder |
| --- | --- | --- | --- |
| ai-service | 409 MiB | 575 MiB | 811 MiB loaded, 1,007 after five searches. **Killed at the 768 MiB limit** |
| ai-worker | 443 MiB | 536 MiB | no reranker |
| ai-db | 36 MiB | 66 MiB | 66 MiB |
| **`ai` adds** | **about 890 MiB** | **about 1,180 MiB** | about 1,610 MiB |
| Container limit, each | 1,536 MiB | 768 MiB | needs more than 1 GiB |
| Whole stack, default + `ai` | 2,416 MiB | 2,242 MiB | not sustained |

**The `ai` profile grew by about 290 MiB. That is the bigger multilingual model, and it still fits.** The same model on torch would be
about 900 MB per process, and MiniLM int8 about 130 MB, which is what German queries cost. The whole-stack total fell only because
other containers' idle figures vary between runs, so don't read it as a saving. The new memory limits are half the old, with about 190
MiB of headroom on the service. A migration holds two models in the worker until cutover, so it needs its limit raised first (see
[the runbook](../runbooks/embedding-model-migration.md)).

Which profiles to combine on an 8 GB laptop, from [the budget](notifications.md#memory-budget), with `ai` at its new size:

| Demo | Profiles | About |
| --- | --- | --- |
| Search, itineraries | `ai` | 2.2 GiB |
| AI with traces and the GenAI panel | `ai`, `observability` (a sum, not measured together), not for long | 3.2 GiB |
| Emails and tickets | `notifications`, without `ai` | 2.4 GiB |
| Everything at once | Not on this laptop | |

## Reproduce

```bash
cd ai-recommendation-service
export DATABASE_URL=postgresql+psycopg://...   # a migrated database with pgvector and PostGIS
export HF_HUB_DISABLE_XET=1                    # the Xet transport stalled here at no bytes for 20 minutes; plain HTTP didn't
python -m eval.run_eval --models minilm-l6 bge-small-en paraphrase-ml paraphrase-ml-int8 e5-small-int8 \
    --rrf-k 10 60 --lexical-weights 0.5 --rerankers ms-marco-l6-int8 en-de-l6-int8 --out eval/results/models.json
python -m eval.footprint --model paraphrase-ml-int8 --runtime onnx
python -m eval.write_reports
```

CI runs `python -m eval.regression` on the reviewed curated set against `eval/baseline.json`, with a cached ONNX model and no
paid API, and fails if nDCG@10 or recall@20 falls by more than 0.005. The coverage set isn't part of it.

`eval/results/curated_before_review.json` holds the scores from before the owner's review, with that run's latencies. It is a
fixed record, and no command writes it.
