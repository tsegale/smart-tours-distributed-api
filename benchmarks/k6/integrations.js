// Tour detail with a display currency and departure weather, through the API gateway:
//   GET /api/tours/{id}?currency=EUR&include=weather&date=DATE
// The page tour-core must keep fast whatever Frankfurter and Open-Meteo are doing.
//
// Run through benchmarks/run_integrations_benchmark.py, which brings the providers into the state of each
// scenario (provider stubs from the chaos compose profile, circuit breaker state, weather cache), reads
// tour-core's weather_lookups_total before and after, and writes docs/benchmarks/integrations.md.
//
// Open model (constant arrival rate), so latency is measured at a fixed load instead of the load adapting
// to it. Tours are picked uniformly from IDS: spread over the seed's hubs, so they fall into many weather
// cells and a cold cache really is cold.
//
// Env: BASE_URL, TOKENS (JSON array of bearer tokens), IDS (JSON array of tour ids), DATE, RATE, DURATION,
// SUMMARY.

import http from 'k6/http';
import { Counter } from 'k6/metrics';

const tokens = JSON.parse(open(__ENV.TOKENS));
const ids = JSON.parse(open(__ENV.IDS));
const baseUrl = __ENV.BASE_URL;
const date = __ENV.DATE;

export const options = {
  scenarios: {
    detail: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 40),
      timeUnit: '1s',
      duration: __ENV.DURATION || '60s',
      preAllocatedVUs: 50,
      maxVUs: 300,
    },
  },
  summaryTrendStats: ['min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

// Every answer is classified: an error is anything but a 200 with the tour, its EUR price and a weatherStatus.
const errors = new Counter('detail_errors');
const weatherOk = new Counter('weather_ok');
const weatherStale = new Counter('weather_stale');
const weatherUnavailable = new Counter('weather_unavailable');
const priced = new Counter('display_priced');

export default function () {
  const id = ids[Math.floor(Math.random() * ids.length)];
  const token = tokens[(__VU + __ITER) % tokens.length];
  const res = http.get(`${baseUrl}/api/tours/${id}?currency=EUR&include=weather&date=${date}`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name: 'tour-detail' },
  });
  if (res.status !== 200) {
    errors.add(1, { status: String(res.status) });
    return;
  }
  let body;
  try {
    body = res.json();
  } catch (e) {
    errors.add(1, { status: 'unparseable' });
    return;
  }
  if (body.id !== id || !body.weatherStatus) {
    errors.add(1, { status: 'incomplete' });
    return;
  }
  if (body.displayPrice && body.displayPrice.currency === 'EUR') {
    priced.add(1);
  }
  if (body.weatherStatus === 'ok') {
    weatherOk.add(1);
  } else if (body.weatherStatus === 'stale') {
    weatherStale.add(1);
  } else {
    weatherUnavailable.add(1);
  }
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data) };
}
