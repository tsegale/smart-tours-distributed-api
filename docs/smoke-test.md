# Full-stack smoke test

A repeatable end-to-end check of the running stack, through the gateway only. Run it from the repo root
in bash (Git Bash on Windows works). Every step prints what it got next to what it expects.

Requirements: Docker with Compose v2, `curl`, Python 3 (for `scripts/dev_token.py` and JSON parsing).

```bash
cp .env.example .env    # then replace every change-me value (openssl rand -base64 48 for JWT_SECRET)
docker compose up --build -d --wait
docker compose ps       # every service "healthy" or "running"
```

## 0. Setup

```bash
set -a; . ./.env; set +a
GW=http://127.0.0.1:8080
MQ=http://127.0.0.1:15672/api
OPERATOR=$(python scripts/dev_token.py --sub op-1 --roles OPERATOR)
CUSTOMER=$(python scripts/dev_token.py --sub alice)
json() { python -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
# Poll for up to 120 s until a command succeeds (embedding is asynchronous).
wait_for() { for _ in $(seq 60); do eval "$1" >/dev/null 2>&1 && return 0; sleep 2; done; echo "timed out: $1"; return 1; }
queue_depth() { curl -s -u "$RABBITMQ_USER:$RABBITMQ_PASSWORD" "$MQ/queues/%2F/$1" | json "d['messages']"; }
```

Use `127.0.0.1` rather than `localhost`: compose publishes ports on IPv4 only, and on Windows `localhost`
tries `::1` first and can stall for the full connect timeout.

## 1. Edge security

```bash
echo "no token on /api/bookings/me:   $(code $GW/api/bookings/me)  (expect 401)"
echo "CUSTOMER creating a tour:       $(code -X POST $GW/api/tours -H "Authorization: Bearer $CUSTOMER" \
  -H 'Content-Type: application/json' -d '{"name":"x","price":1,"latitude":0,"longitude":0}')  (expect 403)"
echo "public GET /api/tours:          $(code $GW/api/tours)  (expect 200)"
```

## 2. Tour events drive embeddings

```bash
create_tour() {
  curl -s -X POST $GW/api/tours -H "Authorization: Bearer $OPERATOR" -H 'Content-Type: application/json' -d "$1" | json "d['id']"
}
T1=$(create_tour '{"name":"Sossusvlei Sunrise Dunes","description":"Climb Dune 45 at dawn above the red desert","price":1850.00,"latitude":-24.7275,"longitude":15.3424}')
T2=$(create_tour '{"name":"Swakopmund Kayak with Seals","description":"Paddle Walvis Bay lagoon among Cape fur seals","price":1200.00,"latitude":-22.9575,"longitude":14.5053}')
echo "created tours $T1 and $T2"

wait_for "docker compose logs ai-worker | grep -q 'Embedded tour $T2 '"
docker compose logs ai-worker | grep "Embedded tour"     # expect a line for each tour id
docker compose exec -T ai-db psql -U "$AI_DB_USER" -d ai -c "SELECT tour_id, title, model FROM tour_embeddings ORDER BY tour_id"

curl -s "$GW/api/tours/nearby?lat=-22.5609&lon=17.0658&radius=400000"; echo     # Swakopmund is ~280 km away
curl -s $GW/api/ai/recommend/$T1 -H "Authorization: Bearer $CUSTOMER"; echo      # expect tour 2 listed
curl -s "$GW/api/ai/search?q=desert" -H "Authorization: Bearer $CUSTOMER"; echo  # expect tour 1 ranked first
```

## 3. Idempotent booking

```bash
AV=$(curl -s -X POST $GW/api/availability -H "Authorization: Bearer $OPERATOR" -H 'Content-Type: application/json' \
  -d "{\"tour_id\":$T1,\"starts_at\":\"2026-12-01T05:30:00+02:00\",\"total_slots\":10}" | json "d['id']")
KEY=$(python -c "import uuid; print(uuid.uuid4())")
book() {
  curl -s -w '\n%{http_code}\n' -X POST $GW/api/bookings -H "Authorization: Bearer $CUSTOMER" \
    -H "Idempotency-Key: $KEY" -H 'Content-Type: application/json' -d "{\"availability_id\":$AV,\"slots\":2}"
}
book    # expect the booking, then 201
book    # expect the same booking id, then 200
curl -s "$GW/api/availability?tour_id=$T1" -H "Authorization: Bearer $CUSTOMER"; echo   # booked_slots is 2, not 4
```

## 4. Poison message goes to the DLQ without blocking the queue

```bash
DLQ_BEFORE=$(queue_depth ai.tour-embedding.dlq)
curl -s -u "$RABBITMQ_USER:$RABBITMQ_PASSWORD" -H 'Content-Type: application/json' \
  -X POST "$MQ/exchanges/%2F/smarttours.events/publish" \
  -d '{"properties":{"content_type":"application/json"},"routing_key":"tour.created","payload":"{not json","payload_encoding":"string"}'
echo   # expect {"routed":true}
wait_for '[ "$(queue_depth ai.tour-embedding.dlq)" -gt "$DLQ_BEFORE" ]'
echo "DLQ depth: $DLQ_BEFORE -> $(queue_depth ai.tour-embedding.dlq)  (expect +1)"

# A valid event published after the poison one is still processed.
T3=$(create_tour '{"name":"Etosha Game Drive","description":"Lions and elephants at the waterholes","price":2400.00,"latitude":-18.8556,"longitude":16.3294}')
wait_for "docker compose logs ai-worker | grep -q 'Embedded tour $T3 '"
docker compose logs ai-worker | grep -E "Poison message|Embedded tour $T3 "   # expect both lines
echo "main queue depth: $(queue_depth ai.tour-embedding)  (expect 0)"
```

## 5. Spoofed identity headers are overwritten

```bash
B=$(curl -s -X POST $GW/api/bookings -H "Authorization: Bearer $CUSTOMER" \
  -H 'X-User-Id: mallory' -H 'X-User-Roles: ADMIN' -H 'Content-Type: application/json' \
  -d "{\"availability_id\":$AV,\"slots\":1}" | json "d['id']")
docker compose exec -T bookings-db psql -U "$BOOKINGS_DB_USER" -d bookings -tAc "SELECT user_id FROM bookings WHERE id = $B"
# expect alice (the token's subject), not mallory
curl -s $GW/api/bookings/me -H "Authorization: Bearer $CUSTOMER" | json "[b['id'] for b in d]"   # includes $B
```

## Reset

```bash
docker compose down -v   # drops all volumes, including the downloaded embedding model
```
