from types import SimpleNamespace

from django.test import SimpleTestCase

from retrieval.identity import (
    TrustedIdentityUnavailable,
    normalize_trusted_email,
    trusted_user_email,
)


class TrustedIdentityTests(SimpleTestCase):
    def test_normalization_matches_the_spicedb_subject_form(self):
        self.assertEqual(
            normalize_trusted_email("  Trusted.Reader@Example.COM  "),
            "trusted.reader@example.com",
        )

    def test_invalid_email_is_rejected(self):
        for value in (None, "", "not-an-email"):
            with self.subTest(value=value):
                with self.assertRaises(TrustedIdentityUnavailable):
                    normalize_trusted_email(value)

    def test_server_authenticated_user_uses_the_shared_normalizer(self):
        user = SimpleNamespace(
            is_authenticated=True,
            email="  Trusted.Reader@Example.COM  ",
        )
        self.assertEqual(trusted_user_email(user), "trusted.reader@example.com")

    def test_unauthenticated_user_is_rejected_before_email_normalization(self):
        user = SimpleNamespace(is_authenticated=False, email="reader@example.com")
        with self.assertRaises(TrustedIdentityUnavailable):
            trusted_user_email(user)
