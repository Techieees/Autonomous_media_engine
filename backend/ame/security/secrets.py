"""Authenticated encryption for OAuth credentials.

Envelope: ``ame.cred.v1.<kid>.<urlsafe-b64(nonce || ciphertext+tag)>``

AES-256-GCM via the ``cryptography`` library. The key comes from
``AME_CREDENTIAL_KEK``, never from ``SECRET_KEY``.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ame.config import get_settings

ENVELOPE_NAME = "ame"
ENVELOPE_KIND = "cred"
ENVELOPE_VERSION = "v1"
DEV_KID = "dev"
_KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_NONCE_LEN = 12
_KEY_LEN = 32
_HKDF_SALT = b"ame.credential.kek.v1"
_HKDF_INFO = b"ame.cred.v1"

_WEAK_KEK_LITERALS = frozenset(
    {
        "",
        "change-me-in-production",
        "changeme",
        "secret",
        "password",
        "ame",
        "dev",
        "development",
        "test",
        "local",
    }
)


class CredentialEncryptionError(ValueError):
    """Credential envelope could not be processed."""


class CredentialKeyMissing(CredentialEncryptionError):
    """Production (or caller) has no usable AME_CREDENTIAL_KEK."""


def _app_env() -> str:
    return (get_settings().app_env or "").strip().lower()


def is_production() -> bool:
    return _app_env() in {"production", "prod"}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").is_file() or (parent / "docker-compose.yml").is_file():
            return parent
    return Path.cwd()


def _dev_kek_path() -> Path:
    return _repo_root() / ".ame" / "dev-credential.kek"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _key_has_entropy(material: bytes) -> bool:
    return len(material) == _KEY_LEN and len(set(material)) > 1


def _materialize_key(secret: str) -> bytes:
    raw = secret.strip()
    if raw.lower() in _WEAK_KEK_LITERALS or len(set(raw.lower())) == 1:
        raise CredentialKeyMissing("AME_CREDENTIAL_KEK is missing or too weak")
    try:
        decoded = _b64decode(raw)
        if len(decoded) == _KEY_LEN:
            if not _key_has_entropy(decoded):
                raise CredentialKeyMissing("AME_CREDENTIAL_KEK is too weak")
            return decoded
    except CredentialKeyMissing:
        raise
    except Exception:
        pass
    try:
        decoded = bytes.fromhex(raw)
        if len(decoded) == _KEY_LEN:
            if not _key_has_entropy(decoded):
                raise CredentialKeyMissing("AME_CREDENTIAL_KEK is too weak")
            return decoded
    except CredentialKeyMissing:
        raise
    except ValueError:
        pass
    if is_production():
        raise CredentialKeyMissing(
            "AME_CREDENTIAL_KEK must be 32-byte urlsafe-base64 or hex in production"
        )
    encoded = raw.encode("utf-8")
    if len(encoded) < 32:
        raise CredentialKeyMissing("AME_CREDENTIAL_KEK is too short")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(encoded)


def _load_or_create_dev_kek() -> tuple[bytes, str]:
    path = _dev_kek_path()
    if path.is_file():
        stored = path.read_text(encoding="utf-8").strip().splitlines()
        line = next((item.strip() for item in stored if item.strip() and not item.startswith("#")), "")
        if line:
            return _materialize_key(line), DEV_KID
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(_KEY_LEN)
    encoded = _b64encode(raw)
    path.write_text(
        "# AME development-only credential KEK. Not valid in production. Do not commit.\n"
        f"{encoded}\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return raw, DEV_KID


def _configured_kek() -> str:
    return (get_settings().ame_credential_kek or "").strip()


def resolve_credential_key() -> tuple[bytes, str]:
    """Return (aes_key, kid). Production fails closed without AME_CREDENTIAL_KEK."""
    settings = get_settings()
    configured = _configured_kek()
    kid = (settings.ame_credential_kek_id or ENVELOPE_VERSION).strip() or ENVELOPE_VERSION
    if not _KID_RE.match(kid):
        raise CredentialEncryptionError("AME_CREDENTIAL_KEK_ID is invalid")
    if configured:
        if configured == (settings.secret_key or "").strip():
            raise CredentialKeyMissing("AME_CREDENTIAL_KEK must not equal SECRET_KEY")
        return _materialize_key(configured), kid
    if is_production():
        raise CredentialKeyMissing("AME_CREDENTIAL_KEK is required in production")
    return _load_or_create_dev_kek()


def _aad(kid: str) -> bytes:
    return f"{ENVELOPE_NAME}.{ENVELOPE_KIND}.{ENVELOPE_VERSION}.{kid}".encode("ascii")


def encrypt_secret(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CredentialEncryptionError("refusing to encrypt an empty credential")
    key, kid = resolve_credential_key()
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), _aad(kid))
    payload = _b64encode(nonce + ciphertext)
    return f"{ENVELOPE_NAME}.{ENVELOPE_KIND}.{ENVELOPE_VERSION}.{kid}.{payload}"


def _parse_envelope(value: str) -> tuple[str, str, str]:
    parts = (value or "").split(".")
    if len(parts) != 5 or parts[0] != ENVELOPE_NAME or parts[1] != ENVELOPE_KIND:
        raise CredentialEncryptionError("unrecognized secret envelope")
    version, kid, payload = parts[2], parts[3], parts[4]
    if version != ENVELOPE_VERSION:
        raise CredentialEncryptionError("unsupported credential envelope version")
    if not _KID_RE.match(kid) or not payload:
        raise CredentialEncryptionError("malformed credential envelope")
    return version, kid, payload


def decrypt_secret(value: str) -> str:
    _version, kid, payload = _parse_envelope(value)
    key, current_kid = resolve_credential_key()
    if kid != current_kid:
        raise CredentialEncryptionError("credential key id does not match")
    raw = _b64decode(payload)
    if len(raw) <= _NONCE_LEN + 16:
        raise CredentialEncryptionError("credential ciphertext is truncated")
    nonce, blob = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, blob, _aad(kid))
    except InvalidTag as exc:
        raise CredentialEncryptionError("credential authentication failed") from exc
    return plaintext.decode("utf-8")


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:2] + "****" + value[-2:]
