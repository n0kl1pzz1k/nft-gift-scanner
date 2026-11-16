import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import marketplace


class FakeRedis:
    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []

    def eval(self, script, key_count, key, window):
        self.calls.append((script, key_count, key, window))
        if self.error:
            raise self.error
        return self.results.pop(0)


class RedisRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "rate-limit.sqlite3")
        )
        self.db_patch.start()
        marketplace.init_marketplace_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_redis_counter_is_atomic_and_enforces_limit(self):
        client = FakeRedis(results=[[1, 60], [2, 59], [3, 58]])
        with patch.object(marketplace, "_get_redis_rate_limit_client", return_value=client):
            marketplace._enforce_user_rate_limit("user-1", "create_listing", 2, 60)
            marketplace._enforce_user_rate_limit("user-1", "create_listing", 2, 60)
            with self.assertRaises(HTTPException) as raised:
                marketplace._enforce_user_rate_limit("user-1", "create_listing", 2, 60)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers["Retry-After"], "58")
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(call[1] == 1 for call in client.calls))
        self.assertTrue(all(call[0] == marketplace._REDIS_RATE_LIMIT_SCRIPT for call in client.calls))
        self.assertEqual(len({call[2] for call in client.calls}), 1)

    def test_redis_failure_falls_back_to_persistent_sqlite_limit(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        client = FakeRedis(error=RedisConnectionError("redis unavailable"))
        with patch.object(marketplace, "_get_redis_rate_limit_client", return_value=client):
            marketplace._enforce_user_rate_limit("user-2", "create_listing", 1, 60)
            with self.assertRaises(HTTPException) as raised:
                marketplace._enforce_user_rate_limit("user-2", "create_listing", 1, 60)

        self.assertEqual(raised.exception.status_code, 429)
        with marketplace._db() as connection:
            row = connection.execute(
                "SELECT request_count FROM api_rate_limits WHERE user_id = ? AND action = ?",
                ("user-2", "create_listing"),
            ).fetchone()
        self.assertEqual(row["request_count"], 1)

    def test_no_redis_url_keeps_sqlite_rate_limiter(self):
        with patch.dict(os.environ, {"REDIS_URL": ""}):
            with patch.object(marketplace, "_redis_rate_limit_client", None):
                marketplace._enforce_user_rate_limit("user-3", "create_listing", 1, 60)
                with self.assertRaises(HTTPException):
                    marketplace._enforce_user_rate_limit("user-3", "create_listing", 1, 60)


if __name__ == "__main__":
    unittest.main()
