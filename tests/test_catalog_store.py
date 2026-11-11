import os
import tempfile
import unittest
from unittest.mock import patch

import marketplace
from catalog_store import get_catalogue_item, upsert_catalogue_items


class PersistentCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "catalogue.sqlite3"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_catalogue_metadata_is_persisted_and_refreshed_by_source_id(self):
        first = {"id": "portals-42", "name": "Plush Pepe", "number": 42, "source": "Portals"}
        self.assertEqual(upsert_catalogue_items([first]), 1)
        stored = get_catalogue_item("portals-42")
        self.assertEqual(stored["name"], "Plush Pepe")
        self.assertEqual(stored["catalogue_source"], "Portals")
        refreshed = {**first, "price": "3.25"}
        self.assertEqual(upsert_catalogue_items([refreshed]), 1)
        stored_again = get_catalogue_item("portals-42")
        self.assertEqual(stored_again["price"], "3.25")
        self.assertEqual(stored_again["first_seen_at"], stored["first_seen_at"])
        self.assertGreaterEqual(stored_again["last_seen_at"], stored["last_seen_at"])

    def test_missing_and_blank_ids_are_not_indexed(self):
        self.assertEqual(upsert_catalogue_items([{"name": "No ID"}, {"id": "  ", "name": "Blank"}]), 0)
        self.assertIsNone(get_catalogue_item("unknown"))
