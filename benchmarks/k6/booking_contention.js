// Many virtual users hammer ONE departure through the API gateway, all trying to book a seat.
//
// Run through benchmarks/run_booking_contention.py, which starts booking-service with each
// RESERVATION_STRATEGY in turn, creates the departure, mints one JWT per VU (dev_token.py)
// and writes docs/benchmarks/booking-locking.md. PAYMENT_PROVIDER=fake, so Stripe is not
// part of what is being measured.
//
// Env: BASE_URL, AVAILABILITY_ID, TOKENS (path to a JSON array of bearer tokens), VUS,
//      ITERATIONS, SUMMARY (path the JSON summary is written to), RUN_TAG.

import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';

const tokens = JSON.parse(open(__ENV.TOKENS));
const baseUrl = __ENV.BASE_URL;
const availabilityId = Number(__ENV.AVAILABILITY_ID);

export const options = {
  scenarios: {
    contention: {
      executor: 'shared-iterations',
      vus: Number(__ENV.VUS),
      iterations: Number(__ENV.ITERATIONS),
      maxDuration: '5m',
    },
  },
  // Every percentile the report needs, computed by k6 itself.
  summaryTrendStats: ['min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max'],
  // A timeout or a 5xx is a real failure; 409s are expected answers and counted separately.
  thresholds: { booking_errors: ['count==0'] },
};

const created = new Counter('booking_created');
const soldOut = new Counter('booking_sold_out');
const busy = new Counter('booking_busy');
const errors = new Counter('booking_errors');
const createdLatency = new Trend('booking_created_duration', true);
const rejectedLatency = new Trend('booking_rejected_duration', true);

export default function () {
  const token = tokens[(__VU - 1) % tokens.length];
  const res = http.post(
    `${baseUrl}/api/bookings`,
    JSON.stringify({ availability_id: availabilityId, slots: 1 }),
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': `${__ENV.RUN_TAG}-${__VU}-${__ITER}`,
      },
      timeout: '30s',
      tags: { name: 'POST /api/bookings' },
    },
  );

  if (res.status === 201) {
    created.add(1);
    createdLatency.add(res.timings.duration);
    return;
  }
  if (res.status === 409) {
    rejectedLatency.add(res.timings.duration);
    const type = (res.json('type') || '').toString();
    if (type.endsWith('/not-enough-slots')) {
      soldOut.add(1);
    } else {
      busy.add(1); // lock wait timed out, or optimistic retries exhausted
    }
    return;
  }
  errors.add(1);
  console.error(`unexpected ${res.status}: ${res.body && res.body.substring(0, 200)}`);
}

export function handleSummary(data) {
  return {
    [__ENV.SUMMARY]: JSON.stringify(data, null, 2),
    stdout: `run ${__ENV.RUN_TAG}: ${JSON.stringify({
      created: metric(data, 'booking_created', 'count'),
      sold_out: metric(data, 'booking_sold_out', 'count'),
      busy: metric(data, 'booking_busy', 'count'),
      errors: metric(data, 'booking_errors', 'count'),
    })}\n`,
  };
}

function metric(data, name, stat) {
  const m = data.metrics[name];
  return m && m.values ? m.values[stat] || 0 : 0;
}
