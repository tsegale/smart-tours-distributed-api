# Runbook: migrate to a new embedding model

Search stays up throughout. A tour can hold one vector per model (ADR 0020). Reads use the one model marked `active` in
`embedding_models`, while the new model's vectors are filled in beside the old. Reads move in a single transaction, and the
old vectors stay until you purge them, so going back is one statement.

**When:** the evaluation in [search-quality](../benchmarks/search-quality.md) says a model is worth the move. Run it first.

**The limit:** the `embedding` column is `vector(384)`. A model with another dimension needs a schema migration too, and is
not covered here. Every candidate evaluated so far is 384.

## Steps

Run these in the worker's image, so the model loads with the same libraries. Add `-T` after `exec` when running from a
script:

```bash
R="docker compose --profile ai exec ai-worker python -m app.model_registry"
```

1. **Register the model in code.** It needs an entry in `MODELS` in `app/embeddings.py`, with its revision and the SHA-256
   of its ONNX file and tokenizer, deployed to the API and the worker. A process refuses a file whose hash doesn't match.
2. **Raise the worker's limit first.** `R` runs inside the worker's container, so the backfill shares its 768 MiB limit
   with the worker. Migrating 309 tours peaked at 1,099 MiB with no tour event arriving. The worker also loads the new model
   on its first event after step 3, which would add about another 490 MiB (not measured). 2 GiB covers both. The limit is
   fixed in `docker-compose.yml`, so raise it with an override file that you don't commit. This restarts the worker:
   ```yaml
   # migration.override.yml
   services:
     ai-worker:
       deploy:
         resources:
           limits:
             memory: 2G
   ```
   ```bash
   docker compose -f docker-compose.yml -f migration.override.yml --profile ai up -d ai-worker
   ```
3. **Start.** Marks it `backfilling`. Within 30 s, every worker also writes it for each tour event, so an edit made during
   the backfill isn't lost.
   ```bash
   $R start e5-small-int8
   ```
4. **Backfill.** Embeds every tour that is missing a vector or whose text has changed, in batches with short transactions.
   Safe to interrupt and to run again.
   ```bash
   $R backfill e5-small-int8
   ```
5. **Check.** `outstanding` must be 0. Run the backfill again if it isn't: tours were edited in the first 30 s.
   ```bash
   $R status e5-small-int8
   ```
6. **Compare on real traffic, if there is any.** Reads are still on the old model, so nothing has changed yet.
7. **Cut over.** In one transaction: the old model becomes `retired`, the new one `active`. It is refused while anything is
   outstanding. Each process notices within 30 s, loads the new model on its next request and unloads the old one.
   ```bash
   $R cutover e5-small-int8
   ```
8. **Warm it, then watch.** Nothing loads the new model until a request needs it, and that request waits for the load: 24 s
   for the first search on the 8 GB laptop, then 15 to 23 ms. The gateway's limit on a response is 30 s, so send one search of
   your own to each API process once 30 s have passed since the cutover. Then watch search latency and error rate on the
   "Services" dashboard.
9. **Put the worker's limit back.** Without the override file, this restarts it on the new model alone:
   ```bash
   docker compose --profile ai up -d ai-worker
   ```
10. **Set `EMBEDDING_MODEL` and `EMBEDDING_MODEL_REVISION`** to the new model in `.env`, or in `docker-compose.yml`'s defaults
    if the project is moving, so that a fresh database starts on it. An existing one ignores `EMBEDDING_MODEL`. They must
    change together: a revision that doesn't match the code's pin stops the service at startup.
11. **Purge, once you won't go back.** Deletes the old model's vectors.
    ```bash
    $R purge minilm-l6
    ```
    This only reaches models in `MODELS` marked `retired`. A database older than versioned embeddings can also hold rows
    under the name `sentence-transformers/all-MiniLM-L6-v2`. Reads filter by model, so they do no harm, and only SQL can
    remove them.

## Going back

Before the purge, in the database:

```sql
BEGIN;
UPDATE embedding_models SET state = 'retired', updated_at = now() WHERE state = 'active';
UPDATE embedding_models SET state = 'active', updated_at = now() WHERE model = 'minilm-l6';
COMMIT;
```

Then run `start` and `backfill` on the old model first if tours changed after the cutover: the worker stopped writing to it
when it retired, so those tours' vectors are stale. Its `outstanding` count says how many.

After a purge, the way back is the whole procedure again, with the old model as the new one.

## Last run

The owner's local database, 2026-11-07, from `paraphrase-ml-int8` to `paraphrase-ml-uint8`, 309 tours: the backfill took 36 s
and every step worked. Its peak memory and first search are given above. Search wasn't sampled continuously through the
cutover, because the sampler failed to start. So "stays up" rests on the tests below and one check after the cutover, not
on a measurement during it.

## What is tested

`tests/test_retrieval_and_itineraries.py` covers reads staying on the old model during a backfill, a cutover refused until
every tour has a vector, a tour edited mid-backfill, the worker writing to both models, exactly one active model, purging
only a retired one, and a running process picking up a cutover and unloading the old model.
