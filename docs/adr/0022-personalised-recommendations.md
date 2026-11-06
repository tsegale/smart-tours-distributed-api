# 22. Personalised recommendations

Date: 2026-11-06 · Status: Accepted

## Context
`GET /api/ai/recommendations/me` should offer a traveller bookable tours they'd like, from what they booked and how they rated it.
There is no real interaction data, so this can only be evaluated on a simulated log.

## Decision
**A time-decayed taste vector, fused with popularity by RRF, then diversified with MMR on the fused score.**

- **Events.** `booking.confirmed`, `booking.cancelled` and `booking.refunded` give who booked what; the latest status wins, in any
  arrival order, so a cancelled trip stops counting. `review.submitted` names a booking and not a user, and can arrive before its
  booking's event, so the two are joined when read. Contact details in the event are never parsed or stored.
- **Profile.** The mean of booked tours' vectors, weighted 1 for a booking, 2 if rated 4 or 5, 0 if rated 1 or 2, with a half-life of
  180 days.
- **Candidates.** Only tours with a seat on a future departure, never one already booked. The 500 nearest the profile, re-ranked by
  RRF with their popularity rank (bookings, then a rating mean pulled towards 3.5).
- **Diversity.** MMR with lambda 0.7 over the top 50, using the fused score as relevance.
- **New users** get the most popular bookable tours. The response's `source` says which logic answered.

## Consequences
- **Popularity alone has the better hit rate on this log, 0.140 against 0.065, and this ships anyway.** That is a judgment. Popularity
  scores well by recommending the same few tours to everyone, 0.5% of the catalogue, and matches a traveller's taste 32% of the time.
  The fused version matches it 82% of the time and covers 27% of the catalogue. See [the numbers](../benchmarks/itineraries.md).
- **The log is synthetic, written by the author of the ranking.** It can show that code finds a planted signal, and nothing about real
  travellers. Re-test all of this, and the choice above, on real interactions as soon as they exist.
- **Time decay is unproven.** The simulation's tastes never drift, so it can't show a benefit, and it doesn't. It stays on the
  assumption that real tastes do.
- **MMR gave the wrong answer at first.** Using cosine as relevance discarded the popularity signal that the fusion had just added, and
  scored worst of all. It is kept in the table as a record of that.
- **The profile is computed per request.** That is cheap at this size, and avoids a stored vector going stale when a model changes,
  since vectors come from the active model. A user with thousands of bookings would need a stored profile.
- **Two embedding models don't mix.** During a migration, booked tours without a vector in the active model count for nothing until
  the backfill reaches them.
