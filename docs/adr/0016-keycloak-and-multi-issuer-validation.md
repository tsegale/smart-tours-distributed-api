# 16. Keycloak, and validating tokens from more than one issuer

Date: 2026-11-05 · Status: Accepted

## Context
Until now the gateway verified HS256 tokens signed with a shared secret, minted by `scripts/dev_token.py`. Nothing in
the stack could log a user in, and anyone holding the secret could mint a token for any role.

A real identity provider costs memory: about 610 MiB here (Keycloak 571 MiB, its database 39 MiB, measured after a few
logins). The default stack has to keep running on an 8 GB laptop without it, and every benchmark mints tokens for
hundreds of simulated users, which would be slow and beside the point through a real login.

## Decision
**Keycloak 26.8 behind an `auth` compose profile, with its own Postgres database and the realm as code**
(`infra/keycloak/realm/smart-tours-realm.json`, imported on first start). The image is built with the build step
done up front, as Keycloak's container guide recommends, and starts with `--optimized`.

**The gateway trusts issuers from an explicit list**, using Spring Security's
`JwtIssuerReactiveAuthenticationManagerResolver` (the WebFlux form of `JwtIssuerAuthenticationManagerResolver`):

| | Keycloak | Dev tokens |
| --- | --- | --- |
| Enabled by | `KEYCLOAK_ISSUER_URI` | `DEV_TOKENS_ENABLED=true` (the gateway's own default is off; compose turns it on for local use) |
| Algorithm | RS256 only | HS256 only |
| Keys | JWKS, fetched over the internal network | `JWT_SECRET` |
| `iss` | The public URL | `smart-tours-dev` |

The unverified `iss` only chooses a manager, which then requires its own algorithm, its own key and the same `iss`
again. So the dev secret can't sign for Keycloak, Keycloak's public key can't be used as an HMAC secret, and an unknown
issuer is a 401 with no key fetched from anywhere a token names. **Both issuers require `aud` to contain
`smart-tours-api`**, so a token Keycloak issued for another application doesn't work here.

**Production can't trust the dev issuer.** With the `prod` Spring profile, dev tokens enabled is a startup failure, not
a warning. So is trusting no issuer at all. Checked on a real container: `prod` with dev tokens on exits with an error,
and with them off a dev token is a 401 while a Keycloak token works.

**Services don't change.** ADR 0004 already has the gateway verify the token and pass identity on as `X-User-*`
headers, so none of them knows or cares who signed it. A Keycloak `sub` is a UUID where a dev one was a free string,
and `user_id` columns are already strings.

### Realm choices
- **`roles` as a top-level claim**, from one client scope, so the gateway reads the same claim from both issuers.
  Clients have `fullScopeAllowed: false` and the scope maps only CUSTOMER, OPERATOR and ADMIN, so Keycloak's built-in
  `default-roles-*` and `offline_access` never reach `X-User-Roles`. A test asserts the claim is exactly the one role.
- **No password grant anywhere.** `smart-tours-web` is public and uses authorization code with PKCE (S256 enforced).
  `smart-tours-operator-tooling` is confidential and uses client credentials, with a service account holding OPERATOR.
  `scripts/keycloak_token.py` logs demo users in through the real login form, so it exercises what a browser would.
- **Secrets stay out of git.** The realm file holds `${PLACEHOLDERS}`, which Keycloak resolves from the environment
  at import. `scripts/keycloak_secrets.py` generates the values into the git-ignored `secrets/` directory, and only
  the database's password goes to the database's container.
- **Token lifetimes:** access token 5 minutes, refresh tokens single-use (`revokeRefreshToken`), SSO session 30 minutes
  idle and 10 hours at most.
- **Brute-force protection:** a temporary lockout after 5 failures, 60 s at first and growing to 15 minutes at most. It
  is temporary, because a permanent lockout lets anyone lock out any user whose name they know.

## Consequences
- **Keycloak down is a 503 with `Retry-After`, not a 401**, once the gateway's cached keys can't serve the token: a 401
  would tell a client to discard a valid session. Anonymous browsing and dev tokens keep working (tested, and checked by
  stopping the container).
- **The issuer URL is part of the contract.** `iss` is the public URL, so it has to match `KEYCLOAK_PUBLIC_URL` exactly,
  while keys come from the internal `keycloak:8080`.
- **The realm file only applies on first import.** Keycloak skips a realm that exists, so a change to the file means
  recreating Keycloak's database locally. Changing a live realm in production needs a migration tool or the admin API;
  that isn't solved here.
- **Keycloak reports ready before it can serve.** Its health endpoint was UP while requests still got
  `503 Bootstrap in progress`, for up to about 30 s on first start, because the realm import runs after that point.
- **Plain HTTP is local only.** Production needs TLS in front (Part B). Keycloak marks its login cookies `Secure`, which
  browsers accept on `localhost` but scripted clients don't by default, so `keycloak_token.py` allows that only for a
  local URL.
- Keycloak's memory is well under its documented 2 GB recommendation for small production deployments and just over
  its 750 MB minimum, at a 1 GiB limit. That is for a laptop. Size it properly for production.
