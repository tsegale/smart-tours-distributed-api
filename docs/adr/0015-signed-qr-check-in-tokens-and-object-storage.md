# 15. Signed QR check-in tokens, and object storage for tickets

Date: 2026-11-04 · Status: Accepted

## Context
A confirmed booking gets a PDF e-ticket with a QR code that a guide scans at the meeting point. Two questions:
what the QR code holds, and where the PDF lives so the customer can download it again.

## Decision

### The QR code holds a signed, expiring token (JWS, EdDSA)
- A compact JWS signed with **Ed25519** by notification-service, with claims `sub` (booking id), `ref` (booking
  reference), `aid` (departure id), `dep` (departure time), `iss`, `aud` (`smarttours-checkin`), `iat`, `nbf`,
  `exp` (departure + 24 h), `jti`, and a `kid` header for rotation.
- **A dedicated key pair, not the gateway's auth secret.** The auth secret is symmetric (HS256): anything that can
  verify a login token can mint one, and a leak there is a leak everywhere. The ticket key is asymmetric: only
  notification-service's worker holds the private key (a compose secret); booking-service, which checks tickets,
  holds only the public key and **can verify a ticket but never mint one**. `python scripts/ticket_keys.py`
  generates the pair into `secrets/` (git-ignored); `--rotate` adds a new one (see the update below).
- Why a token and not a random code looked up in a table: the scanner's decision needs the signature, the expiry
  and the booking row it already has. A random code would work too (and is revocable by deleting it), but needs a
  table per issued ticket and gives no offline check. The signature also binds the booking to its departure, so a
  ticket can't be replayed for another tour.

### Check-in rules (`POST /api/check-ins {token}`, OPERATOR)
In this order, each with its own Problem Details type (RFC 9457):

| Check | Failure |
| --- | --- |
| signature, issuer, audience, structure | 422 `ticket-invalid` (tampered, foreign, malformed) |
| `exp` (60 s leeway) | 410 `ticket-expired` |
| the booking exists | 404 `not-found` |
| `ref` and `aid` match the booking | 422 `ticket-invalid` |
| the booking is `CONFIRMED` | 409 `booking-not-confirmed` |
| the departure is **today in the tour's time zone** (not UTC: 00:05 in Windhoek is still yesterday in UTC) | 409 `ticket-wrong-day` |
| not checked in yet | 409 `ticket-already-used`, with the first `checked_in_at` |

The booking row is locked for the decision, so two scans of one ticket at the same moment are decided one after
the other: one succeeds, the other is `ticket-already-used`. `checked_in_at` and `checked_in_by` are recorded.

### PDFs in S3-compatible object storage, downloaded through presigned URLs
- The PDF goes to a **private** bucket (`tickets`, no anonymous policy) through the AWS SDK (boto3), so S3 in
  production is a configuration change. MinIO in compose. MinIO stopped publishing community images in October 2025;
  compose uses Chainguard's build of current MinIO source, pinned by digest.
- **Two least-privilege users**, created by a one-shot setup container: `tickets-writer` (notification-service: put
  and get) and `tickets-reader` (booking-service: get only). A presigned URL can do no more than its signer.
- `GET /api/bookings/{id}/ticket` (owner only, 404 for anyone else): **302 to a presigned GET URL** valid for
  5 minutes, `Cache-Control: no-store`; 404 `ticket-not-ready` until the ticket exists. The URL is signed for the
  host the browser uses (`TICKETS_S3_PUBLIC_ENDPOINT_URL`); presigning is a local computation, no call to storage.
- Why not stream the PDF through booking-service: the gateway and booking-service would carry every byte; with a
  presigned URL they carry one redirect, and storage does what it is built for.

### booking-service learns where the ticket is from an event
notification-service records the ticket and writes `ticket.rendered` to **its own outbox** in the same transaction
(the envelope and relay design of ADR 0008; schema in docs/events.md). booking-service's `ticket-listener` consumes
it into `booking_tickets`. No service reads another's database (ADR 0001). The relay runs as a Celery task, right
after rendering and every 30 s from Beat.

## Consequences
- A leaked public key is harmless; a leaked private key lets someone mint valid tickets until it is rotated and
  retired, which invalidates the tickets it signed (docs/runbooks/ticket-key-rotation.md).
- Tickets can't be revoked individually before they expire; cancellation is caught by the `CONFIRMED` check
  instead, which is the case that matters.
- The download link expires after 5 minutes, so it can't be shared usefully; the customer asks again (rate
  limited at the gateway, 1/s with a burst of 10).
- One more object store to run, behind the `notifications` profile.

## Update (Phase 5): rotation with an overlap window
The first version had one key pair: rotating it invalidated every ticket already sent. Now:
- A key's `kid` is its RFC 7638 JWK thumbprint, so an id can't point at the wrong key and needs no configuration.
- notification-service publishes the public keys still accepted (the active one and retired ones whose tickets
  haven't expired) as a JWKS, `GET /.well-known/jwks.json`, internal network only. The render worker refuses to
  sign with a key that isn't published, so no ticket is ever issued that nothing can verify.
- booking-service fetches the JWKS, caches it (5 min), and refetches on an unknown kid at most every 30 s, so a
  rotated key is picked up on its first scan and made-up kids can't make every scan a request. If the JWKS can't be
  fetched, known keys keep working; an unknown kid then is a 503, not an invalid ticket.
- Each ticket records its `kid`; a retired key is unpublished once the latest expiry of its tickets has passed.

The private key still lives in exactly one process. Signing through a key-management service, where the key never
leaves the KMS, would remove even that. Procedure:
[docs/runbooks/ticket-key-rotation.md](../runbooks/ticket-key-rotation.md).
