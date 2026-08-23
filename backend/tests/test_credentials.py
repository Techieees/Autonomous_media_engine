"""Authenticated credential encryption — no custom crypto, no SECRET_KEY fallback."""

from __future__ import annotations

import base64

import pytest

from ame.config import get_settings
from ame.contracts.enums import ConnectionState
from ame.oauth.tokens import persist_tokens
from ame.security.secrets import (
    ENVELOPE_KIND,
    ENVELOPE_NAME,
    ENVELOPE_VERSION,
    CredentialEncryptionError,
    CredentialKeyMissing,
    decrypt_secret,
    encrypt_secret,
    resolve_credential_key,
)

PLAINTEXT = "ya29.oauth-access-token-UNIQUE-PLAINTEXT-xyz"


def _b64key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(seed).decode("ascii").rstrip("=")


KEY_A = _b64key(bytes(range(32)))
KEY_B = _b64key(bytes(range(32, 64)))
PROD_SECRET = "production-secret-key-value-32ok"


def _production(monkeypatch, *, kek: str | None, kid: str = "v1") -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", PROD_SECRET)
    if kek is None:
        monkeypatch.delenv("AME_CREDENTIAL_KEK", raising=False)
    else:
        monkeypatch.setenv("AME_CREDENTIAL_KEK", kek)
    monkeypatch.setenv("AME_CREDENTIAL_KEK_ID", kid)
    get_settings.cache_clear()


def test_ciphertext_hides_plaintext(monkeypatch) -> None:
    _production(monkeypatch, kek=KEY_A)
    blob = encrypt_secret(PLAINTEXT)
    assert blob.startswith(f"{ENVELOPE_NAME}.{ENVELOPE_KIND}.{ENVELOPE_VERSION}.")
    assert PLAINTEXT not in blob
    assert PLAINTEXT.encode("utf-8") not in blob.encode("ascii")
    payload = blob.rsplit(".", 1)[-1]
    pad = "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(payload + pad)
    assert PLAINTEXT.encode("utf-8") not in raw
    assert b"ya29." not in raw


def test_correct_key_decrypts(monkeypatch) -> None:
    _production(monkeypatch, kek=KEY_A)
    assert decrypt_secret(encrypt_secret(PLAINTEXT)) == PLAINTEXT


def test_wrong_key_fails(monkeypatch) -> None:
    _production(monkeypatch, kek=KEY_A)
    blob = encrypt_secret(PLAINTEXT)
    _production(monkeypatch, kek=KEY_B)
    with pytest.raises(CredentialEncryptionError):
        decrypt_secret(blob)


def test_tampered_ciphertext_fails(monkeypatch) -> None:
    _production(monkeypatch, kek=KEY_A)
    blob = encrypt_secret(PLAINTEXT)
    head, tail = blob.rsplit(".", 1)
    flipped = "A" if not tail.startswith("A") else "B"
    tampered = f"{head}.{flipped}{tail[1:]}"
    with pytest.raises(CredentialEncryptionError):
        decrypt_secret(tampered)


def test_production_without_kek_refuses_encrypt(monkeypatch) -> None:
    _production(monkeypatch, kek=None)
    with pytest.raises(CredentialKeyMissing, match="AME_CREDENTIAL_KEK"):
        encrypt_secret(PLAINTEXT)


def test_production_without_kek_refuses_decrypt(monkeypatch) -> None:
    _production(monkeypatch, kek=KEY_A)
    blob = encrypt_secret(PLAINTEXT)
    _production(monkeypatch, kek=None)
    with pytest.raises(CredentialKeyMissing, match="AME_CREDENTIAL_KEK"):
        decrypt_secret(blob)


@pytest.mark.asyncio
async def test_production_without_kek_refuses_token_persist(monkeypatch) -> None:
    _production(monkeypatch, kek=None)

    class _Session:
        async def flush(self) -> None:
            return None

    class _Conn:
        metadata_json = {}
        token_encrypted = None
        refresh_encrypted = None
        expires_at = None
        scopes = []
        state = "not_configured"
        platform = "youtube"
        account_label = None

    async def _load(_session, _platform):
        return _Conn()

    monkeypatch.setattr("ame.oauth.tokens.load_connection", _load)
    with pytest.raises(CredentialKeyMissing):
        await persist_tokens(
            _Session(),  # type: ignore[arg-type]
            "youtube",
            access_token=PLAINTEXT,
            refresh_token="refresh-UNIQUE-PLAINTEXT",
            expires_in=3600,
            scopes=["readonly"],
            state=ConnectionState.CONNECTED,
        )


def test_kek_must_not_equal_secret_key(monkeypatch) -> None:
    _production(monkeypatch, kek=PROD_SECRET)
    with pytest.raises(CredentialKeyMissing, match="SECRET_KEY"):
        resolve_credential_key()


def test_production_rejects_low_entropy_kek(monkeypatch) -> None:
    _production(monkeypatch, kek=_b64key(b"A" * 32))
    with pytest.raises(CredentialKeyMissing, match="too weak"):
        resolve_credential_key()


def test_production_rejects_passphrase_kek(monkeypatch) -> None:
    _production(monkeypatch, kek="not-a-32-byte-key-but-long-enough-passphrase!!")
    with pytest.raises(CredentialKeyMissing, match="32-byte"):
        resolve_credential_key()


def test_legacy_hmac_envelope_rejected(monkeypatch) -> None:
    _production(monkeypatch, kek=KEY_A)
    with pytest.raises(CredentialEncryptionError, match="unrecognized"):
        decrypt_secret("ame1:not-a-real-envelope")
