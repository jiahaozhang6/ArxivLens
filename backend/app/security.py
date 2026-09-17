import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


class SecretDecryptionError(ValueError):
    pass


_VERSION = "v2"


def _configured_secrets() -> list[str]:
    settings = get_settings()
    return list(
        dict.fromkeys(
            secret for secret in (settings.secret_key, settings.secret_key_previous) if secret
        )
    )


def _fernet(secret: str, purpose: str) -> Fernet:
    key_material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"arxiv-lens-secret-storage-v2",
        info=f"arxiv-lens:{purpose}".encode(),
    ).derive(secret.encode("utf-8"))
    key = base64.urlsafe_b64encode(key_material)
    return Fernet(key)


def _legacy_fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str | None, purpose: str = "general") -> str | None:
    if not value:
        return None
    token = _fernet(get_settings().secret_key, purpose).encrypt(value.encode("utf-8"))
    return f"{_VERSION}:{purpose}:{token.decode('ascii')}"


def decrypt_secret(value: str | None, purpose: str = "general") -> str | None:
    if not value:
        return None
    if value.startswith(f"{_VERSION}:"):
        try:
            version, stored_purpose, token = value.split(":", 2)
        except ValueError as exc:
            raise SecretDecryptionError("Stored secret has an invalid encrypted format.") from exc
        if version != _VERSION or stored_purpose != purpose:
            raise SecretDecryptionError("Stored secret cannot be used for this purpose.")
        for secret in _configured_secrets():
            try:
                return _fernet(secret, purpose).decrypt(token.encode("ascii")).decode("utf-8")
            except InvalidToken:
                continue
    else:
        for secret in _configured_secrets():
            try:
                return _legacy_fernet(secret).decrypt(value.encode("ascii")).decode("utf-8")
            except InvalidToken:
                continue
    raise SecretDecryptionError(
        "Cannot decrypt a stored secret. Check SECRET_KEY and SECRET_KEY_PREVIOUS."
    )
