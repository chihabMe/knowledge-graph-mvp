import json
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from integrations.drive.token_encryption import (
    CredentialDecryptionError,
    EncryptedCredential,
    decrypt_refresh_credential,
    encrypt_refresh_credential,
)


class RefreshCredentialEncryptionTests(SimpleTestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.keyring_file = Path(self.temporary_directory.name) / "token-keyring.json"
        self._write_keyring(active_version="v2", versions=("v1", "v2"))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_keyring(self, *, active_version, versions):
        keys = {version: Fernet.generate_key().decode("ascii") for version in versions}
        self.keyring_file.write_text(
            json.dumps({"active_version": active_version, "keys": keys}),
            encoding="utf-8",
        )

    def test_round_trip_uses_ciphertext_and_records_active_version(self):
        refresh_credential = "test-refresh-credential-material"
        with override_settings(GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE=str(self.keyring_file)):
            encrypted = encrypt_refresh_credential(refresh_credential)
            decrypted = decrypt_refresh_credential(
                ciphertext=encrypted.ciphertext,
                key_version=encrypted.key_version,
            )

        self.assertEqual(encrypted.key_version, "v2")
        self.assertNotIn(refresh_credential.encode("utf-8"), encrypted.ciphertext)
        self.assertEqual(decrypted, refresh_credential)

    def test_old_ciphertext_remains_decryptable_while_old_key_is_present(self):
        with override_settings(GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE=str(self.keyring_file)):
            encrypted = encrypt_refresh_credential("rotatable-refresh-credential")
            original_payload = json.loads(self.keyring_file.read_text(encoding="utf-8"))
            original_payload["active_version"] = "v1"
            self.keyring_file.write_text(json.dumps(original_payload), encoding="utf-8")
            self.assertEqual(
                decrypt_refresh_credential(
                    ciphertext=encrypted.ciphertext,
                    key_version=encrypted.key_version,
                ),
                "rotatable-refresh-credential",
            )

    def test_wrong_or_missing_key_fails_closed(self):
        with override_settings(GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE=str(self.keyring_file)):
            encrypted = encrypt_refresh_credential("credential-that-must-not-leak")
            self._write_keyring(active_version="v3", versions=("v3",))
            with self.assertRaises(CredentialDecryptionError):
                decrypt_refresh_credential(
                    ciphertext=encrypted.ciphertext,
                    key_version=encrypted.key_version,
                )

    def test_encrypted_value_representation_is_always_redacted(self):
        value = EncryptedCredential(ciphertext=b"sensitive-ciphertext", key_version="v1")
        self.assertEqual(str(value), "<EncryptedCredential redacted>")
        self.assertEqual(repr(value), "<EncryptedCredential redacted>")
        self.assertNotIn("sensitive", repr(value))
