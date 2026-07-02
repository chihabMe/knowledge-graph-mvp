from django.test import SimpleTestCase


class ProjectImportTests(SimpleTestCase):
    def test_django_project_imports(self):
        from django.conf import settings

        self.assertEqual(settings.ROOT_URLCONF, "config.urls")

