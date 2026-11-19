import tempfile
import unittest
from pathlib import Path

import marketplace


class TelegramUpdateInboxIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = marketplace.DB_PATH
        marketplace.DB_PATH = str(Path(self.temp_dir.name) / "test-gift-radar.sqlite3")
        marketplace.init_marketplace_db()

    def tearDown(self):
        marketplace.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_same_update_is_not_processed_twice(self):
        payload = {"update_id": 9001, "message": {"text": "/start"}}
        self.assertEqual(marketplace.claim_telegram_update(9001, payload), "claimed")
        self.assertEqual(marketplace.claim_telegram_update(9001, payload), "in_progress")

        marketplace.finish_telegram_update(9001)

        self.assertEqual(marketplace.claim_telegram_update(9001, payload), "processed")

    def test_same_update_id_with_different_payload_is_rejected(self):
        first = {"update_id": 9002, "message": {"text": "/start"}}
        changed = {"update_id": 9002, "message": {"text": "/admin"}}
        self.assertEqual(marketplace.claim_telegram_update(9002, first), "claimed")
        self.assertEqual(marketplace.claim_telegram_update(9002, changed), "conflict")

    def test_failed_update_can_be_retried_after_release(self):
        payload = {"update_id": 9003, "message": {"text": "/help"}}
        self.assertEqual(marketplace.claim_telegram_update(9003, payload), "claimed")
        marketplace.release_telegram_update(9003)
        self.assertEqual(marketplace.claim_telegram_update(9003, payload), "claimed")


if __name__ == "__main__":
    unittest.main()
