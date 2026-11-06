# 20. Embedding model and runtime chosen by evaluation

Date: 2026-11-06 · Status: Accepted

## Context
The AI service ran `all-MiniLM-L6-v2` on PyTorch, chosen by default rather than by measurement, in a 2.16 GB image with a 1.5 GB
memory limit on each of two containers, on an 8 GB laptop. Tours and travellers are in English and German. The stack has to
run with no paid service.

## Decision
**`paraphrase-multilingual-MiniLM-L12-v2`, int8 quantised, on ONNX Runtime. No cross-encoder. Torch leaves the runtime and CI.**
All from [the evaluation](../benchmarks/search-quality.md), on 182 labelled queries in three shapes:

- **Model.** On vague queries, nDCG@10 is 0.751 against 0.631 for MiniLM and 0.654 for bge-small, both clearly worse by paired
  bootstrap. On German alone it is 0.697 against 0.525 and 0.503. `e5-small` is the close one: no clear difference on vague
  queries, clearly worse on filter queries (-0.066), clearly better on exact names (+0.070). Filter queries are the larger part of
  what the product is for, and that is a judgment, so it is a close call and a candidate to re-test on real queries.
- **Runtime.** ONNX float and torch agree to a mean cosine of 1.0000, so the same quality by identity, at 7.6 ms against 18.0 per query.
- **Precision.** int8 shows no clear difference from float on any query set, agrees with it to 0.998, and halves query time
  again to 3.7 ms. Resident memory is 489 MB against 823 for float and 901 for torch. The image goes from 2.16 GB to 615 MB.
- **Cross-encoder off.** With this model, re-ranking gives no clear gain on any set. Through the real stack, search goes from about
  40 ms to 1 to 3 s, and the service from 575 to about 1,007 MiB, which got it killed under its 768 MiB limit until the limit was
  raised. It helped the weaker models, which is why it was measured, and it stays behind `RERANK_MODEL`.

**Versioned embeddings, so a model can change with search up.** A tour holds one vector per model, and `embedding_models` says which
one reads use. A new model is backfilled beside the old while reads stay put. Cutover is one transaction, refused while any tour is
missing or stale, and each process picks it up within 30 s with no restart. See
[the runbook](../runbooks/embedding-model-migration.md).

**A quality gate in CI:** `eval/regression.py` runs the curated queries over the written tours against a committed baseline, with a
cached ONNX model and no paid API, and fails on a fall of more than 0.005.

## Consequences
- **Memory went up, not down, against the old setup, and that is the model's doing, not the runtime's.** Idle, ai-service and
  ai-worker were 409 and 443 MiB with torch and MiniLM, and are 575 and 536 with this model. ONNX is what makes this model
  affordable: on torch it would take about 900 MB per process. A MiniLM int8 process needs about 130 MB, so the German queries cost
  about 360 MB per process. That is the first trade to revisit if memory is the constraint.
- **The labels aren't human yet.** The curated set's judgments were proposed by Claude and are marked `needs_review`, the corpus is
  synthetic, and no real user has touched any of this. The numbers rank these options on this data.
- **The chosen model truncates at 128 tokens.** Descriptions here are short. Long real ones would lose their ends.
- **A model of another dimension needs a schema migration.** The column is `vector(384)`, and every candidate happens to be 384.
- **`model_qint8_avx512_vnni.onnx`** runs on any x86-64 CPU and fastest on VNNI. ARM servers and older CPUs are unmeasured.
- **HNSW isn't exercised at this size** (ADR 0019), and one index holds all models' vectors during a migration, with the filter on
  `model` applied by iterative scan. That is unmeasured at scale too.
- **Model downloads disable the Xet transport** (`HF_HUB_DISABLE_XET=1`): on this connection it stalled at no bytes for 20 minutes,
  while plain HTTP from the same host took 6 minutes for the same file. One connection isn't a verdict on Xet, and a slower path is
  a small cost for the download happening.
