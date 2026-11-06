# Runbook: migrate to a new embedding model

Search stays up throughout. A tour can hold one vector per model (ADR 0020). Reads use the one model marked `active` in
`embedding_models`, while the new model's vectors are filled in beside the old. Reads move in a single transaction, and the
old vectors stay until you purge them, so going back is one statement.

**When:** the evaluation in [search-quality](../benchmarks/search-quality.md) says a model is worth the move. Run it first.

**The limit:** the `embedding` column is `vector(384)`. A model with another dimension needs a schema migration too, and is
not covered here. Every candidate evaluated so far is 384.

## Steps

Run these in the worker's image, so the model loads with the same libraries:

```bash
R="docker compose --profile ai exec ai-worker python -m app.model_registry"
```

1. **Register the model in code.** It needs an entry in `MODELS` in `app/embeddings.py`, deployed to the API and the worker.
2. **Start.** Marks it `backfilling`. Within 30 s, every worker also writes it for each tour event, so an edit made during
   the backfill isn't lost.
   ```bash
   $R start e5-small-int8
   ```
   The worker's memory grows by one model until the cutover. Its limit is 768 MiB and it idles at about 536 with one model,
   so **raise the limit first**, or the worker is killed on its first event. See the memory table in
   [search-quality](../benchmarks/search-quality.md).
3. **Backfill.** Embeds every tour that is missing a vector or whose text has changed, in batches with short transactions.
   Safe to interrupt and to run again.
   ```bash
   $R backfill e5-small-int8
   ```
4. **Check.** `outstanding` must be 0. Run the backfill again if it isn't: tours were edited in the first 30 s.
   ```bash
   $R status e5-small-int8
   ```
5. **Compare on real traffic, if there is any.** Reads are still on the old model, so nothing has changed yet.
6. **Cut over.** In one transaction: the old model becomes `retired`, the new one `active`. It is refused while anything is
   outstanding. Each process notices within 30 s, loads the new model on its next request and unloads the old one.
   ```bash
   $R cutover e5-small-int8
   ```
7. **Watch.** Search latency and error rate on the "Services" dashboard. The first search after the change in each API
   process is slow while the model loads (see load times in [search-quality](../benchmarks/search-quality.md)).
8. **Set `EMBEDDING_MODEL`** to the new model in `.env`, so that a fresh database starts on it. An existing one ignores it.
9. **Purge, once you won't go back.** Deletes the old model's vectors.
   ```bash
   $R purge minilm-l6
   ```

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

## What is tested

`tests/test_retrieval_and_itineraries.py` covers reads staying on the old model during a backfill, a cutover refused until
every tour has a vector, a tour edited mid-backfill, the worker writing to both models, exactly one active model, purging
only a retired one, and a running process picking up a cutover and unloading the old model.
