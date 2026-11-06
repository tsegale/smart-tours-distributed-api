# Search quality: which retrieval, which model, which runtime

Every choice in [ADR 0019](../adr/0019-hybrid-retrieval-with-rrf-in-one-sql-statement.md) and
[ADR 0020](../adr/0020-embedding-model-and-runtime-by-evaluation.md) rests on the tables here. They are written from
`ai-recommendation-service/eval/results/` by `python -m eval.write_reports`, so nothing is typed by hand.

**Decision: `paraphrase-multilingual-MiniLM-L12-v2`, U8U8 (`model_quint8_avx2.onnx`), on ONNX Runtime. Hybrid retrieval with query understanding and a
lexical weight of 0.5, `k` = 60. No cross-encoder.** The owner's review of the labels changed none of this
([before and after](#the-owners-review-before-and-after)). A
[bug that depended on the CPU](#does-the-score-depend-on-the-cpu) did change the quantised file.

## How to read this

**The corpus is synthetic.** `datasets/tours_v1`: 2,505 tours, 105 of them individually written and 2,400 composed from 172
sentences ([datasheet](../datasets.md)). **Nothing here has met a real user.** Four query sets, chosen because they fail in
different ways. They are reported apart, because a single average would hide what decides this:

| Set | Queries | Corpus | Shape | Labels |
| --- | --- | --- | --- | --- |
| Curated | 50, EN and DE | 105 written tours | Vague descriptions, deliberately avoiding the tours' wording | Reviewed: proposed by Claude, revised in a second Claude pass, contested calls decided and the set signed off by the owner on 2026-11-06 |
| Rule | 106, EN and DE | 2,400 composed tours | Activity, area, price and family phrases | Computed from the tours' generating attributes |
| Known-item | 26, EN and DE | 105 written tours | One exact, rare word | Computed: 3 if the term is in the name, 2 if in the description |
| [Coverage](#coverage-the-tours-the-reviewed-set-misses) | 12, EN and DE | 105 written tours | Conversational, aimed at tours the curated set never labels | Reviewed the same way, and signed off by the owner on 2026-11-06. Part of every decision since 2026-11-07, and has its own lines in the CI gate |

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
went from 0.751 to 0.756. This section was measured on the int8 file that shipped then.

Each decision's comparison on the curated set, as a paired difference:

<!-- table:decisions -->
| Against the system chosen then | Before | After | Verdict changed |
| --- | --- | --- | --- |
| Lexical only | -0.290, worse | -0.297 [-0.374, -0.221], worse | no |
| Dense + parser | +0.041, no clear difference | +0.039 [-0.003, +0.082], no clear difference | no |
| Equal weights | -0.024, worse | -0.026 [-0.047, -0.009], worse | no |
| With EN-DE cross-encoder | -0.028, no clear difference | -0.038 [-0.110, +0.034], no clear difference | no |
| With EN cross-encoder | -0.036, no clear difference | -0.047 [-0.113, +0.013], no clear difference | no |
| Float instead of U8U8 | -0.006, no clear difference | -0.007 [-0.026, +0.010], no clear difference | no |
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
  verdict the review moved, and it moved towards the existing choice. The upper end of that interval is -0.004, so it is clear, but not by much, and on the U8U8 file that replaced int8 it is
  back to no clear difference (-0.046 [-0.094, +0.001]). Treat this verdict as close.
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

With the chosen model. nDCG@10 [95% CI]. Latency is on the curated set, from this model's own run on an otherwise idle laptop.

<!-- table:modes -->
| System | Curated: vague | Rule: filters | Known: exact name | Coverage | ms p50 / p95 |
| --- | --- | --- | --- | --- | --- |
| Lexical only | 0.459 [0.374, 0.549] | 0.476 [0.405, 0.550] | 0.876 [0.731, 0.984] | 0.196 [0.117, 0.274] | 5 / 8 |
| Dense only | 0.820 [0.752, 0.882] | 0.751 [0.698, 0.800] | 0.560 [0.398, 0.720] | 0.502 [0.413, 0.579] | 29 / 116 |
| Hybrid, equal weights, k = 60 | 0.755 [0.687, 0.812] | 0.705 [0.645, 0.761] | 0.900 [0.788, 0.988] | 0.419 [0.313, 0.517] | 29 / 49 |
| Hybrid, equal weights, k = 10 | 0.772 [0.711, 0.825] | 0.674 [0.618, 0.727] | 0.900 [0.788, 0.988] | 0.441 [0.337, 0.530] | 30 / 41 |
| Hybrid, lexical weight 0.5 | 0.773 [0.710, 0.826] | 0.759 [0.704, 0.810] | 0.864 [0.739, 0.963] | 0.447 [0.353, 0.537] | 22 / 27 |
| Dense + parser | 0.781 [0.705, 0.855] | 0.895 [0.851, 0.932] | 0.577 [0.421, 0.740] | 0.464 [0.351, 0.571] | 25 / 53 |
| Hybrid + parser, equal weights | 0.736 [0.664, 0.801] | 0.830 [0.777, 0.880] | 0.902 [0.801, 0.978] | 0.393 [0.290, 0.493] | 26 / 44 |
| **Hybrid + parser, lexical weight 0.5 (chosen)** | 0.752 [0.685, 0.812] | 0.880 [0.834, 0.918] | 0.878 [0.769, 0.970] | 0.417 [0.312, 0.516] | 27 / 53 |
| ... + EN-DE cross-encoder | 0.713 [0.639, 0.779] | 0.881 [0.840, 0.917] | 0.831 [0.692, 0.940] | 0.257 [0.195, 0.321] | 664 / 1116 |
| ... + EN cross-encoder | 0.698 [0.632, 0.759] | 0.836 [0.782, 0.886] | 0.915 [0.816, 0.984] | 0.306 [0.174, 0.449] | 972 / 1407 |
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
- **Cross-encoders are off.** On this model they add no clear gain anywhere, are clearly worse on coverage (-0.161 and -0.111), and
  add at least 600 ms at p50 on this laptop against about 27 ms. They did help the weaker models (next section's comparison was run with them too, in `models.json`), which is
  why this took measuring rather than assuming. The code stays behind `RERANK_MODEL`, so that a better cross-encoder can be
  tried with one setting.

**The fourth set changed no decision.** With coverage reported as a decision set, every choice stands. Its verdicts all favour what
is chosen, or show no clear difference: the cross-encoders, `e5-small`, bge-small, MiniLM, equal weights and lexical alone are all clearly worse on it. Plain dense without the parser is clearly ahead on it, as it is on the curated set, and [still loses on filters and names](#does-dense-really-lead-here).

Each alternative against the chosen system, as a paired difference in nDCG@10:

<!-- table:paired -->
| Against the chosen system | Curated | Rule | Known | Coverage |
| --- | --- | --- | --- | --- |
| Lexical only | -0.293 [-0.366, -0.220], worse | -0.404 [-0.479, -0.329], worse | -0.002 [-0.127, +0.130], no clear difference | -0.221 [-0.288, -0.153], worse |
| Dense + parser | +0.029 [-0.020, +0.079], no clear difference | +0.015 [-0.019, +0.050], no clear difference | -0.301 [-0.449, -0.160], worse | +0.047 [-0.032, +0.140], no clear difference |
| Equal weights | -0.016 [-0.033, -0.003], worse | -0.050 [-0.083, -0.021], worse | +0.024 [+0.000, +0.071], no clear difference | -0.024 [-0.050, -0.002], worse |
| With EN-DE cross-encoder | -0.039 [-0.114, +0.033], no clear difference | +0.001 [-0.031, +0.035], no clear difference | -0.047 [-0.152, +0.031], no clear difference | -0.161 [-0.274, -0.027], worse |
| With EN cross-encoder | -0.054 [-0.117, +0.006], no clear difference | -0.044 [-0.081, -0.008], worse | +0.037 [+0.000, +0.097], no clear difference | -0.111 [-0.222, -0.009], worse |
| Float instead of U8U8 | -0.003 [-0.015, +0.009], no clear difference | +0.001 [-0.009, +0.009], no clear difference | -0.015 [-0.049, +0.005], no clear difference | +0.013 [-0.015, +0.040], no clear difference |
| The previous int8 file, on a VNNI CPU | +0.004 [-0.007, +0.018], no clear difference | +0.006 [-0.001, +0.014], no clear difference | -0.017 [-0.048, +0.000], no clear difference | -0.002 [-0.031, +0.021], no clear difference |
| e5-small int8 | -0.046 [-0.094, +0.001], no clear difference | -0.060 [-0.098, -0.023], worse | +0.053 [-0.003, +0.123], no clear difference | -0.103 [-0.182, -0.020], worse |
| bge-small-en | -0.100 [-0.150, -0.049], worse | -0.089 [-0.136, -0.046], worse | +0.047 [-0.007, +0.120], no clear difference | -0.138 [-0.227, -0.052], worse |
| all-MiniLM-L6-v2 | -0.124 [-0.182, -0.068], worse | -0.120 [-0.176, -0.070], worse | +0.005 [-0.045, +0.068], no clear difference | -0.167 [-0.256, -0.082], worse |
<!-- /table:paired -->

## 2. Embedding models

All 384 dimensions, so an index costs the same whichever is chosen. RSS and query latency are from a process of its own in a
Linux container (`eval/footprint.py`).

<!-- table:models -->
| Model (hybrid + parser, weight 0.5) | Languages | Curated nDCG@10 | EN | DE | Rule | Known | Coverage | Curated recall@20 | RSS MB | Query ms p50 | File MB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all-MiniLM-L6-v2 | EN | 0.627 [0.559, 0.693] | 0.686 | 0.523 | 0.760 | 0.884 | 0.250 | 0.644 | 202 | 3.8 | 90 |
| bge-small-en-v1.5 | EN | 0.652 [0.578, 0.722] | 0.737 | 0.501 | 0.791 | 0.925 | 0.279 | 0.694 | - | - | 133 |
| paraphrase-multilingual-MiniLM-L12-v2 | 50 | 0.749 [0.682, 0.811] | 0.779 | 0.696 | 0.881 | 0.864 | 0.430 | 0.916 | 823 | 7.6 | 470 |
| same, previous int8 file | 50 | 0.756 [0.687, 0.818] | 0.781 | 0.711 | 0.886 | 0.861 | 0.416 | 0.909 | 489 | 3.7 | 118 |
| same, U8U8 (shipped) | 50 | 0.752 [0.685, 0.812] | 0.781 | 0.699 | 0.880 | 0.878 | 0.417 | 0.926 | 490 | 8.4 | 118 |
| multilingual-e5-small, int8 | 94 | 0.706 [0.644, 0.763] | 0.729 | 0.665 | 0.820 | 0.931 | 0.314 | 0.794 | - | - | 118 |
<!-- /table:models -->

What the numbers decided: **German decides this.** The two English models are competitive on English and fall away on German
queries, and the product has to serve both languages. Between the two multilingual models,
`paraphrase-multilingual` is clearly ahead on filter and coverage queries and ahead on recall. On vague queries and exact names the gap to
`e5-small` is within noise, and both verdicts have moved with the labels and the quantised file.

`paraphrase-multilingual` was a sentence-similarity model, and its `max_seq_length` of 128 tokens truncates long descriptions.
That didn't stop it winning here, where descriptions are short. It should be re-checked against longer real text.

## Coverage: the tours the reviewed set misses

The curated set labels 82 of the 105 written tours, so a system could return anything for the other 23 without the evaluation
noticing. These six EN/DE pairs reach 20 of them. "Tigerfish on the Zambezi", "Photo boat with swivel seats" and "Stone church
and a railway past" are still unlabelled. Claude proposed the labels before seeing any result, a second pass took them from 54 to
73, and the owner decided the contested calls and signed the set off on 2026-11-06 (`eval/curated_coverage.py`).

<!-- table:coverage -->
| System | nDCG@10 | EN | DE | Recall@20 |
| --- | --- | --- | --- | --- |
| Lexical only | 0.196 [0.117, 0.274] | 0.260 | 0.132 | 0.131 |
| Dense only | 0.502 [0.413, 0.579] | 0.572 | 0.432 | 0.559 |
| Hybrid, equal weights, k = 60 | 0.419 [0.313, 0.517] | 0.458 | 0.380 | 0.504 |
| Hybrid, equal weights, k = 10 | 0.441 [0.337, 0.530] | 0.492 | 0.389 | 0.522 |
| Hybrid, lexical weight 0.5 | 0.447 [0.353, 0.537] | 0.478 | 0.415 | 0.530 |
| Dense + parser | 0.464 [0.351, 0.571] | 0.597 | 0.330 | 0.539 |
| Hybrid + parser, equal weights | 0.393 [0.290, 0.493] | 0.458 | 0.328 | 0.474 |
| **Hybrid + parser, lexical weight 0.5 (chosen)** | 0.417 [0.312, 0.516] | 0.478 | 0.356 | 0.500 |
| ... + EN-DE cross-encoder | 0.257 [0.195, 0.321] | 0.275 | 0.238 | 0.394 |
| ... + EN cross-encoder | 0.306 [0.174, 0.449] | 0.445 | 0.167 | 0.371 |
| Float instead of U8U8 | 0.430 [0.317, 0.540] | 0.477 | 0.384 | 0.492 |
| The previous int8 file, on a VNNI CPU | 0.416 [0.301, 0.527] | 0.467 | 0.364 | 0.492 |
| e5-small int8 | 0.314 [0.208, 0.430] | 0.364 | 0.264 | 0.346 |
| bge-small-en | 0.279 [0.172, 0.400] | 0.390 | 0.169 | 0.335 |
| all-MiniLM-L6-v2 | 0.250 [0.144, 0.366] | 0.355 | 0.145 | 0.287 |
<!-- /table:coverage -->

Every system before and after the owner's review, on the int8 file both runs used:

<!-- table:coverage-review -->
| System | Before | After [95% CI] | Change |
| --- | --- | --- | --- |
| lexical | 0.156 | 0.196 [0.117, 0.274] | +0.040 |
| dense:minilm-l6 | 0.284 | 0.291 [0.147, 0.440] | +0.008 |
| hybrid:minilm-l6:k10 | 0.227 | 0.264 [0.164, 0.373] | +0.037 |
| hybrid:minilm-l6:k60 | 0.205 | 0.241 [0.140, 0.357] | +0.037 |
| hybrid:minilm-l6:w0.5 | 0.218 | 0.250 [0.144, 0.366] | +0.032 |
| hybrid+parser:minilm-l6 | 0.205 | 0.241 [0.140, 0.357] | +0.037 |
| dense+parser:minilm-l6 | 0.284 | 0.291 [0.147, 0.440] | +0.008 |
| hybrid+parser:minilm-l6:w0.5 | 0.218 | 0.250 [0.144, 0.366] | +0.032 |
| hybrid+parser+rerank:minilm-l6:ms-marco-l6-int8 | 0.264 | 0.275 [0.149, 0.419] | +0.011 |
| hybrid+parser+rerank:minilm-l6:en-de-l6-int8 | 0.277 | 0.281 [0.176, 0.380] | +0.004 |
| dense:bge-small-en | 0.316 | 0.330 [0.206, 0.458] | +0.014 |
| hybrid:bge-small-en:k10 | 0.274 | 0.308 [0.195, 0.432] | +0.034 |
| hybrid:bge-small-en:k60 | 0.222 | 0.258 [0.167, 0.365] | +0.036 |
| hybrid:bge-small-en:w0.5 | 0.249 | 0.279 [0.172, 0.400] | +0.031 |
| hybrid+parser:bge-small-en | 0.222 | 0.258 [0.167, 0.365] | +0.036 |
| dense+parser:bge-small-en | 0.316 | 0.330 [0.206, 0.458] | +0.014 |
| hybrid+parser:bge-small-en:w0.5 | 0.249 | 0.279 [0.172, 0.400] | +0.031 |
| hybrid+parser+rerank:bge-small-en:ms-marco-l6-int8 | 0.261 | 0.279 [0.148, 0.423] | +0.018 |
| hybrid+parser+rerank:bge-small-en:en-de-l6-int8 | 0.273 | 0.294 [0.214, 0.377] | +0.021 |
| dense:paraphrase-ml | 0.520 | 0.513 [0.405, 0.619] | -0.007 |
| hybrid:paraphrase-ml:k10 | 0.415 | 0.451 [0.329, 0.575] | +0.036 |
| hybrid:paraphrase-ml:k60 | 0.387 | 0.430 [0.308, 0.552] | +0.043 |
| hybrid:paraphrase-ml:w0.5 | 0.407 | 0.446 [0.329, 0.563] | +0.039 |
| hybrid+parser:paraphrase-ml | 0.369 | 0.416 [0.300, 0.532] | +0.047 |
| dense+parser:paraphrase-ml | 0.463 | 0.467 [0.341, 0.584] | +0.004 |
| hybrid+parser:paraphrase-ml:w0.5 | 0.386 | 0.430 [0.317, 0.540] | +0.044 |
| hybrid+parser+rerank:paraphrase-ml:ms-marco-l6-int8 | 0.257 | 0.275 [0.145, 0.423] | +0.018 |
| hybrid+parser+rerank:paraphrase-ml:en-de-l6-int8 | 0.266 | 0.273 [0.187, 0.367] | +0.007 |
| dense:paraphrase-ml-int8 | 0.503 | 0.487 [0.390, 0.573] | -0.016 |
| hybrid:paraphrase-ml-int8:k10 | 0.405 | 0.436 [0.324, 0.537] | +0.031 |
| hybrid:paraphrase-ml-int8:k60 | 0.379 | 0.410 [0.298, 0.514] | +0.032 |
| hybrid:paraphrase-ml-int8:w0.5 | 0.396 | 0.434 [0.312, 0.547] | +0.038 |
| hybrid+parser:paraphrase-ml-int8 | 0.337 | 0.378 [0.269, 0.487] | +0.042 |
| dense+parser:paraphrase-ml-int8 | 0.451 | 0.451 [0.337, 0.554] | +0.001 |
| hybrid+parser:paraphrase-ml-int8:w0.5 | 0.368 | 0.416 [0.301, 0.527] | +0.048 |
| hybrid+parser+rerank:paraphrase-ml-int8:ms-marco-l6-int8 | 0.261 | 0.280 [0.149, 0.425] | +0.019 |
| hybrid+parser+rerank:paraphrase-ml-int8:en-de-l6-int8 | 0.281 | 0.292 [0.209, 0.383] | +0.011 |
| dense:e5-small-int8 | 0.345 | 0.339 [0.231, 0.440] | -0.005 |
| hybrid:e5-small-int8:k10 | 0.276 | 0.318 [0.212, 0.427] | +0.042 |
| hybrid:e5-small-int8:k60 | 0.271 | 0.313 [0.207, 0.424] | +0.042 |
| hybrid:e5-small-int8:w0.5 | 0.282 | 0.316 [0.205, 0.430] | +0.034 |
| hybrid+parser:e5-small-int8 | 0.273 | 0.312 [0.205, 0.426] | +0.039 |
| dense+parser:e5-small-int8 | 0.348 | 0.347 [0.241, 0.449] | -0.001 |
| hybrid+parser:e5-small-int8:w0.5 | 0.281 | 0.314 [0.208, 0.430] | +0.033 |
| hybrid+parser+rerank:e5-small-int8:ms-marco-l6-int8 | 0.268 | 0.277 [0.152, 0.421] | +0.010 |
| hybrid+parser+rerank:e5-small-int8:en-de-l6-int8 | 0.272 | 0.282 [0.174, 0.392] | +0.010 |
<!-- /table:coverage-review -->

### Does dense really lead here?

An earlier run on the unreviewed labels had dense at 0.503 against 0.368 for the chosen system, and this section called that a clear lead.
**With the owner's labels, the direction holds and the size roughly halves. Whether it is clear depends on which 8-bit file ran, which is a reason not to lean on it:**

<!-- table:coverage-gap -->
| Minus the chosen system | Coverage, Claude's labels, int8 | Coverage, owner's labels, int8 | Coverage, owner's labels, U8U8 | The 50 reviewed queries, U8U8 |
| --- | --- | --- | --- | --- |
| Dense, no parser | +0.135 [+0.029, +0.240], better | +0.071 [-0.012, +0.150], no clear difference | +0.085 [+0.020, +0.165], better | +0.068 [+0.016, +0.123], better |
| Dense + parser | +0.083 [-0.026, +0.207], no clear difference | +0.036 [-0.056, +0.139], no clear difference | +0.047 [-0.032, +0.140], no clear difference | +0.029 [-0.020, +0.079], no clear difference |
<!-- /table:coverage-gap -->

**It isn't special to this set.** Dense sits about the same distance ahead on the 50 reviewed queries. Both sets show the same small pull
from the lexical list. Coverage scores far lower for a different reason: dense falls just as far (0.464 against 0.781), so the queries
are harder, whatever the retrieval. Plain dense isn't an option anyway: it loses 0.129 on filter queries and 0.318 on exact names.

### Is it the negated wording?

**Mostly not, and this section's earlier example was wrong.** It blamed "safari" in "not sitting in a safari car". That word never pushed a tour in: the words that did were
`sit`, `car` and `wild`.

- **Negated queries aren't the main loss.** On this set the gap is +0.030 on the 4 negated queries and +0.055 on the 8 others.
  One curated query does lose a lot to it ("strange trees at sunset, not much walking", +0.234), but that is one query.
- **Removing the negation gives no clear gain**: coverage +0.027 [0.000, +0.067], curated +0.003.
- **Length alone isn't it either.** Across all 62 queries, the correlation between length and dense's lead is -0.002, and the lead doesn't grow with length.
- **What it is: one stray common word is enough to get in.** The lexical list ORs every term, in both languages' stemmers. So `walk`, `view` and `feel` push eight tours graded 0 into "strenuous walk uphill", and
  `water` and `stori` push in seven for "terrifying in the water". Tours it pushed in have a mean grade of 0.15, and those they pushed out 0.57. The two stemmers add to it: `without`
  survives German's stopword list, and `off` comes from English stemming of the German "offene".

<!-- table:coverage-queries -->
| Query | Words | Negated | Chosen | Dense + parser | Lexical's effect | Lexical alone | Grades it pushed in | Grades it pushed out | Words that matched |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| easy evening with a drink watching the sun go down over huge scenery | 13 |  | 0.665 | 0.643 | +0.022 | 0.359 | 0, 0, 0, 0 | 0, 0, 1, 0 | drink, even, watch |
| entspannter Abend mit einem Drink, während die Sonne über einer riesigen Landschaft untergeht | 13 |  | 0.459 | 0.374 | +0.085 | 0.213 | 0, 0, 0 | 0, 1, 0 | drink, riesig, sonn |
| strenuous walk uphill to a big view, I want to feel it in my legs | 15 |  | 0.219 | 0.651 | -0.432 | 0.170 | 0, 0, 0, 0, 0, 0, 0, 0 | 0, 3, 1, 2, 0, 1, 0, 3 | feel, view, walk |
| anstrengender Aufstieg zu Fuß mit weitem Ausblick, ich will es in den Beinen spüren | 14 |  | 0.496 | 0.366 | +0.130 | 0.368 | 0, 0 | 0, 1 | fuss, spur |
| weird rock shapes and geology without anything physically demanding | 9 | yes | 0.615 | 0.608 | +0.007 | 0.415 | 1, 0, 0, 0 | 0, 1, 0, 0 | rock, shape, without |
| bizarre Felsformationen und Geologie, ohne dass es körperlich anstrengend wird | 10 | yes | 0.606 | 0.520 | +0.086 | 0.212 |  |  |  |
| something truly terrifying in the water, so I have a story to tell | 13 |  | 0.517 | 0.786 | -0.269 | 0.144 | 0, 0, 0, 0, 0, 0, 0 | 0, 3, 0, 0, 0, 0, 2 | stori, water |
| etwas richtig Furchteinflößendes im Wasser, damit ich etwas zu erzählen habe | 11 |  | 0.353 | 0.378 | -0.025 | 0.000 | 0 | 0 | erzahl |
| all-day game drive through wide open country | 8 |  | 0.483 | 0.432 | +0.050 | 0.336 | 0, 1, 1, 0, 0, 0, 0, 0 | 0, 0, 0, 0, 0, 0, 0, 0 | countri, day, drive, open, wide |
| Pirschfahrt den ganzen Tag durch offene, weite Landschaft | 8 |  | 0.000 | 0.000 | +0.000 | 0.000 | 0, 0 | 0, 0 | off, tag |
| exploring the wild under my own power, not sitting in a safari car | 13 | yes | 0.371 | 0.463 | -0.092 | 0.138 | 2, 0, 2, 0, 0 | 3, 2, 0, 0, 0 | car, sit, wild |
| die Wildnis aus eigener Kraft erkunden, statt im Safariwagen zu sitzen | 11 | yes | 0.223 | 0.345 | -0.122 | 0.000 | 0, 0 | 2, 0 | sitz |
<!-- /table:coverage-queries -->

<!-- table:coverage-groups -->
| Queries | n | Mean words | Chosen | Dense + parser | Dense minus chosen | Tours lexical pushed in | Their mean grade | Mean grade of those pushed out |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coverage | 12 | 11.5 | 0.417 | 0.464 | +0.047 | 46 | 0.15 | 0.57 |
| curated | 50 | 5.5 | 0.752 | 0.781 | +0.029 | 115 | 0.10 | 0.15 |
| negated | 5 | 10.0 | 0.488 | 0.559 | +0.071 | 15 | 0.40 | 0.80 |
| negated, coverage | 4 | 10.8 | 0.454 | 0.484 | +0.030 | 11 | 0.45 | 0.73 |
| negated, curated | 1 | 7.0 | 0.625 | 0.858 | +0.234 | 4 | 0.25 | 1.00 |
| not negated, coverage | 8 | 11.9 | 0.399 | 0.454 | +0.055 | 35 | 0.06 | 0.51 |
| under 6 words | 27 | 4.1 | 0.724 | 0.769 | +0.045 | 38 | 0.03 | 0.16 |
| 6 to 8 words | 22 | 6.9 | 0.742 | 0.753 | +0.011 | 75 | 0.16 | 0.15 |
| 9 words or more | 13 | 11.5 | 0.517 | 0.558 | +0.042 | 48 | 0.12 | 0.54 |
<!-- /table:coverage-groups -->

### Would anything fix it?

Each candidate against the chosen system, on every set, because a fix for sentences could easily cost names. **None of these is shipped.** "Long titles" is a whole tour name of 6 words or more, pasted in with
only that tour labelled: the case a length-based rule could break, and one the known-item set doesn't test.

<!-- table:coverage-candidates -->
| Candidate | Curated | Coverage | Rule | Known | Long titles |
| --- | --- | --- | --- | --- | --- |
| chosen | 0.752 | 0.417 | 0.880 | 0.878 | 0.903 |
| dense + parser (no lexical list) | 0.781, +0.029 [-0.020, +0.079], no clear difference | 0.464, +0.047 [-0.032, +0.140], no clear difference | 0.895, +0.015 [-0.019, +0.050], no clear difference | 0.577, -0.301 [-0.449, -0.160], worse | 0.879, -0.024 [-0.060, +0.000], no clear difference |
| dense, no parser | 0.820, +0.068 [+0.016, +0.123], better | 0.502, +0.085 [+0.020, +0.165], better | 0.751, -0.129 [-0.188, -0.069], worse | 0.560, -0.318 [-0.486, -0.136], worse | 0.976, +0.073 [-0.024, +0.194], no clear difference |
| weight 0.1 | 0.800, +0.049 [+0.025, +0.075], better | 0.498, +0.081 [+0.033, +0.140], better | 0.895, +0.015 [-0.014, +0.044], no clear difference | 0.689, -0.190 [-0.333, -0.067], worse | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| weight 0.25 | 0.780, +0.028 [+0.015, +0.045], better | 0.481, +0.064 [+0.028, +0.106], better | 0.891, +0.011 [-0.008, +0.031], no clear difference | 0.771, -0.108 [-0.232, -0.009], worse | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| weight 0.75 | 0.734, -0.017 [-0.034, -0.005], worse | 0.412, -0.006 [-0.016, +0.003], no clear difference | 0.867, -0.013 [-0.031, +0.002], no clear difference | 0.891, +0.013 [+0.000, +0.039], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| weight 1.0 | 0.736, -0.016 [-0.033, -0.003], worse | 0.393, -0.024 [-0.050, -0.002], worse | 0.830, -0.050 [-0.083, -0.021], worse | 0.902, +0.024 [+0.000, +0.071], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| 0.1 from 6 words | 0.770, +0.019 [+0.002, +0.036], better | 0.498, +0.081 [+0.033, +0.140], better | 0.891, +0.011 [-0.008, +0.033], no clear difference | 0.878, +0.000 [+0.000, +0.000], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| 0.1 from 8 words | 0.758, +0.007 [-0.005, +0.020], no clear difference | 0.498, +0.081 [+0.033, +0.140], better | 0.876, -0.004 [-0.018, +0.008], no clear difference | 0.878, +0.000 [+0.000, +0.000], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| 0.25 from 8 words | 0.758, +0.007 [+0.000, +0.016], better | 0.481, +0.064 [+0.028, +0.106], better | 0.880, +0.000 [-0.008, +0.007], no clear difference | 0.878, +0.000 [+0.000, +0.000], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| negation stripped, lexical only | 0.755, +0.003 [+0.000, +0.010], no clear difference | 0.444, +0.027 [+0.000, +0.067], no clear difference | 0.880, +0.000 [+0.000, +0.000], no clear difference | 0.878, +0.000 [+0.000, +0.000], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| negation stripped, both lists | 0.755, +0.003 [+0.000, +0.010], no clear difference | 0.433, +0.016 [-0.004, +0.046], no clear difference | 0.880, +0.000 [+0.000, +0.000], no clear difference | 0.878, +0.000 [+0.000, +0.000], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
| negation stripped + 0.1 from 8 words | 0.762, +0.010 [-0.003, +0.025], no clear difference | 0.499, +0.081 [+0.033, +0.140], better | 0.876, -0.004 [-0.018, +0.008], no clear difference | 0.878, +0.000 [+0.000, +0.000], no clear difference | 0.903, +0.000 [+0.000, +0.000], no clear difference |
<!-- /table:coverage-candidates -->

### What was recommended, and where it stands

- **Fold the 12 into the decision set: done on 2026-11-07, as a fourth reported set, not averaged in.** Their shape is different (11.5 words on average, against 5.5), and an average would hide that. Checked with all 62
  queries together: no comparison changes direction, and the only verdicts that move are the EN cross-encoder and `e5-small`, both becoming clearly worse, which favours what is already chosen.
- **Add them to the CI gate: done, as their own lines.** Their spread on the gate's own system is 0.0047 at most ([measured](#the-gates-lines-and-the-cross-encoders)), inside the tolerance.
- **Negation handling: no.** No clear gain, on five queries, for a rule that would need maintaining in two languages.
- **RRF weight: promising, but not yet.** `0.1 from 6 words` is the best candidate: clearly better on curated (+0.019) and coverage (+0.081), no clear difference on filters, and unchanged on exact names and long titles.
  A flat 0.1 or 0.25 is ruled out by its clear loss on exact names. But the threshold was picked after looking at these same queries, with coverage providing most of the gain. Test it on queries written afterwards, ideally real ones, before shipping it.
- **Being tested:** that rule, one stemmer chosen by the query's language, and a minimum share of matching query terms, on 24 fresh queries in `eval/heldout.py`. They were committed before any system ran on them, and their labels await the owner's review. Nothing here is shipped.

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
| paraphrase-ml-uint8 | onnx | 490 | 8.4 | 16.8 | 24.8 | 5.3 |
<!-- /table:runtimes -->

| | Before: torch | After: ONNX int8 |
| --- | --- | --- |
| Image | 2.16 GB | 615 MB |

Vector agreement on the chosen model, as mean cosine over test queries: ONNX float against torch **1.0000**, int8 against float
**0.9979**.

What the numbers decided:

- **ONNX float and torch produce the same vectors,** so they have the same quality by identity. The difference is purely cost:
  ONNX is about twice as fast per query and uses a little less memory. Torch left the runtime image and CI.
- **8-bit costs no measurable quality** (no clear difference on any set in the table above) and
  nearly halves memory against float, so an 8-bit file is what runs. Which one took [more measuring](#does-the-score-depend-on-the-cpu).
  This table's `paraphrase-ml-uint8` latency came from a busier session, so compare 8-bit files using that section's survey instead.
- **Most of the chosen model's memory isn't the network.** The file is 118 MB and the process holds about 490 MB, against
  about 130 MB for MiniLM int8. A smaller English model would save 360 MB and lose German: not worth it, but it is the first
  thing to revisit if memory gets tight.

An ARM server, or an x86 CPU without AVX2, would see different latencies and possibly different quality: not measured. Upstream's `model_qint8_arm64.onnx` is byte-identical to the file that lost quality on x86 without VNNI, so it can't be assumed safe either ([ADR 0020](../adr/0020-embedding-model-and-runtime-by-evaluation.md)).

## Does the score depend on the CPU?

**Yes, for the first int8 file, and that was a quality bug. It is fixed.** The CI gate gave two answers on one commit
([issue #10](https://github.com/tsegale/smart-tours-distributed-api/issues/10)). `.github/workflows/int8-cpu-survey.yml` then ran each model on
36 hosted runner jobs, in three samples, logging each CPU's flags. They landed 18 with VNNI and 18 without, on five CPU models.

<!-- table:drift -->
| Variant | Chosen system, VNNI | Chosen system, no VNNI | Gate, VNNI | Gate, no VNNI | Cosine across CPUs, mean / min | Worst cosine to float, no VNNI | Against float, VNNI | Against float, no VNNI | Query ms p50, VNNI / no VNNI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paraphrase-ml | 0.7490 | 0.7490 | 0.7687 | 0.7687 | 1.00000 / 1.00000 | 1.0000 | +0.000 [+0.000, +0.000], no clear difference | +0.000 [+0.000, +0.000], no clear difference | 4.0 / 5.2 |
| paraphrase-ml-int8 | 0.7557 | 0.7399 | 0.7635 | 0.7558 | 0.98143 / 0.87907 | 0.8821 | +0.007 [-0.010, +0.026], no clear difference | -0.009 [-0.026, +0.007], no clear difference | 2.1 / 3.6 |
| paraphrase-ml-uint8 | 0.7516 | 0.7511 | 0.7725 | 0.7677 | 1.00000 / 1.00000 | 0.9512 | +0.003 [-0.008, +0.015], no clear difference | +0.002 [-0.011, +0.016], no clear difference | 2.5 / 4.0 |
| qint8-rr | 0.7363 | 0.7378 | 0.7445 | 0.7509 | 0.99996 / 0.99772 | 0.9256 | -0.013 [-0.038, +0.013], no clear difference | -0.011 [-0.037, +0.015], no clear difference | 1.8 / 3.2 |
| qint8-rr-pc | 0.7416 | 0.7409 | 0.7673 | 0.7657 | 1.00000 / 1.00000 | 0.9529 | -0.007 [-0.024, +0.010], no clear difference | -0.008 [-0.030, +0.012], no clear difference | 2.0 / 3.6 |
| quint8 | 0.7423 | 0.7410 | 0.7606 | 0.7650 | 0.99999 / 0.99945 | 0.9885 | -0.007 [-0.020, +0.006], no clear difference | -0.008 [-0.022, +0.006], no clear difference | 2.7 / 3.6 |
| quint8-pc | 0.7475 | 0.7450 | 0.7600 | 0.7583 | 1.00000 / 1.00000 | 0.9884 | -0.001 [-0.016, +0.014], no clear difference | -0.004 [-0.019, +0.012], no clear difference | 2.5 / 3.9 |
<!-- /table:drift -->

- **Scores split exactly on VNNI, and the side without it is lower on the chosen system.** Every job on one side of the flag agreed with every other,
  and float was the same everywhere. One AMD model appears on both sides, with the flag hidden on some hosts, so it follows
  the instruction and not the chip. A worst-query cosine of 0.88 to float is what saturation in the U8S8 kernels would look like; this
  measures the behaviour, not the kernels themselves.
- **It wasn't lower on everything.** Without VNNI, the old file did slightly better on coverage (0.4270 against 0.4155). The vectors were simply worse: these
  query sets are small enough for that to go either way.
- **`paraphrase-ml-uint8` replaces it.** All six 8-bit files, the old one included, stay within float's interval on both CPU types, so the choice was on agreement
  and on what it takes to ship. It has the highest score on the chosen system, the smallest spread there, and needs nothing built or hosted, since upstream publishes it.
  Against that, it isn't the closest to float in vector terms (worst query 0.951, against 0.989 for a plain U8U8 build), and the gate's own system still differs
  by 0.0048 between CPUs although query vectors agree to five decimals: most likely near-ties in the ranking going either way, which wasn't examined further.
- **Against the old file on a VNNI laptop, it shows no clear difference on any set:** vague -0.004, filters -0.006, exact names +0.017, coverage +0.002.
- **The gate's tolerance is now measured:** the largest spread on a gate metric is 0.0050 (recall 0.930 against 0.935), so 0.006, and a test keeps that
  tied to `eval/results/cpu_survey.json`. The old file's spread, 0.0083, would fail it, as it should.

### The gate's lines and the cross-encoders

`.github/workflows/gate-cpu-survey.yml` then ran 12 more jobs at the pinned revision, 5 with VNNI and 7 without, for two questions the first survey left open.

**Does the tolerance hold for the coverage lines? Yes.** Every gate line of the shipped model stays within 0.006, so the coverage lines use the same tolerance. These are the lines that differ at all; lexical doesn't depend on the CPU:

<!-- table:gate-lines -->
| Gate line, shipped model | VNNI | No VNNI | Spread |
| --- | --- | --- | --- |
| curated hybrid ndcg@10 | 0.7725 | 0.7677 | 0.0048 |
| curated hybrid recall@20 | 0.9300 | 0.9350 | 0.0050 |
| coverage hybrid ndcg@10 | 0.4466 | 0.4479 | 0.0013 |
| coverage hybrid recall@20 | 0.5299 | 0.5346 | 0.0047 |
<!-- /table:gate-lines -->

The old file's coverage nDCG@10 moved by 0.0092 here, which would fail, as it should. Twelve queries are noisy, so these lines catch large falls only.

**Do the cross-encoders depend on the CPU? Their scores do, in both builds, but their quality barely moves.** On the curated and coverage queries, with the shipped embedding model:

<!-- table:cross-encoders -->
| Cross-encoder file | nDCG@10, VNNI | nDCG@10, no VNNI | Mean score difference across CPUs | Largest | ms p50, VNNI / no VNNI |
| --- | --- | --- | --- | --- | --- |
| ms-marco-l6-int8 | 0.6218 | 0.6198 | 0.357 | 0.948 | 223 / 430 |
| ms-marco-l6-uint8 | 0.6221 | 0.6241 | 0.019 | 0.630 | 389 / 507 |
| en-de-l6-int8 | 0.6344 | 0.6326 | 0.387 | 4.835 | 178 / 344 |
| en-de-l6-uint8 | 0.6244 | 0.6250 | 0.238 | 3.596 | 315 / 404 |
<!-- /table:cross-encoders -->

- **U8U8 helps `ms-marco` a lot:** the mean score difference falls from 0.357 to 0.019. **It helps `en-de` much less:** 0.387 to 0.238, with one score still differing by 3.6. So the signed-weights explanation doesn't cover everything, and this file isn't CPU-independent. Why wasn't examined.
- **Rankings are less sensitive than scores.** nDCG@10 moves by 0.002 or less between CPUs on all four files. Only the order is used, so this matters less than the scores suggest.
- **`RERANKERS` keeps both builds, and U8U8 is the one reported above and the one to use.** All of them stay off, so the choice changes nothing that runs. Anyone turning one on should re-run this survey and look at `en-de` in particular.


## 4. Memory of the `ai` profile

`docker stats`, idle, 3 minutes after start, on this laptop. "Before" is torch with MiniLM, from
[the earlier budget](notifications.md#memory-budget). The U8U8 column is from 2026-11-07, with 309 tours and two models' vectors in the database, 197 MiB of the
Docker VM's swap in use, and 685 MiB available in it. Each process verifies its model's hash at load, about 0.1 s.

| Container | Before: torch, MiniLM | ONNX int8, first choice | **ONNX U8U8, shipped** | With the EN-DE cross-encoder, int8 |
| --- | --- | --- | --- | --- |
| ai-service | 409 MiB | 575 MiB | **555 MiB** | 811 MiB loaded, 1,007 after five searches. **Killed at the 768 MiB limit** |
| ai-worker | 443 MiB | 536 MiB | **505 MiB** | no reranker |
| ai-db | 36 MiB | 66 MiB | **65 MiB** | 66 MiB |
| **`ai` adds** | **about 890 MiB** | **about 1,180 MiB** | **about 1,125 MiB** | about 1,610 MiB |
| Container limit, each | 1,536 MiB | 768 MiB | 768 MiB | needs more than 1 GiB |
| Whole stack, default + `ai` | 2,416 MiB | 2,242 MiB | 2,562 MiB | not sustained |

**The `ai` profile is about 235 MiB bigger than with torch and MiniLM. That is the bigger multilingual model, and it still fits.** The new file changes nothing measurable here: the 55 MiB against the first file is within what these idle figures vary by, and a process's resident memory was 490 MB for both.
Right after a model is downloaded or swapped, `docker stats` can read 730 to 768 MiB on the service, because recently read file pages count until they're reclaimed. It wasn't killed: at idle, 533 of its 555 MiB is process memory. The same model on torch would be
about 900 MB per process, and MiniLM int8 about 130 MB, which is what German queries cost. The whole-stack total moves mainly because
other containers' idle figures vary between runs, so don't read its changes as savings or costs. The new memory limits are half the old, with about 190
MiB of headroom on the service. A migration holds two models in the worker until cutover, so it needs its limit raised first (see
[the runbook](../runbooks/embedding-model-migration.md)).

Which profiles to combine on an 8 GB laptop, from [the budget](notifications.md#memory-budget), with `ai` at its new size:

| Demo | Profiles | About |
| --- | --- | --- |
| Search, itineraries | `ai` | 2.5 GiB |
| AI with traces and the GenAI panel | `ai`, `observability` (a sum, not measured together), not for long | 3.5 GiB |
| Emails and tickets | `notifications`, without `ai` | 2.4 GiB |
| Everything at once | Not on this laptop | |

## Reproduce

```bash
cd ai-recommendation-service
export DATABASE_URL=postgresql+psycopg://...   # a migrated database with pgvector and PostGIS
export HF_HUB_DISABLE_XET=1                    # the Xet transport stalled here at no bytes for 20 minutes; plain HTTP didn't
python -m eval.run_eval --models minilm-l6 bge-small-en paraphrase-ml paraphrase-ml-int8 paraphrase-ml-uint8 e5-small-int8 \
    --rrf-k 10 60 --lexical-weights 0.5 --rerankers ms-marco-l6-uint8 en-de-l6-uint8 --out eval/results/models.json
python -m eval.footprint --model paraphrase-ml-uint8 --runtime onnx
python -m eval.coverage_analysis --out eval/results/coverage_analysis.json
python -m eval.write_reports
```

CI runs `python -m eval.regression` on the reviewed curated set against `eval/baseline.json`, with a cached ONNX model and no
paid API, and fails if nDCG@10 or recall@20 falls by more than 0.006 on either set's lines. Before changing the model or ONNX
Runtime, run the `int8 CPU survey` and `Gate and cross-encoder CPU survey` workflows by hand, and write their summaries to
`eval/results/cpu_survey.json` and `cpu_survey_gate.json`.

`eval/results/curated_before_review.json` holds the scores from before the owner's review, with that run's latencies. It is a
fixed record, and no command writes it.
