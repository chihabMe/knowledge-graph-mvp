"""Dedicated authenticated encryption for per-user Drive refresh credentials."""

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings_validators import load_google_user_token_keyring


class CredentialEncryptionError(RuntimeError):
    """A controlled credential-encryption failure with no secret material."""


class CredentialDecryptionError(RuntimeError):
    """A controlled credential-decryption failure with no secret material."""


@dataclass(frozen=True, repr=False)
class EncryptedCredential:
    ciphertext: bytes
    key_version: str

    def __repr__(self) -> str:
        return "<EncryptedCredential redacted>"

    __str__ = __repr__


def _keyring() -> tuple[str, dict[str, str]]:
    try:
        return load_google_user_token_keyring(settings.GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE)
    except ImproperlyConfigured as exc:
        raise CredentialEncryptionError(
            "The credential encryption keyring is unavailable."
        ) from exc


def encrypt_refresh_credential(refresh_credential: str) -> EncryptedCredential:
    """Encrypt a non-empty refresh credential with the active key version."""
    if not isinstance(refresh_credential, str) or not refresh_credential:
        raise CredentialEncryptionError("A non-empty refresh credential is required.")
    active_version, keys = _keyring()
    try:
        ciphertext = Fernet(keys[active_version].encode("ascii")).encrypt(
            refresh_credential.encode("utf-8")
        )
    except (KeyError, UnicodeError, ValueError) as exc:
        raise CredentialEncryptionError("The refresh credential could not be encrypted.") from exc
    return EncryptedCredential(ciphertext=ciphertext, key_version=active_version)


def decrypt_refresh_credential(*, ciphertext: bytes, key_version: str) -> str:
    """Decrypt ciphertext only with its recorded version; unknown keys deny."""
    if not isinstance(ciphertext, bytes) or not ciphertext or not key_version:
        raise CredentialDecryptionError("Encrypted credential material is invalid.")
    try:
        _, keys = load_google_user_token_keyring(settings.GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE)
        encoded_key = keys[key_version]
        plaintext = Fernet(encoded_key.encode("ascii")).decrypt(ciphertext)
        credential = plaintext.decode("utf-8")
    except (ImproperlyConfigured, InvalidToken, KeyError, UnicodeError, ValueError) as exc:
        raise CredentialDecryptionError("The refresh credential could not be decrypted.") from exc
    if not credential:
        raise CredentialDecryptionError("The refresh credential could not be decrypted.")
    return credential
