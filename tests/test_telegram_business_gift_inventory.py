import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import app
import marketplace
from telegram_business_gift_inventory import (
    fetch_business_account_gifts,
    fetch_sticker_preview,
    normalise_business_owned_gift,
)


class TelegramBusinessGiftInventoryTests(unittest.IsolatedAsyncioTestCase):
    def test_normalises_unique_owned_gift_with_transfer_handle(self):
        item = normalise_business_owned_gift({
            "type": "unique",
            "owned_gift_id": "owned-unique-42",
            "send_date": 1760000000,
            "gift": {
                "name": "PlushPepe-42",
                "base_name": "Plush Pepe",
                "number": 42,
                "model": {"sticker": {"file_id": "model-preview-file", "file_unique_id": "model-preview-unique"}},
            },
        })
        self.assertEqual(item["owned_gift_id"], "owned-unique-42")
        self.assertEqual(item["external_asset_id"], "PlushPepe-42")
        self.assertEqual(item["gift_url"], "https://t.me/nft/PlushPepe-42")
        self.assertEqual(item["model_sticker_file_id"], "model-preview-file")

    def test_uses_symbol_thumbnail_when_model_has_no_raster_preview(self):
        item = normalise_business_owned_gift({
            "type": "unique",
            "owned_gift_id": "owned-symbol-7",
            "gift": {
                "name": "Gift-7",
                "base_name": "Gift",
                "number": 7,
                "model": {"sticker": {"file_id": "animated-model", "is_animated": True}},
                "symbol": {"sticker": {
                    "file_id": "animated-symbol",
                    "is_animated": True,
                    "thumbnail": {"file_id": "symbol-static-thumb"},
                }},
            },
        })
        self.assertEqual(item["model_sticker_file_id"], "symbol-static-thumb")

    def test_rejects_missing_owned_gift_id_and_blockchain_gifts(self):
        self.assertIsNone(normalise_business_owned_gift({
            "type": "unique", "gift": {"name": "Gift-1", "number": 1}
        }))
        self.assertIsNone(normalise_business_owned_gift({
            "type": "unique", "owned_gift_id": "id",
            "gift": {"name": "Gift-1", "number": 1, "is_from_blockchain": True},
        }))

    async def test_downloads_only_small_static_image_previews(self):
        image_bytes = b"RIFF" + bytes.fromhex("04000000") + b"WEBP"
        def handler(request):
            if request.method == "POST":
                self.assertTrue(request.url.path.endswith("/getFile"))
                return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/model.webp"}})
            self.assertEqual(request.url.path, "/file/bot123456:test-token/photos/model.webp")
            return httpx.Response(200, content=image_bytes, headers={"content-type": "image/webp"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
        with patch.dict(os.environ, {"BOT_TOKEN": "123456:test-token"}):
            data, media_type = await fetch_sticker_preview("model-file-id", client=client)
        await client.aclose()
        self.assertEqual(data, image_bytes)
        self.assertEqual(media_type, "image/webp")

    async def test_fetches_business_gifts_and_paginates(self):
        calls = []
        def handler(request):
            calls.append(request)
            payload = __import__("json").loads(request.content)
            self.assertEqual(payload["business_connection_id"], "business-101")
            self.assertTrue(payload["exclude_from_blockchain"])
            if payload["offset"] == "":
                return httpx.Response(200, json={
                    "ok": True,
                    "result": {
                        "total_count": 2,
                        "gifts": [{
                            "type": "unique", "owned_gift_id": "owned-1",
                            "gift": {"name": "Gift-1", "number": 1, "base_name": "Gift"},
                        }],
                        "next_offset": "next",
                    },
                })
            return httpx.Response(200, json={
                "ok": True,
                "result": {
                    "total_count": 2,
                    "gifts": [{
                        "type": "unique", "owned_gift_id": "owned-2",
                        "gift": {"name": "Gift-2", "number": 2, "base_name": "Gift"},
                    }],
                },
            })
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telegram.org",
        )
        with patch.dict(os.environ, {"BOT_TOKEN": "123456:test-token"}):
            result = await fetch_business_account_gifts("business-101", client=client)
        await client.aclose()
        self.assertTrue(result["complete"])
        self.assertEqual([x["owned_gift_id"] for x in result["items"]], ["owned-1", "owned-2"])
        self.assertEqual(len(calls), 2)


class TelegramBusinessGiftImportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            marketplace, "DB_PATH",
            os.path.join(self.temp_dir.name, "business-gifts.sqlite3"),
        )
        self.db_patch.start()
        marketplace.init_marketplace_db()
        self.auth_patch = patch.object(
            app, "_authenticate_telegram_credential",
            return_value={"id": "101"},
        )
        self.auth_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_import_requires_business_connection_with_view_right(self):
        response = self.client.post(
            "/api/marketplace/assets/import-business-gifts",
            headers={"X-Telegram-Init-Data": "verified-session"},
        )
        self.assertEqual(response.status_code, 409)

    def test_import_saves_owned_gift_id_separately_from_public_gift_slug(self):
        marketplace.upsert_telegram_business_connection({
            "id": "business-connection-101",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {
                "can_view_gifts_and_stars": True,
                "can_transfer_and_upgrade_gifts": True,
            },
        })
        snapshot = {
            "complete": True,
            "total_count": 1,
            "supported_unique_count": 1,
            "items": [{
                "owned_gift_id": "owned-gift-instance-42",
                "external_asset_id": "PlushPepe-42",
                "title": "Plush Pepe #42",
                "gift_url": "https://t.me/nft/PlushPepe-42",
                "send_date": "1760000000",
                "model_sticker_file_id": "model-preview-file",
            }],
        }
        with patch.object(app, "fetch_business_account_gifts", new=AsyncMock(return_value=snapshot)):
            response = self.client.post(
                "/api/marketplace/assets/import-business-gifts",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 1)
        asset_id = response.json()["items"][0]["id"]
        ref = marketplace.get_telegram_business_gift_ref(asset_id, "101")
        self.assertEqual(ref["owned_gift_id"], "owned-gift-instance-42")
        self.assertEqual(ref["gift_slug"], "PlushPepe-42")
        preview_ref = marketplace.get_telegram_business_gift_preview_ref(asset_id, "101")
        self.assertEqual(preview_ref["model_sticker_file_id"], "model-preview-file")

    def test_primary_telegram_import_prefers_business_when_view_permission_is_granted(self):
        marketplace.upsert_telegram_business_connection({
            "id": "business-connection-101",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {"can_view_gifts_and_stars": True},
        })
        snapshot = {
            "complete": True,
            "total_count": 1,
            "supported_unique_count": 1,
            "items": [{
                "owned_gift_id": "owned-primary-1",
                "external_asset_id": "PrimaryGift-1",
                "title": "Primary Gift #1",
                "gift_url": "https://t.me/nft/PrimaryGift-1",
                "send_date": "1760000000",
                "model_sticker_file_id": "primary-preview-file",
            }],
        }
        with patch.object(app, "fetch_business_account_gifts", new=AsyncMock(return_value=snapshot)) as business_fetch:
            with patch.object(app, "fetch_user_telegram_gifts", new=AsyncMock()) as regular_fetch:
                response = self.client.post(
                    "/api/marketplace/assets/import-telegram",
                    headers={"X-Telegram-Init-Data": "verified-session"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 1)
        business_fetch.assert_awaited_once_with("business-connection-101")
        regular_fetch.assert_not_awaited()
        asset_id = response.json()["items"][0]["id"]
        preview_ref = marketplace.get_telegram_business_gift_preview_ref(asset_id, "101")
        self.assertEqual(preview_ref["model_sticker_file_id"], "primary-preview-file")

    def test_owner_can_fetch_preview_with_view_permission_without_transfer_permission(self):
        marketplace.upsert_telegram_business_connection({
            "id": "business-connection-101",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {"can_view_gifts_and_stars": True, "can_transfer_and_upgrade_gifts": False},
        })
        snapshot = {
            "complete": True, "total_count": 1, "supported_unique_count": 1,
            "items": [{
                "owned_gift_id": "owned-preview-1", "external_asset_id": "PreviewGift-1",
                "title": "Preview Gift #1", "gift_url": "https://t.me/nft/PreviewGift-1",
                "send_date": "1760000000", "model_sticker_file_id": "preview-file-id",
            }],
        }
        with patch.object(app, "fetch_business_account_gifts", new=AsyncMock(return_value=snapshot)):
            imported = self.client.post("/api/marketplace/assets/import-business-gifts", headers={"X-Telegram-Init-Data": "verified-session"})
        self.assertEqual(imported.status_code, 200, imported.text)
        asset_id = imported.json()["items"][0]["id"]
        image_bytes = b"RIFF" + bytes.fromhex("04000000") + b"WEBP"
        with patch.object(app, "fetch_sticker_preview", new=AsyncMock(return_value=(image_bytes, "image/webp"))):
            response = self.client.get(f"/api/marketplace/assets/{asset_id}/preview", headers={"X-Telegram-Init-Data": "verified-session"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, image_bytes)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_incomplete_snapshot_does_not_prune_previous_transfer_handles(self):
        marketplace.upsert_telegram_business_connection({
            "id": "business-connection-101",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {
                "can_view_gifts_and_stars": True,
                "can_transfer_and_upgrade_gifts": True,
            },
        })
        asset = marketplace.register_verified_asset(
            source="telegram_gift", external_asset_id="Gift-Old-7",
            owner_telegram_id="101", title="Gift Old #7", asset_type="gift",
            image_url="", verification_method="telegram_gift_owner",
            verification_reference="test-business-gift-ownership-ref-123",
        )
        marketplace.save_telegram_business_gift_ref(
            asset_id=asset["id"], owner_telegram_id="101",
            business_connection_id="business-connection-101",
            owned_gift_id="owned-old-7", gift_slug="Gift-Old-7",
        )
        snapshot = {
            "complete": False, "total_count": 200, "supported_unique_count": 0,
            "items": [],
        }
        with patch.object(app, "fetch_business_account_gifts", new=AsyncMock(return_value=snapshot)):
            response = self.client.post(
                "/api/marketplace/assets/import-business-gifts",
                headers={"X-Telegram-Init-Data": "verified-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(marketplace.get_telegram_business_gift_ref(asset["id"], "101"))


if __name__ == "__main__":
    unittest.main()
