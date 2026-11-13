import unittest

from app import app


class ApiSurfaceSecurityTests(unittest.TestCase):
    def test_interactive_docs_and_openapi_schema_are_disabled(self):
        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.redoc_url)
        self.assertIsNone(app.openapi_url)


if __name__ == "__main__":
    unittest.main()
