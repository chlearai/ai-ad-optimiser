"""
Simple Fernet encryption for credentials at rest.
In production use a proper KMS (AWS KMS, HashiCorp Vault, etc.).

Backwards compatibility: credentials may have been encrypted with the
legacy default key (before ADOPTIMA_SECRET_KEY/ADOPTIMA_SALT were set in
the environment). decrypt() tries the current key first, then falls back
to the legacy key so historical blobs keep working.
"""
import os
import base64
import logging
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("AdOptima")

# In production set ADOPTIMA_SECRET_KEY via environment
SECRET_KEY = os.environ.get("ADOPTIMA_SECRET_KEY", "adoptima-internal-secret-key-for-demo-only")
SALT = os.environ.get("ADOPTIMA_SALT", "adoptima-salt").encode()

LEGACY_SECRET_KEY = "adoptima-internal-secret-key-for-demo-only"
LEGACY_SALT = "adoptima-salt".encode()

_fernet_cache: dict = {}


def _fernet_for(secret: str, salt: bytes) -> Fernet:
    cache_key = (secret, salt)
    if cache_key not in _fernet_cache:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
        _fernet_cache[cache_key] = Fernet(key)
    return _fernet_cache[cache_key]


def _get_fernet():
    return _fernet_for(SECRET_KEY, SALT)


def encrypt(text: str) -> str:
    if not text:
        return ""
    return _get_fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # Fall back to the legacy demo key for blobs written before
        # ADOPTIMA_SECRET_KEY/ADOPTIMA_SALT existed in this environment.
        if SECRET_KEY != LEGACY_SECRET_KEY or SALT != LEGACY_SALT:
            try:
                plaintext = _fernet_for(LEGACY_SECRET_KEY, LEGACY_SALT).decrypt(token.encode()).decode()
                logger.warning(
                    "Credential blob decrypted with LEGACY default key — "
                    "re-save this credential to encrypt it with the current key."
                )
                return plaintext
            except InvalidToken:
                pass
        raise