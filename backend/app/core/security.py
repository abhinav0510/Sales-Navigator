"""
Security primitives. Every module uses these helpers; none rolls its own crypto.

- Passwords:      argon2id hashing (memory-hard, the current OWASP recommendation).
- Access tokens:  short-lived JWTs (HS256) carrying user id, workspace id and role.
- Encryption:     AES-256-GCM for provider keys and contact values at rest.
- Lookup hashes:  keyed HMAC-SHA256 ("pepper") so we can find / dedupe / suppress
                  an email or phone without storing it in readable form.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings
from app.core.errors import Unauthorized

_hasher = PasswordHasher()


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


# --------------------------------------------------------------------------
# Access tokens
# --------------------------------------------------------------------------
def create_access_token(user_id: uuid.UUID, workspace_id: uuid.UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ws": str(workspace_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Validate signature + expiry. Raises Unauthorized on any problem."""
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid or expired token") from exc


# --------------------------------------------------------------------------
# Encryption at rest (AES-256-GCM)
# --------------------------------------------------------------------------
def _aes_key() -> bytes:
    settings = get_settings()
    if settings.encryption_key:
        return base64.urlsafe_b64decode(settings.encryption_key)
    if settings.env == "prod":
        raise RuntimeError("SN_ENCRYPTION_KEY must be set in production")
    # Dev/test only: derive a stable key from the JWT secret.
    return hashlib.sha256(("enc:" + settings.jwt_secret).encode()).digest()


def encrypt(plain: str) -> bytes:
    """Encrypt a short string. Output = 12-byte nonce || ciphertext+tag."""
    nonce = os.urandom(12)
    return nonce + AESGCM(_aes_key()).encrypt(nonce, plain.encode(), None)


def decrypt(blob: bytes) -> str:
    nonce, ciphertext = bytes(blob[:12]), bytes(blob[12:])
    return AESGCM(_aes_key()).decrypt(nonce, ciphertext, None).decode()


# --------------------------------------------------------------------------
# Keyed lookup hashes
# --------------------------------------------------------------------------
def lookup_hash(kind: str, value: str) -> bytes:
    """
    HMAC-SHA256 of a *normalized* value, namespaced by kind.

    Used for: suppression lists, the per-customer "already delivered" ledger,
    and contact dedupe. The same input always gives the same hash, but the hash
    cannot be reversed without the server-side pepper.
    """
    normalized = value.strip().lower()
    key = get_settings().hmac_pepper.encode()
    return hmac.new(key, f"{kind}:{normalized}".encode(), hashlib.sha256).digest()


def generate_secrets() -> dict[str, str]:
    """Fresh random secrets for a new deployment (`python -m app.cli gen-secrets`)."""
    return {
        "SN_JWT_SECRET": base64.urlsafe_b64encode(os.urandom(48)).decode(),
        "SN_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "SN_HMAC_PEPPER": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
