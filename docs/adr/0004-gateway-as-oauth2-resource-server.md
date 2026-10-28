# 4. Gateway authenticates; services trust its identity headers

Date: 2026-10-27 · Status: Accepted

## Context
The gateway validated JWTs with hand-written jjwt code, and the secret was committed to git. Downstream,
booking-service took `user_id` from the request body, so any caller could book as anyone (an IDOR).

## Decision
- The gateway is a standard Spring Security OAuth2 resource server. Today it verifies HS256 tokens with a
  secret from the environment. Moving to Keycloak (RS256 + JWKS, Phase 5) is a configuration change.
- Route-level authorisation lives at the edge: browsing tours is public, writes need `OPERATOR`.
- The gateway strips any client-sent `X-User-Id` / `X-User-Roles` and sets them from the verified token.
- Services read identity only from those headers, and only the gateway's port is published.

## Consequences
- One place to change auth. Services stay simple and language-agnostic.
- Trust depends on network isolation. In a real deployment that means a private network or mTLS between
  gateway and services. Noted as future work.
- The secret previously committed to git must be treated as leaked: it is no longer used anywhere.
