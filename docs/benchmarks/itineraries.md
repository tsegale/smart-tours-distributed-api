# Itineraries and personalised recommendations

Tables written from `ai-recommendation-service/eval/results/` by `python -m eval.write_reports`. Measured on the shipped
`paraphrase-ml-uint8` on 2026-11-07. Against the int8 file that came before it, 40 of 40 plans still meet every constraint, the plans
change slightly (items per plan on an always-valid fake, 3.9 to 4.0), and every recommendation score stays inside its earlier interval.

## Itineraries

40 prompts, 20 English and 20 German, through the real pipeline on the synthetic catalogue, with 12 random departures per tour
over 120 days. Budgets range from impossible to unlimited, parties from 1 to 10, trips from 1 to 14 days.

"Constraints met" is **not** the validator's own opinion: `independent_check` in `eval/run_itineraries.py` re-checks every final
plan against the tables with its own SQL and arithmetic, for catalogue membership, seats, dates, budget, overlaps and road time.

<!-- table:itineraries -->
| Provider | Constraints met | First-pass valid | Repaired | Fell back | ms p50 / p95 | Tokens per plan | Items per plan | Days covered | Budget used |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Deterministic only (no provider) | 100% | - | - | - | 44 / 179 | 0 | 5.2 | 88% | 76% |
| Fake: always valid | 100% | 100% | 0% | 0% | 54 / 114 | 9132 | 4.0 | 87% | 63% |
| Fake: invalid, then valid | 100% | 0% | 100% | 0% | 62 / 210 | 18208 | 4.0 | 87% | 63% |
| Fake: never valid | 100% | 0% | 0% | 100% | 50 / 82 | 27227 | 5.2 | 88% | 76% |
| Fake: obeys an injected instruction | 100% | 0% | 0% | 100% | 45 / 59 | 27227 | 5.2 | 88% | 76% |
<!-- /table:itineraries -->

**No real model has been run.** No API key was used, as agreed, and this table has no row for one. Every Fake row is scripted:

- **Its first-pass, repair and fallback rates are true by construction** and say nothing about any model. What they show is that
  each path of the pipeline works and what it adds: its latency without a model's own, and the tokens each path spends. Fake token
  counts are estimates at four characters a token.
- **What they do show:** whatever the planner does, 40 of 40 final plans met every constraint, including when the "model" returned
  a tour that doesn't exist on every attempt. Two plans are empty, for the same request in each language: a budget of N$500 for two
  people, which nothing in the catalogue fits. An empty plan is the correct answer there, and the response says so by having no days.

To add a real provider, set `LLM_PROVIDER` and its settings, then:

```bash
python -m eval.run_itineraries --model paraphrase-ml-uint8 --provider none configured --out eval/results/itineraries.json
```

That run spends money or needs a local model. Its first-pass validity, repair rate, latency and tokens are the numbers this
document can't yet give.

The deterministic planner is greedy and has no sense of a pleasant trip. Days covered and budget used are reported so that a real
model has something to beat. On the prompts' tight budgets and short trips, it does not cover every day, partly because
departures are random and sparse.

## Personalised recommendations

**The interactions are synthetic.** A simulated log from `eval/run_recommendations.py`: 500 travellers over 18 months, each with
a taste of one or two categories. 70% of their bookings follow that taste and 30% follow popularity, which has a long tail. The
split is by time, with the last 3 months as the test period. Each traveller's bookings in that period are what's to be predicted,
from what they did before it. The embeddings see only tour text, not the labels the simulation chose by.

So this shows how the ranking finds a signal **planted by the same person who wrote the ranking**. It can't show what real
travellers would do, and it can't reward time decay at all, because simulated tastes never drift.

<!-- table:recommendations -->
| System | Hit rate@10 | nDCG@10 | Taste match@10 | Catalogue coverage |
| --- | --- | --- | --- | --- |
| popularity | 0.140 [0.106, 0.176] | 0.054 [0.040, 0.071] | 0.32 | 0.5% |
| content, last booking | 0.023 [0.010, 0.039] | 0.006 [0.002, 0.010] | 0.79 | 67.6% |
| profile | 0.013 [0.003, 0.026] | 0.003 [0.001, 0.007] | 0.94 | 50.4% |
| profile, no decay | 0.016 [0.005, 0.028] | 0.004 [0.001, 0.007] | 0.95 | 43.8% |
| profile + MMR | 0.010 [0.003, 0.021] | 0.004 [0.001, 0.007] | 0.91 | 50.0% |
| profile + popularity (RRF) | 0.060 [0.036, 0.083] | 0.017 [0.009, 0.025] | 0.87 | 33.7% |
| profile + popularity + MMR | 0.062 [0.039, 0.088] | 0.021 [0.012, 0.031] | 0.82 | 25.9% |
| same, no booking data | 0.010 [0.003, 0.021] | 0.003 [0.000, 0.007] | 0.93 | 49.8% |
| same, no booking data, ties by id (the old bug) | 0.016 [0.005, 0.028] | 0.004 [0.001, 0.008] | 0.80 | 23.1% |
<!-- /table:recommendations -->

Taste match is the share of the ten recommended tours in the traveller's simulated categories. Coverage is the share of the
catalogue that was ever recommended to anyone.

What the numbers decided, and what they didn't:

- **Popularity alone has the best hit rate here, and that is not hidden.** With a long-tailed simulation, recommending the same five
  or so tours to everyone hits often. It also ignores taste, and it recommends about 0.5% of the catalogue.
- **The profile alone finds taste and misses the booking.** It is right about what a traveller likes and has no way to choose among
  hundreds of tours in that category.
- **So production fuses the two by RRF, then diversifies with MMR on the fused score:** about half of popularity's hit rate with
  more than double its taste match and about 50 times its coverage. That trade is a judgment, not something the table proves. A
  real interaction log could change it, and should be used to re-test it as soon as one exists.
- **MMR first used cosine as its relevance,** which threw away the popularity signal the fusion had added, and scored worst of all.
  Using the fused score fixed that. Plain MMR with the profile stays in the table as the record of that.
- **Ties in popularity first counted as a signal,** because equally popular tours were ranked by id. The simulated log has bookings
  everywhere that matters, so the table never showed it. The smoke test did: on a catalogue with no bookings, the closest match to what
  a traveller booked came 20th, behind unrelated tours with low ids. They now share a rank. With no booking data, that takes taste
  match from 0.80 to 0.93 and coverage from 23.1% to 49.8%, and hit rate shows no clear difference. With booking data, on the file that ran then, it left hit
  rate where it was, and coverage fell from 27.1% to 25.3%. The last two rows are the record of it.
- **Time decay shows no gain,** and can't here (see above). It stays because tastes and trips do change in reality, which is an
  assumption, not a finding.
