"""Key ids, the published key set (JWKS) and rotation: tokens signed with a retired key verify while it's published."""

import base64
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.tickets import (
    ALGORITHM,
    AUDIENCE,
    ISSUER,
    KeyNotPublishedError,
    ensure_published,
    issue_token,
    key_id,
    public_jwk,
    published_keys,
)
from tests.conftest import SIGNING_KEY
from tests.test_tickets import snapshot

PUBLIC_DIR = get_settings().ticket_public_keys_dir


def pem(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


@pytest.fixture
def retired_key():
    """A previous signing key whose public half is still published (the overlap window)."""
    key = Ed25519PrivateKey.generate()
    path = PUBLIC_DIR / "retired.pem"
    path.write_bytes(pem(key.public_key()))
    yield key
    path.unlink(missing_ok=True)


def test_the_key_id_is_the_rfc_7638_thumbprint():
    # RFC 8037, appendix A.2 and A.3: this public key's thumbprint.
    x = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
    key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(x + "="))

    assert public_jwk(key)["x"] == x
    assert key_id(key) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"


def test_the_jwks_lists_the_active_key_and_retired_ones_still_published(retired_key):
    response = TestClient(app).get("/.well-known/jwks.json")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/jwk-set+json"
    assert response.headers["cache-control"] == f"public, max-age={get_settings().jwks_max_age_s}"
    kids = {jwk["kid"] for jwk in response.json()["keys"]}
    assert kids == {key_id(SIGNING_KEY.public_key()), key_id(retired_key.public_key())}
    assert all(
        jwk["kty"] == "OKP" and jwk["crv"] == "Ed25519" and jwk["alg"] == "EdDSA" for jwk in response.json()["keys"]
    )


def test_during_the_overlap_tokens_from_the_old_and_the_new_key_both_verify(retired_key):
    """What a verifier does: pick the published key by the token's kid."""
    booking = snapshot(datetime(2030, 12, 1, 4, 0, tzinfo=UTC))
    old = issue_token(booking, retired_key, timedelta(hours=24))
    new = issue_token(booking, SIGNING_KEY, timedelta(hours=24))
    jwks = jwt.PyJWKSet.from_dict({"keys": published_keys(PUBLIC_DIR)})

    for token in (old, new):
        kid = jwt.get_unverified_header(token.value)["kid"]
        claims = jwt.decode(token.value, jwks[kid].key, algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER)
        assert claims["ref"] == booking.reference
    assert old.key_id != new.key_id


def test_once_a_retired_key_is_unpublished_its_kid_is_gone(retired_key):
    retired = key_id(retired_key.public_key())
    (PUBLIC_DIR / "retired.pem").unlink()

    assert retired not in {jwk["kid"] for jwk in published_keys(PUBLIC_DIR)}


def test_a_signing_key_that_is_not_published_is_refused():
    with pytest.raises(KeyNotPublishedError):
        ensure_published(Ed25519PrivateKey.generate(), PUBLIC_DIR)
    assert ensure_published(SIGNING_KEY, PUBLIC_DIR) == key_id(SIGNING_KEY.public_key())


def test_no_published_keys_is_a_503_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ticket_public_keys_dir", tmp_path)

    response = TestClient(app).get("/.well-known/jwks.json")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
