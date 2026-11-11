import os
import tempfile
import unittest
from unittest.mock import patch

import marketplace
from catalog_store import get_catalogue_item, search_catalogue_items, upsert_catalogue_items


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

    def test_search_supports_bounded_pagination_and_filters(self):
        upsert_catalogue_items([
            {"id": "g-1", "name": "Plush Pepe", "number": 1, "collection_id": "plush"},
            {"id": "g-2", "name": "Durov Cap", "number": 2, "collection_id": "caps"},
            {"id": "g-3", "name": "Plush Pepe", "number": 3, "collection_id": "plush"},
        ])
        page = search_catalogue_items("Plush", "plush", limit=1, offset=0)
        self.assertEqual(page["total_count"], 2)
        self.assertEqual(page["limit"], 1)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["collection_id"], "plush")
        second = search_catalogue_items("Plush", "plush", limit=1, offset=1)
        self.assertEqual(second["total_count"], 2)
        self.assertNotEqual(page["items"][0]["id"], second["items"][0]["id"])

    def test_search_clamps_pagination(self):
        page = search_catalogue_items(limit=500, offset=-10)
        self.assertEqual(page["limit"], 50)
        self.assertEqual(page["offset"], 0)
