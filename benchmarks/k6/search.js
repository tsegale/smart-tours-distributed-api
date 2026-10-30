// Catalogue search through the API gateway: a realistic mix of what people search for.
//
// Run through benchmarks/run_search_benchmark.py, which seeds the catalogue, mints tokens, empties the
// cache for the cold run, reads the cache hit ratio from tour-core's /actuator/prometheus, and writes
// docs/benchmarks/search.md.
//
// SCENARIO=mix (default): an open model (constant arrival rate), so latency is measured at a fixed load
// instead of the load adapting to latency. Every iteration is one user action:
//   35% nearby     a point near a hub, a radius, sort by distance, sometimes a price cap
//   25% text       a full-text query, sometimes with a category
//   25% filtered   a country box with category, difficulty, price and rating facets; 1 in 5 also asks
//                  for a departure with free seats in a date window
//   15% paging     a filtered first page, then up to 3 more pages by cursor
// Queries come from fixed pools and are picked with a skew (popular searches repeat, the long tail
// doesn't), which is what makes a first-page cache worth having at all.
//
// SCENARIO=burst: one signed-in user fires RATE requests per second at search: the rate limiter at work.
//
// Env: BASE_URL, TOKENS (JSON array of bearer tokens), RATE, DURATION, SUMMARY, SCENARIO, SEED.

import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';

const tokens = JSON.parse(open(__ENV.TOKENS));
const baseUrl = __ENV.BASE_URL;
const scenario = __ENV.SCENARIO || 'mix';
const rate = Number(__ENV.RATE || 100);
const duration = __ENV.DURATION || '60s';

export const options = {
  scenarios: {
    [scenario]: {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration,
      preAllocatedVUs: scenario === 'burst' ? 50 : 100,
      maxVUs: scenario === 'burst' ? 100 : 400,
    },
  },
  summaryTrendStats: ['min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max'],
  // Mix: any error or 429 is a failed benchmark (budgets are per user and the load is spread).
  thresholds: scenario === 'mix' ? { search_errors: ['count==0'], search_limited: ['count==0'] } : {},
};

const ok = new Counter('search_ok');
const limited = new Counter('search_limited');
const errors = new Counter('search_errors');
const firstPage = new Trend('first_page_duration', true);
const laterPage = new Trend('later_page_duration', true);

// ------------------------------------------------------------------ query pools (deterministic)

const HUBS = [
  [-24.7275, 15.3424], [-22.6784, 14.5266], [-18.8556, 16.3294], [-22.5609, 17.0658], [-19.3, 22.9],
  [-17.9243, 25.8572], [-33.9249, 18.4241], [-24.0, 31.5], [-29.4, 29.4], [-13.0833, 31.5], [-20.5, 14.4],
  [-26.2041, 28.0473],
];
const BOXES = [
  [11.72, -28.97, 25.26, -16.96], [19.99, -26.91, 29.38, -17.78],
  [16.45, -34.84, 32.89, -22.13], [21.99, -18.08, 33.71, -8.22],
];
const TEXTS = [
  'sunrise dunes', 'game drive', 'kayak seals', 'township walk', 'rhino tracking', 'stargazing',
  'big five safari', 'quad bike', 'canyon hike', 'food tour', 'bird watching', 'sandboarding',
  'himba village', 'rock art', 'night drive leopard', 'photo safari', 'milky way', 'river sunset',
  'shipwreck coast', 'cheetah', 'elephant', 'market tour', '"walking safari"', 'desert -quad',
];
const CATEGORIES = ['SAFARI', 'DESERT', 'COASTAL', 'CULTURAL', 'ADVENTURE', 'CITY', 'WILDLIFE', 'PHOTOGRAPHY'];
const DIFFICULTIES = ['EASY', 'MODERATE', 'CHALLENGING', 'EXTREME'];
const SORTS = ['price_asc', 'price_desc', 'rating', 'newest'];

// A small deterministic PRNG per pool entry, so pool i is the same query on every run.
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = (r, list) => list[Math.floor(r() * list.length)];
const isoDay = (offset) => new Date(Date.UTC(2026, 11, 1) + offset * 86400000).toISOString().slice(0, 10);

function nearbyQuery(i) {
  const r = mulberry32(1000 + i);
  const [lat, lon] = pick(r, HUBS);
  const q = { lat: (lat + (r() - 0.5) * 0.2).toFixed(3), lon: (lon + (r() - 0.5) * 0.2).toFixed(3),
    radius: pick(r, [25000, 50000, 100000, 200000]), sort: 'distance' };
  if (r() < 0.4) q.maxPrice = pick(r, [800, 1500, 3000]);
  return q;
}
function textQuery(i) {
  const r = mulberry32(2000 + i);
  const q = { q: pick(r, TEXTS) };
  if (r() < 0.3) q.category = pick(r, CATEGORIES);
  return q;
}
function filteredQuery(i) {
  const r = mulberry32(3000 + i);
  const [minLon, minLat, maxLon, maxLat] = pick(r, BOXES);
  const q = { bbox: `${minLon},${minLat},${maxLon},${maxLat}`, category: pick(r, CATEGORIES),
    difficulty: pick(r, DIFFICULTIES), sort: pick(r, SORTS) };
  if (r() < 0.6) q.maxPrice = pick(r, [1000, 2500, 6000]);
  if (r() < 0.4) q.minRating = pick(r, [3.5, 4, 4.5]);
  if (r() < 0.2) { const start = Math.floor(r() * 60); q.from = isoDay(start); q.to = isoDay(start + 14); }
  return q;
}

const POOL = 200;
// Skewed choice: index = POOL * u^2 puts about 30% of picks on the 20 most popular queries.
const skewed = () => Math.floor(POOL * Math.random() ** 2);
const encode = (q) => Object.entries(q).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

function search(query, token, name) {
  const res = http.get(`${baseUrl}/api/tours/search?${encode(query)}`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name },
    timeout: '30s',
  });
  if (res.status === 200) ok.add(1);
  else if (res.status === 429) limited.add(1);
  else { errors.add(1); console.error(`unexpected ${res.status} for ${name}: ${res.body && res.body.slice(0, 200)}`); }
  return res;
}

export default function () {
  const token = tokens[(__VU - 1) % tokens.length];
  if (scenario === 'burst') {
    search(textQuery(0), tokens[0], 'burst');
    return;
  }
  const u = Math.random();
  if (u < 0.35) {
    firstPage.add(search(nearbyQuery(skewed()), token, 'nearby').timings.duration);
  } else if (u < 0.6) {
    firstPage.add(search(textQuery(skewed()), token, 'text').timings.duration);
  } else if (u < 0.85) {
    firstPage.add(search(filteredQuery(skewed()), token, 'filtered').timings.duration);
  } else {
    const query = filteredQuery(skewed());
    let res = search(query, token, 'filtered');
    firstPage.add(res.timings.duration);
    for (let page = 0; page < 3 && res.status === 200; page++) {
      const next = res.json('nextCursor');
      if (!next) break;
      res = search({ ...query, cursor: next }, token, 'next-page');
      laterPage.add(res.timings.duration);
    }
  }
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data, null, 2) };
}
