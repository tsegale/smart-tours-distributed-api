# 20. Embedding model and runtime chosen by evaluation

Date: 2026-11-06 · Status: Accepted

## Context
The AI service ran `all-MiniLM-L6-v2` on PyTorch, chosen by default rather than by measurement, in a 2.16 GB image with a 1.5 GB
memory limit on each of two containers, on an 8 GB laptop. Tours and travellers are in English and German. The stack has to
run with no paid service.

## Decision
**`paraphrase-multilingual-MiniLM-L12-v2`, quantised with U8U8 weights (`onnx/model_quint8_avx2.onnx`), on ONNX Runtime. No
cross-encoder. Torch leaves the runtime and CI.** The first choice was the same model's `model_qint8_avx512_vnni.onnx`, replaced
because its quality depended on the CPU (below).
All from [the evaluation](../benchmarks/search-quality.md), on 182 labelled queries in three shapes:

- **Model.** On vague queries, nDCG@10 is 0.752 against 0.627 for MiniLM and 0.652 for bge-small, both clearly worse by paired
  bootstrap. On German alone it is 0.699 against 0.523 and 0.501. `e5-small` is the close one: clearly worse on filter queries
  (-0.060), and with no clear difference on vague queries (-0.046 [-0.094, +0.001]) or exact names (+0.053 [-0.003, +0.123]).
  Those two verdicts have moved with the labels and the quantised file, so don't lean on them. It stays a candidate to re-test on
  real queries.
- **Runtime.** ONNX float and torch agree to a mean cosine of 1.0000, so the same quality by identity, at 7.6 ms against 18.0 per query.
- **Precision.** U8U8 shows no clear difference from float on any query set, on either type of CPU. Resident memory is 490 MB against
  823 for float and 901 for torch. The image goes from 2.16 GB to 615 MB.
- **Which 8-bit file: the one whose quality doesn't depend on the CPU.** The first file's weights are signed. Without VNNI, on 18 of
  36 CI jobs sampled, the chosen system fell from 0.7557 to 0.7399 and the worst query's cosine to float from 0.967 to 0.882, while
  float stayed exactly the same. That split follows the VNNI flag, not the chip: one AMD model appears on both sides. This matches saturation
  in ONNX Runtime's U8S8 kernels, though only the behaviour was measured, not the kernels. It was a quality bug on those servers, found because the CI gate
  disagreed with itself ([issue #10](https://github.com/tsegale/smart-tours-distributed-api/issues/10)).
- **Why this replacement.** Five alternatives were tested on both CPU types, and all stayed within float's interval: this one plus
  four built here with `reduce_range` and per-channel variants. This one has the highest score on the chosen system (0.7516 and 0.7511, the smallest spread),
  query vectors agreeing to five decimals across CPUs, and it is published upstream, so nothing has to be built or hosted. It
  isn't the closest to float in vector terms: its worst query is 0.951, against 0.989 for a plain U8U8 build. Query time rises from
  2.1 to 2.5 ms with VNNI and 3.6 to 4.0 without, in one sample of runners. See [the table](../benchmarks/search-quality.md#does-the-score-depend-on-the-cpu).
- **Cross-encoder off.** With this model, re-ranking gives no clear gain on any set. Through the real stack, search goes from about
  40 ms to 1 to 3 s, and the service from 575 to about 1,007 MiB, which got it killed under its 768 MiB limit until the limit was
  raised. It helped the weaker models, which is why it was measured, and it stays behind `RERANK_MODEL`.

**Versioned embeddings, so a model can change with search up.** A tour holds one vector per model, and `embedding_models` says which
one reads use. A new model is backfilled beside the old while reads stay put. Cutover is one transaction, refused while any tour is
missing or stale, and each process picks it up within 30 s with no restart. See
[the runbook](../runbooks/embedding-model-migration.md).

**A quality gate in CI:** `eval/regression.py` runs the curated queries over the written tours against a committed baseline, with a
cached ONNX model and no paid API, and fails on a fall of more than 0.006. That tolerance is the largest spread measured between CPUs,
0.0050, rounded up, and a test ties it to the evidence.

## Consequences
- **Memory went up, not down, against the old setup, and that is the model's doing, not the runtime's.** Idle, ai-service and
  ai-worker were 409 and 443 MiB with torch and MiniLM, and are 575 and 536 with this model. ONNX is what makes this model
  affordable: on torch it would take about 900 MB per process. A MiniLM int8 process needs about 130 MB, so the German queries cost
  about 360 MB per process. That is the first trade to revisit if memory is the constraint.
- **The curated labels are reviewed; the rest is still synthetic.** The owner decided the contested calls and signed the set off on
  2026-11-06, and [no decision changed](../benchmarks/search-quality.md#the-owners-review-before-and-after). The corpus is synthetic
  and no real user has touched any of this, so the numbers rank these options on this data. A further 12 queries for tours the set
  doesn't reach were signed off the same day and are reported apart.
- **The chosen model truncates at 128 tokens.** Descriptions here are short. Long real ones would lose their ends.
- **A model of another dimension needs a schema migration.** The column is `vector(384)`, and every candidate happens to be 384.
- **ARM servers and x86 CPUs without AVX2 are unmeasured,** and hosted runners can't sample them. Moving to one means running
  `int8-cpu-survey.yml` or its equivalent there first.
- **The cross-encoders still use `model_qint8_avx512_vnni.onnx`.** They are off, but anyone turning one on should expect the same
  dependence on the CPU, and measure it.
- **The model's revision isn't pinned in code.** Upstream could change this file, and the gate would catch only a fall in quality.
- **A database already on the old file stays on it,** because the active model outranks `EMBEDDING_MODEL`. Moving it is the normal
  [migration](../runbooks/embedding-model-migration.md): the old and new vectors are not comparable.
- **HNSW isn't exercised at this size** (ADR 0019), and one index holds all models' vectors during a migration, with the filter on
  `model` applied by iterative scan. That is unmeasured at scale too.
- **Model downloads disable the Xet transport** (`HF_HUB_DISABLE_XET=1`): on this connection it stalled at no bytes for 20 minutes,
  while plain HTTP from the same host took 6 minutes for the same file. One connection isn't a verdict on Xet, and a slower path is
  a small cost for the download happening.
