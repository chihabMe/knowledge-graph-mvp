import logging

from django.test import SimpleTestCase

from config.logging_filters import RedactOAuthCallbackQuery


class OAuthCallbackLoggingFilterTests(SimpleTestCase):
    def test_callback_query_is_removed_from_request_line(self):
        record = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='"%s" %s %s',
            args=(
                "GET /api/drive/oauth/callback?code=secret&state=secret HTTP/1.1",
                "302",
                "0",
            ),
            exc_info=None,
        )

        self.assertTrue(RedactOAuthCallbackQuery().filter(record))
        self.assertEqual(
            record.args[0],
            "GET /api/drive/oauth/callback?redacted=1 HTTP/1.1",
        )

    def test_non_callback_request_is_unchanged(self):
        request_line = "GET /api/health/?state=ordinary HTTP/1.1"
        record = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='"%s" %s %s',
            args=(request_line, "200", "99"),
            exc_info=None,
        )

        RedactOAuthCallbackQuery().filter(record)

        self.assertEqual(record.args[0], request_line)
