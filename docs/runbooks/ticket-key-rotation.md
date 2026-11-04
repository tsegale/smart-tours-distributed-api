# Runbook: rotate the ticket signing key

The QR code on every ticket holds a check-in token signed with notification-service's Ed25519 key (ADR 0015).
booking-service verifies a scan with the public key whose id matches the token's `kid`, taken from
notification-service's JWKS (`GET /.well-known/jwks.json` on the internal network). Rotating means: a new key signs
new tickets, the previous public key stays published until the last ticket it signed has expired, then it is
removed. No ticket is reissued and no service is redeployed.

**When:** on a schedule (yearly is reasonable), when someone who could read the key leaves, or at once if the
private key may have leaked (then see [Compromised key](#compromised-key)).

## What is where

| | Local (compose) | Who reads it |
| --- | --- | --- |
| Active private key | `secrets/ticket-signing/ticket_signing_key.pem` (a compose secret) | `notification-worker` only |
| Published public keys | `secrets/ticket-public/<kid>.pem`, one per key still accepted | `notification-service` (serves the JWKS), `notification-worker` (refuses to sign with an unpublished key) |
| Key cache | in memory, `TICKET_JWKS_TTL_S` (300 s), refetch on an unknown kid at most every `TICKET_JWKS_MIN_REFETCH_S` (30 s) | booking-service |

A `kid` is the key's RFC 7638 thumbprint, so the file name, the JWKS entry and the token header can't disagree.
Every ticket row records the `kid` that signed it (`tickets.key_id` in the notifications database).

## Rotate

1. **Look at what is published now.**

   ```bash
   python scripts/ticket_keys.py --list
   ```

2. **Make the new key.** It is published first, then becomes the signing key; the previous key stays published.

   ```bash
   python scripts/ticket_keys.py --rotate
   ```

3. **Restart the render worker** so it loads the new private key (the API reads the public keys on every request
   and needs no restart):

   ```bash
   docker compose --profile notifications restart notification-worker
   ```

4. **Check.** The JWKS lists both kids, and a newly rendered ticket carries the new one:

   ```bash
   docker compose --profile notifications exec -T notification-service \
     python -c "import json,urllib.request;print([k['kid'] for k in json.load(urllib.request.urlopen('http://localhost:8084/.well-known/jwks.json'))['keys']])"
   docker compose --profile notifications exec -T notifications-db psql -U notifications -d notifications \
     -c "SELECT key_id, count(*), max(rendered_at) FROM tickets GROUP BY key_id"
   ```

   booking-service picks up the new key on the first scan that carries it (an unknown kid triggers a refetch), or
   within the cache TTL, whichever comes first. Check-ins happen hours or days after a ticket is issued, so nothing
   waits on this.

## Retire the previous key (the end of the overlap window)

A token is valid until its departure plus `TICKET_TOKEN_GRACE_HOURS` (24 h). Tickets for departures months away
were signed with the previous key, so the overlap lasts until the **latest expiry of any ticket that key signed**:

```bash
docker compose --profile notifications exec -T notifications-db psql -U notifications -d notifications \
  -c "SELECT key_id, count(*), max(token_expires_at) FROM tickets GROUP BY key_id ORDER BY 3"
```

Tickets rendered before keys had ids have `key_id` NULL; they were signed with the first key. Once `max` for the
previous key is in the past:

```bash
python scripts/ticket_keys.py --retire <previous kid>
```

booking-service drops it at its next refresh (at most `TICKET_JWKS_TTL_S` later); from then on a token signed with
it is refused with `ticket-invalid`. A scheduled reminder in the calendar for that date is the usual way to not
forget this step. Keeping a retired public key published longer costs nothing but a slightly larger JWKS.

## Compromised key

Whoever has the private key can mint tickets for any booking. Rotate as above, then **retire the compromised key
at once** instead of waiting for the overlap. Every ticket it signed stops verifying, so re-render them with the new
key: they are the notifications of confirmed bookings whose ticket row has the old `key_id`. Delete those ticket
rows and put their confirmations back through the admin API (`POST /admin/notifications/{id}/retry`, after setting
them FAILED), which renders and emails a fresh ticket. A guest arriving with an old printout is checked in by
booking reference at the desk. Record the incident, the window and the number of tickets reissued.

## In production

The procedure is the same; only where the files live changes (the deploy phase keeps the private key in a
SOPS-encrypted secret and the public keys in a ConfigMap, both rolled out by Argo CD). The rules that matter:
publish before signing, keep the previous public key until its last ticket expires, and never put the private key
anywhere but the render worker.

Tested by `booking-service/tests/test_ticket_keys.py` (overlap, pick-up of a new kid, refetch limits, retirement,
JWKS outage) and `notification-service/tests/test_ticket_keys.py` (thumbprint, the JWKS, refusing an unpublished key).
