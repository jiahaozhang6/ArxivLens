import base64
import hashlib

import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.security import SecretDecryptionError, decrypt_secret, encrypt_secret


def _legacy_encrypt(value: str, secret: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")


def test_versioned_secret_is_purpose_isolated():
    encrypted = encrypt_secret("llm-secret-value", "llm")
    assert encrypted is not None
    assert encrypted.startswith("v2:llm:")
    assert decrypt_secret(encrypted, "llm") == "llm-secret-value"
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(encrypted, "smtp")


def test_legacy_secret_remains_decryptable():
    settings = get_settings()
    encrypted = _legacy_encrypt("legacy-secret-value", settings.secret_key)
    assert decrypt_secret(encrypted, "llm") == "legacy-secret-value"


def test_previous_master_key_supports_rotation():
    settings = get_settings()
    current = settings.secret_key
    previous = settings.secret_key_previous
    try:
        settings.secret_key = "old-master-key-used-before-rotation"
        encrypted = encrypt_secret("rotated-secret-value", "smtp")
        settings.secret_key = "new-master-key-used-after-rotation"
        settings.secret_key_previous = "old-master-key-used-before-rotation"
        assert decrypt_secret(encrypted, "smtp") == "rotated-secret-value"
    finally:
        settings.secret_key = current
        settings.secret_key_previous = previous
