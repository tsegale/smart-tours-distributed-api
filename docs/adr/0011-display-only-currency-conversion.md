# 11. Display-only currency conversion from daily reference rates

Date: 2026-10-31 · Status: Accepted

## Context
Tours are priced and charged in Namibian dollars (NAD). Most visitors think in their own currency: South
Africans in ZAR, Batswana in BWP, overseas visitors in EUR, USD or GBP. Showing an approximate price in their
currency helps them browse; charging in it would need FX accounts, a rate guarantee and refund rules, which a
payment provider handles far better than we would (ADR 0006).

Three questions decide the design: where the rates come from, how fresh they must be, and how conversion
interacts with the catalogue cache (ADR 0009).

## Decision

### Display only
- `price` (NAD) stays the authoritative price on every tour and search hit. With `?currency=EUR` a response
  adds `displayPrice {amount, currency, rate, rateAsOf, stale}`. Bookings are charged in the booking's
  currency (NAD) by Stripe; every booking response says so in `currency_notice`.
- Supported display currencies: NAD, ZAR, USD, EUR, GBP, BWP. Anything else is a 400 Problem Details listing
  them. NAD itself is allowed (rate 1), so a client can always send its user's choice.

### Source: Frankfurter v2, blended rates
- **The ECB does not publish NAD.** Its reference rates cover about 30 currencies, and Frankfurter v1 (which
  serves only those) answers 404 for `base=NAD`. v1 is also deprecated: its responses carry a `Deprecation`
  header pointing to v2.
- Frankfurter **v2** (`GET /v2/rates?base=NAD&quotes=BWP,EUR,GBP,USD,ZAR`) lists eight sources that quote NAD
  directly (`/v2/currency/nad`: among them the Bank of Botswana, the BIS and the European Commission's
  InforEuro) and, without a `providers` filter, returns its own blend of every daily-publishing source,
  including cross rates (NAD/EUR on 2026-10-31 blended 88 sources, one more excluded). Free, no key, commercial use
  allowed. Each row has its own date, so staleness is judged per quote.
- Pinning a single source was considered and rejected. One of the eight, the Banco Nacional de Angola (`BNA`),
  gave NAD/ZAR 0.97847 on 2026-10-30, 2.2% off a legal 1:1 peg, while the blend gave 1.0005. One source's quirk
  becomes our price; a blend dilutes it. That response is kept as a test fixture for the peg check.
- Each stored row records its `source` (`frankfurter:blended`), so a later switch of provider stays auditable.

### Storage, refresh and the read path
- `fx_rates` (Flyway V5): base, quote, `rate NUMERIC(18,8)`, `as_of`, source, `fetched_at`. History is kept:
  one row per pair, publication date and source.
- **Rates are never fetched on the request path.** A daily refresh at 18:15 Africa/Windhoek (after the ECB's
  16:00 CET and the regional banks) runs under a **ShedLock** lock (JDBC provider, `shedlock` table, database
  time) so one of N instances fetches. A start-up refresh runs in the background when nothing was accepted
  today. A failed refresh is not retried until the next day or restart: the provider policy already retries
  within the run (ADR 0012), and the staleness window below absorbs a missed day.
- The read path has three tiers: an in-process copy (re-read every 60 s, so another instance's refresh
  arrives within a minute), Redis (`fx:latest:NAD`, written by whichever instance refreshed), and the
  database. A Redis or database outage keeps the copy already loaded.

### Sanity checks before a fetch replaces the rates in use
A rejected fetch changes nothing, logs why and increments `fx_refresh_rejections_total{reason}`:
- every expected quote present once, positive, not dated in the future;
- no quote moved more than 10% (configurable) against the rate in use: a decimal slip is a 900% move;
- **NAD/ZAR within ±0.5% of 1.0000.** NAD is pegged 1:1 to ZAR in the Common Monetary Area, so a rate
  outside that band is a data error, not news.

### Staleness
Age is today (Africa/Windhoek) minus the quote's publication date.
- up to 4 days: `stale: false`. Central banks publish on business days only; four days covers a long weekend.
- 5 to 14 days: converted, `stale: true`, so a client can say "approximately".
- more than 14 days: no `displayPrice`; `displayPriceUnavailable` gives the reason. Search bounds in that
  currency are refused with 503 `exchange-rates-unavailable` (search in NAD always works).

### Rounding
`BigDecimal` throughout. Amount = NAD × rate, rounded **HALF_EVEN** to the target's ISO 4217 minor units from
`java.util.Currency#getDefaultFractionDigits()` (2 for EUR, 0 for JPY), so currencies without cents are right
without special cases. Search bounds given in a display currency are converted to NAD to the cent and widened
(FLOOR for a minimum, CEILING for a maximum), so a tour displayed at exactly the bound is still included.

### Convert after the cache
Cached tours and cached search pages hold NAD only. The conversion is applied to the response on the way out.
- One cache entry serves every currency: caching per currency would multiply entries by six and invalidate
  every one of them each day when the rates change.
- A rate change never invalidates the catalogue cache, and a cached price can never carry yesterday's rate.
- Price bounds are converted to NAD **before** the criteria are built, so the cache key and the cursor see NAD
  only: "under 80 EUR" and "under 1486.25 NAD" are the same cache entry.
- The cost is a multiplication per price per response, measured in [`integrations.md`](../benchmarks/integrations.md).

## Consequences
- Display prices can differ from a bank's or card issuer's rate by their spread; the response says the rate,
  its date and that it is indicative.
- A tour response with `currency` (or `include=weather`) carries no ETag: the version doesn't capture the rate,
  and a 304 would replay an old one. The plain response keeps its ETag for If-Match.
- A keyset cursor from a search with display-currency bounds becomes invalid (400) if the rates change between
  two pages, because the converted NAD bounds change. That happens at most once a day; the client restarts from
  the first page.
- Rates depend on Frankfurter's blend and its upstream central banks. If Frankfurter disappears, the rates age
  into "stale" and then "unavailable" over 14 days, and prices are still shown in NAD. Replacing the adapter
  behind `ExchangeRateProvider` is the only code change needed.
