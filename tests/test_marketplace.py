import os
import tempfile
import sqlite3
import unittest
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch
import asyncio

from fastapi.testclient import TestClient

import app as api
import marketplace
import marketplace_offers
from marketplace_payments import PaymentStateError, create_payment_intent, post_ledger_transaction, record_dispute_resolution_approval, transition_deal_status, transition_payment_intent
from marketplace_stars import confirm_stars_payment, validate_stars_pre_checkout
from marketplace_offers import escalate_stalled_paid_deals
from ton_nft_inventory import build_nft_transfer_payload, normalise_ton_address
from telegram_business_gift_inventory import TelegramBusinessGiftInventoryError


class SharedMarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(marketplace, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.db_patch.start()
        self.offers_db_patch = patch.object(marketplace_offers, "DB_PATH", os.path.join(self.temp_dir.name, "test.sqlite3"))
        self.offers_db_patch.start()
        marketplace.init_marketplace_db()
        marketplace_offers.init_marketplace_offers_db()
        api.init_ton_connect_db()
        self.auth_patch = patch.object(
            api,
            "_verify_telegram_init_data",
            side_effect=lambda value: {
                "id": {"user-a": "101", "user-b": "202", "user-c": "303"}.get(value, "202"),
                "first_name": {"user-a": "Tester A", "user-b": "Tester B", "user-c": "Tester C"}.get(value, "Tester B"),
                "username": "tester",
            },
        )
        self.auth_patch.start()
        self.feature_flag_patch = patch.dict(os.environ, {"APP_ENV": "test", "HASSLE_VERIFIED_ASSETS_ENABLED": "true", "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true", "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true"})
        self.feature_flag_patch.start()
        self.credential_patch = patch.object(
            api,
            "_authenticate_telegram_credential",
            side_effect=lambda value: (
                api._verify_telegram_init_data(value)
                if value in ("user-a", "user-b", "user-c")
                else marketplace.get_telegram_session_user(value)
            ),
        )
        self.credential_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.credential_patch.stop()
        self.feature_flag_patch.stop()
        self.auth_patch.stop()
        self.offers_db_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _create_stars_deal_with_intent(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Stars checkout gift", "price": "10", "currency": "XTR"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "Stars checkout"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}",
            headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        invoice_payload = f"hassle:stars:{deal_id}:test"
        with marketplace_offers._db() as conn:
            transition_deal_status(
                conn, deal_id=deal_id, new_status="awaiting_payment",
                actor_user_id="202", idempotency_key=f"{deal_id}:checkout-request",
                event_type="deal_payment_requested", event_data={"provider": "telegram_stars"},
                now=1700000000,
            )
            intent = create_payment_intent(
                conn, deal_id=deal_id, provider="telegram_stars", currency="XTR",
                amount="9", idempotency_key=f"{deal_id}:stars-intent",
                invoice_payload=invoice_payload, expires_at=1700000900,
                metadata={"buyer_telegram_id": "202", "deal_id": deal_id},
                now=1700000000,
            )
            conn.commit()
        return deal_id, invoice_payload, intent["id"]

    def test_seller_response_to_buy_request_is_rejected_until_asset_binding_exists(self):
        buyer_headers = {"X-Telegram-Init-Data": "user-a"}
        seller_headers = {"X-Telegram-Init-Data": "user-b"}
        buy_request = self.client.post(
            "/api/marketplace/orders",
            headers=buyer_headers,
            json={"kind": "buy-order", "title": "Wanted collectible gift", "price": "25", "currency": "TON"},
        )
        self.assertEqual(buy_request.status_code, 201, buy_request.text)
        response = self.client.post(
            "/api/marketplace/offers",
            headers=seller_headers,
            json={
                "order_id": buy_request.json()["item"]["id"],
                "amount": "24",
                "note": "I have a collectible gift",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("specific verified asset", response.json()["detail"])
        with marketplace_offers._db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM marketplace_offers").fetchone()[0]
        self.assertEqual(count, 0)

    def test_telegram_business_gift_delivery_transfers_once_and_records_buyer_ownership(self):
        asset = marketplace.register_verified_asset(
            source="telegram_gift",
            external_asset_id="test-business-gift-unique-9001",
            owner_telegram_id="101",
            title="Business Gift #9001",
            asset_type="gift",
            image_url="",
            verification_method="telegram_gift_owner",
            verification_reference="business-gift-proof-reference-9001",
            allow_owner_transfer=True,
        )
        marketplace.upsert_telegram_business_connection({
            "id": "business-connection-101",
            "user": {"id": 101},
            "user_chat_id": 101,
            "is_enabled": True,
            "rights": {
                "can_view_gifts_and_stars": True,
                "can_transfer_and_upgrade_gifts": True,
                "can_transfer_stars": True,
            },
        })
        marketplace.save_telegram_business_gift_ref(
            asset_id=asset["id"],
            owner_telegram_id="101",
            business_connection_id="business-connection-101",
            owned_gift_id="owned-gift-9001",
            gift_slug="BusinessGift-9001",
        )
        listing = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={
                "kind": "listing", "title": "Business Gift #9001", "price": "9",
                "currency": "XTR", "asset_id": asset["id"],
            },
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "buy"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            transition_deal_status(
                conn, deal_id=deal_id, new_status="awaiting_payment",
                actor_user_id="202", idempotency_key=f"{deal_id}:test-awaiting",
                event_type="deal_payment_requested", event_data={"provider": "telegram_stars"},
                now=1700000000,
            )
            intent = create_payment_intent(
                conn, deal_id=deal_id, provider="telegram_stars", currency="XTR",
                amount="9", idempotency_key=f"{deal_id}:test-business-gift-payment",
                invoice_payload=f"hassle:test:{deal_id}", expires_at=1700000900,
                metadata={"buyer_telegram_id": "202"}, now=1700000000,
            )
            transition_payment_intent(conn, intent["id"], "pending", now=1700000001)
            transition_payment_intent(
                conn, intent["id"], "confirmed",
                provider_reference="test-business-gift-charge", now=1700000002,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="payment_pending",
                actor_user_id="202", idempotency_key=f"{deal_id}:test-payment-pending",
                event_type="payment_pending", event_data={"provider": "telegram_stars"},
                now=1700000001,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="paid",
                actor_user_id="202", idempotency_key=f"{deal_id}:test-payment-confirmed",
                event_type="payment_confirmed", event_data={"provider": "telegram_stars"},
                now=1700000002,
            )
            post_ledger_transaction(
                conn, idempotency_key=f"{deal_id}:test-business-gift-escrow",
                reference_type="deal", reference_id=deal_id, currency="XTR",
                debit_account=("telegram_stars_clearing", "test-business-gift-charge"),
                credit_account=("escrow", deal_id), debit_amount="9", credit_amount="9",
                now=1700000002,
            )
            conn.commit()

        live_inventory = {
            "complete": True,
            "items": [{
                "owned_gift_id": "owned-gift-9001",
                "can_be_transferred": True,
                "transfer_star_count": 0,
                "next_transfer_date": 0,
            }],
        }
        with patch(
            "telegram_business_gift_inventory.fetch_business_account_gifts",
            new=AsyncMock(return_value=live_inventory),
        ), patch(
            "telegram_business_gift_inventory.transfer_business_account_gift",
            new=AsyncMock(side_effect=TelegramBusinessGiftInventoryError(
                "Telegram rejected transfer", definitive_rejection=True,
            )),
        ) as rejected_transfer:
            rejected = self.client.post(
                f"/api/marketplace/deals/{deal_id}/telegram-business-gift-delivery/transfer",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": "business-transfer-rejected-9000"},
            )
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertIn("rejected", rejected.text.lower())
            rejected_transfer.assert_awaited_once()

        with patch(
            "telegram_business_gift_inventory.fetch_business_account_gifts",
            new=AsyncMock(return_value=live_inventory),
        ), patch(
            "telegram_business_gift_inventory.transfer_business_account_gift",
            new=AsyncMock(return_value=None),
        ) as same_key_retry_transfer:
            same_key_retry = self.client.post(
                f"/api/marketplace/deals/{deal_id}/telegram-business-gift-delivery/transfer",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": "business-transfer-rejected-9000"},
            )
            self.assertEqual(same_key_retry.status_code, 409, same_key_retry.text)
            same_key_retry_transfer.assert_not_awaited()

        original_transition = transition_deal_status
        fail_completion_once = {"pending": True}
        def injected_finalization_failure(conn, **kwargs):
            if kwargs.get("new_status") == "completed" and fail_completion_once["pending"]:
                fail_completion_once["pending"] = False
                raise RuntimeError("injected post-provider finalization failure")
            return original_transition(conn, **kwargs)

        with patch(
            "telegram_business_gift_inventory.fetch_business_account_gifts",
            new=AsyncMock(return_value=live_inventory),
        ), patch(
            "telegram_business_gift_inventory.transfer_business_account_gift",
            new=AsyncMock(return_value=None),
        ) as transfer, patch(
            "marketplace_payments.transition_deal_status",
            side_effect=injected_finalization_failure,
        ):
            uncertain = self.client.post(
                f"/api/marketplace/deals/{deal_id}/telegram-business-gift-delivery/transfer",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": "business-transfer-9001"},
            )
            self.assertEqual(uncertain.status_code, 409, uncertain.text)
            self.assertIn("reconciliation", uncertain.text)
            # The provider already confirmed the irreversible transfer. A fresh
            # request key must resume DB finalization without calling Telegram again.
            response = self.client.post(
                f"/api/marketplace/deals/{deal_id}/telegram-business-gift-delivery/transfer",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": "business-transfer-recovery-9002"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "completed")
            with patch("marketplace_offers._enforce_user_rate_limit"):
                replay = self.client.post(
                    f"/api/marketplace/deals/{deal_id}/telegram-business-gift-delivery/transfer",
                    headers={"X-Telegram-Init-Data": "user-a"},
                    json={"idempotency_key": "business-transfer-9001"},
                )
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json()["idempotent_replay"])
            transfer.assert_awaited_once()

        with marketplace._db() as conn:
            owner = conn.execute(
                "SELECT owner_telegram_id FROM verified_assets WHERE id=?", (asset["id"],)
            ).fetchone()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
            history = conn.execute(
                "SELECT previous_owner_id,new_owner_id FROM asset_ownership_history WHERE asset_id=?",
                (asset["id"],),
            ).fetchall()
            seller_credit = conn.execute(
                """SELECT account_id,amount FROM marketplace_ledger_entries
                   WHERE account_type='seller_payable' AND transaction_id IN
                     (SELECT id FROM marketplace_ledger_transactions WHERE idempotency_key=?)""",
                (f"{deal_id}:seller-payable:XTR",),
            ).fetchone()
            gift_ref = conn.execute(
                "SELECT 1 FROM telegram_business_gift_refs WHERE asset_id=?", (asset["id"],)
            ).fetchone()
        self.assertEqual(owner["owner_telegram_id"], "202")
        self.assertEqual(deal["status"], "completed")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["previous_owner_id"], "101")
        self.assertEqual(history[0]["new_owner_id"], "202")
        self.assertEqual(seller_credit["account_id"], "101")
        self.assertEqual(seller_credit["amount"], "9")
        self.assertIsNone(gift_ref)

    def test_ton_nft_delivery_requires_live_buyer_ownership_before_completion(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft",
            external_asset_id="0:" + "33" * 32,
            owner_telegram_id="101",
            title="Verified TON gift",
            asset_type="gift",
            image_url="",
            verification_method="ton_connect_proof",
            verification_reference="seller-proof-reference-001",
        )
        seller_address = "0:" + "11" * 32
        buyer_address = "0:" + "22" * 32
        with marketplace._db() as conn:
            for telegram_id, address in (("101", seller_address), ("202", buyer_address)):
                conn.execute(
                    """INSERT INTO verified_wallets
                       (network, address, telegram_id, public_key, state_init_hash,
                        created_at, last_verified_at, revoked_at)
                       VALUES ('-239', ?, ?, ?, ?, 1700000000, 1700000000, NULL)""",
                    (address, telegram_id, "ab" * 32, "cd" * 32),
                )
            conn.commit()
        listing = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={
                "kind": "listing", "title": "Verified TON gift", "price": "10",
                "currency": "XTR", "asset_id": asset["id"],
            },
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "buy"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            conn.execute("UPDATE marketplace_deals SET status='paid' WHERE id=?", (deal_id,))
            post_ledger_transaction(
                conn,
                idempotency_key=f"{deal_id}:test-escrow-funding",
                reference_type="deal",
                reference_id=deal_id,
                currency="XTR",
                debit_account=("telegram_stars_clearing", "test-charge"),
                credit_account=("escrow", deal_id),
                debit_amount="9",
                credit_amount="9",
                now=1700000000,
            )
            conn.commit()

        seller_owner = {"owner_address": seller_address, "is_scam": False}
        buyer_owner = {"owner_address": buyer_address, "is_scam": False}
        with patch("ton_nft_inventory.fetch_nft_owner", new=AsyncMock(return_value=seller_owner)):
            prepared = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-nft-delivery/prepare",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": "prepare-ton-transfer-001"},
            )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(prepared.json()["status"], "delivery_pending")
        self.assertEqual(prepared.json()["transaction"]["network"], "-239")
        self.assertEqual(prepared.json()["transaction"]["messages"][0]["address"], asset["external_asset_id"])

        with patch("ton_nft_inventory.fetch_nft_owner", new=AsyncMock(return_value=seller_owner)):
            prepared_again = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-nft-delivery/prepare",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": "prepare-ton-transfer-002"},
            )
        self.assertEqual(prepared_again.status_code, 200, prepared_again.text)
        self.assertTrue(prepared_again.json()["idempotent_replay"])
        self.assertEqual(
            prepared_again.json()["transaction"]["messages"][0]["payload"],
            prepared.json()["transaction"]["messages"][0]["payload"],
        )

        with patch("ton_nft_inventory.fetch_nft_owner", new=AsyncMock(return_value=seller_owner)), patch(
            "ton_nft_inventory.fetch_wallet_nft_history", new=AsyncMock(return_value=[]),
        ):
            not_yet = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-nft-delivery/confirm",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "confirm-ton-transfer-too-early"},
            )
        self.assertEqual(not_yet.status_code, 409)

        with patch("ton_nft_inventory.fetch_nft_owner", new=AsyncMock(return_value=buyer_owner)), patch(
            "ton_nft_inventory.fetch_wallet_nft_history", new=AsyncMock(return_value=[]),
        ):
            no_transfer_proof = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-nft-delivery/confirm",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "confirm-ton-transfer-without-history"},
            )
        self.assertEqual(no_transfer_proof.status_code, 409, no_transfer_proof.text)
        self.assertIn("history", no_transfer_proof.text.lower())

        transfer_history = [{
            "operation": "transfer",
            "utime": int(time.time()) + 10,
            "lt": 123456789,
            "transaction_hash": "ef" * 32,
            "item": {"address": asset["external_asset_id"]},
            "source": {"address": seller_address},
            "destination": {"address": buyer_address},
        }]
        with patch("ton_nft_inventory.fetch_nft_owner", new=AsyncMock(return_value=buyer_owner)), patch(
            "ton_nft_inventory.fetch_wallet_nft_history", new=AsyncMock(return_value=transfer_history),
        ):
            confirmed = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-nft-delivery/confirm",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "confirm-ton-transfer-001"},
            )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["status"], "completed")
        with marketplace._db() as conn:
            current_asset = conn.execute(
                "SELECT owner_telegram_id FROM verified_assets WHERE id=?", (asset["id"],)
            ).fetchone()
            ownership = conn.execute(
                "SELECT previous_owner_id, new_owner_id FROM asset_ownership_history WHERE asset_id=?",
                (asset["id"],),
            ).fetchall()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
            seller_credit = conn.execute(
                """SELECT e.account_type, e.account_id, e.direction, e.amount
                   FROM marketplace_ledger_entries e
                   JOIN marketplace_ledger_transactions t ON t.id = e.transaction_id
                   WHERE t.idempotency_key = ? AND e.account_type = 'seller_payable'""",
                (f"{deal_id}:seller-payable:XTR",),
            ).fetchone()
            seller_credit_event = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='seller_credit_recorded'",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(current_asset["owner_telegram_id"], "202")
        self.assertEqual(len(ownership), 1)
        self.assertEqual(deal["status"], "completed")
        self.assertIsNotNone(seller_credit)
        self.assertEqual(seller_credit["account_id"], "101")
        self.assertEqual(seller_credit["direction"], "credit")
        self.assertEqual(seller_credit["amount"], "9")
        self.assertEqual(seller_credit_event, 1)

    def test_ton_nft_transfer_payload_encodes_standard_tep62_body(self):
        payload = build_nft_transfer_payload(
            new_owner_address="0:" + "11" * 32,
            response_destination="0:" + "22" * 32,
            query_id=123456789,
        )
        import base64
        from tonsdk.boc import Cell
        raw = base64.b64decode(payload, validate=True)
        parsed = Cell.one_from_boc(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(normalise_ton_address("0:" + "11" * 32), "0:" + "11" * 32)

    def test_two_admin_approved_stars_refund_calls_telegram_and_reverses_escrow_ledger(self):
        deal_id, invoice_payload, intent_id = self._create_stars_deal_with_intent()
        ok, _ = validate_stars_pre_checkout(
            invoice_payload=invoice_payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000010,
        )
        self.assertTrue(ok)
        confirm_stars_payment(
            invoice_payload=invoice_payload,
            telegram_user_id="202",
            currency="XTR",
            total_amount=9,
            telegram_payment_charge_id="tg-refund-charge-001",
            now=1700000020,
        )
        with marketplace_offers._db() as conn:
            conn.execute("UPDATE marketplace_deals SET status='disputed' WHERE id=?", (deal_id,))
            record_dispute_resolution_approval(
                conn, deal_id=deal_id, admin_user_id="101", decision="refund_buyer",
                idempotency_key=f"{deal_id}:refund-admin-101", now=1700000030,
            )
            record_dispute_resolution_approval(
                conn, deal_id=deal_id, admin_user_id="303", decision="refund_buyer",
                idempotency_key=f"{deal_id}:refund-admin-303", now=1700000040,
            )
            conn.commit()

        class FakeTelegramResponse:
            status_code = 200
            def json(self):
                return {"ok": True, "result": True}

        class FakeTelegramClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.calls = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def post(self, url, json):
                self.calls.append((url, json))
                return FakeTelegramResponse()

        fake_client = FakeTelegramClient()
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101,303", "BOT_TOKEN": "test-bot-token"}):
            with patch("httpx.AsyncClient", return_value=fake_client):
                response = self.client.post(
                    f"/api/marketplace/admin/deals/{deal_id}/execute-refund",
                    headers={"X-Telegram-Init-Data": "user-a"},
                    json={"idempotency_key": f"{deal_id}:refund-execution-001"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["refund_status"], "refunded")
        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(fake_client.calls[0][1]["user_id"], 202)
        self.assertEqual(fake_client.calls[0][1]["telegram_payment_charge_id"], "tg-refund-charge-001")
        with marketplace_offers._db() as conn:
            intent = conn.execute(
                "SELECT status FROM marketplace_payment_intents WHERE id=?", (intent_id,)
            ).fetchone()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
            transactions = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE reference_id=?",
                (deal_id,),
            ).fetchone()[0]
            refund_events = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='refund_confirmed'",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(intent["status"], "refunded")
        self.assertEqual(deal["status"], "cancelled")
        self.assertEqual(transactions, 2)
        self.assertEqual(refund_events, 1)

        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101,303", "BOT_TOKEN": "test-bot-token"}):
            replay = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-refund",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"idempotency_key": f"{deal_id}:refund-execution-001"},
            )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(len(fake_client.calls), 1)

    def test_stars_checkout_creates_and_reuses_one_open_telegram_invoice(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Invoice route gift", "price": "10", "currency": "XTR"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "checkout"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}",
            headers=seller_headers, json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]

        class FakeTelegramResponse:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return {"ok": True, "result": "https://t.me/$hassle-invoice"}

        class FakeTelegramClient:
            def __init__(self, **kwargs):
                self.calls = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def post(self, url, json):
                self.calls.append((url, json))
                return FakeTelegramResponse()

        fake = FakeTelegramClient()
        flags = {
            "HASSLE_STARS_CHECKOUT_ENABLED": "1",
            "HASSLE_STARS_CUSTODY_ACKNOWLEDGED": "1",
            "HASSLE_STARS_PAYOUT_MODE": "internal_xtr_credit",
            "BOT_TOKEN": "test-bot-token",
            "PAYMENT_SUPPORT_CONTACT": "@hassle_support",
        }
        with patch.dict(os.environ, flags):
            buyer_deals = self.client.get("/api/marketplace/deals", headers=buyer_headers)
            self.assertTrue(buyer_deals.json()["items"][0]["payments_enabled"])
            with patch("httpx.AsyncClient", return_value=fake):
                first = self.client.post(
                    f"/api/marketplace/deals/{deal_id}/stars-checkout",
                    headers=buyer_headers,
                    json={"idempotency_key": "stars-invoice-first"},
                )
                second = self.client.post(
                    f"/api/marketplace/deals/{deal_id}/stars-checkout",
                    headers=buyer_headers,
                    json={"idempotency_key": "stars-invoice-second"},
                )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["payment_intent_id"], second.json()["payment_intent_id"])
        self.assertEqual(first.json()["invoice_url"], "https://t.me/$hassle-invoice")
        self.assertEqual(second.json()["invoice_url"], "https://t.me/$hassle-invoice")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[0][1]["payload"], fake.calls[1][1]["payload"])
        with marketplace_offers._db() as conn:
            open_intents = conn.execute(
                "SELECT COUNT(*) FROM marketplace_payment_intents WHERE deal_id=? AND status IN ('created','pending')",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(open_intents, 1)

    def _create_ton_deal_with_verified_wallets(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="0:" + "33" * 32,
            owner_telegram_id="101", title="TON checkout gift", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="ton-checkout-asset-proof-001",
        )
        seller_address = "0:" + "11" * 32
        buyer_address = "0:" + "22" * 32
        with marketplace._db() as conn:
            for telegram_id, address in (("101", seller_address), ("202", buyer_address)):
                conn.execute(
                    """INSERT INTO verified_wallets
                       (network, address, telegram_id, public_key, state_init_hash,
                        created_at, last_verified_at, revoked_at)
                       VALUES ('-239', ?, ?, ?, ?, ?, ?, NULL)""",
                    (address, telegram_id, "ab" * 32, "cd" * 32, int(time.time()), int(time.time())),
                )
            conn.commit()
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "TON checkout gift", "price": "10", "currency": "TON", "asset_id": asset["id"]},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "TON checkout"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}",
            headers=seller_headers, json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        return accepted.json()["deal_id"], seller_address, buyer_address

    def test_ton_checkout_creates_reusable_verified_wallet_invoice(self):
        deal_id, seller_wallet, buyer_wallet = self._create_ton_deal_with_verified_wallets()
        flags = {
            "HASSLE_TON_CHECKOUT_ENABLED": "1",
            "HASSLE_TON_CUSTODY_ACKNOWLEDGED": "1",
            "HASSLE_TON_PAYOUT_MODE": "manual_multisig_2_of_3",
            "HASSLE_TON_TREASURY_ADDRESS": "0:" + "aa" * 32,
            "TONAPI_TOKEN": "test-tonapi-token",
            "PAYMENT_SUPPORT_CONTACT": "@hassle_support",
        }
        with patch.dict(os.environ, flags), patch(
            "marketplace_offers._require_ton_multisig_2_of_3", new=AsyncMock(return_value=None),
        ), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address":seller_wallet,"is_scam":False}),
        ):
            buyer_deals = self.client.get("/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-b"})
            self.assertTrue(buyer_deals.json()["items"][0]["payments_enabled"])
            first = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-checkout",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "ton-invoice-first"},
            )
            second = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-checkout",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "ton-invoice-second"},
            )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["payment_intent_id"], second.json()["payment_intent_id"])
        self.assertEqual(first.json()["buyer_wallet"], buyer_wallet)
        self.assertEqual(first.json()["transaction"]["messages"][0]["amount"], "9000000000")
        self.assertEqual(first.json()["transaction"]["network"], "-239")
        self.assertEqual(first.json()["memo"], second.json()["memo"])
        with marketplace_offers._db() as conn:
            intent = conn.execute(
                "SELECT status, provider, currency FROM marketplace_payment_intents WHERE deal_id=?",
                (deal_id,),
            ).fetchone()
            deal = conn.execute("SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)).fetchone()
        self.assertEqual(intent["status"], "pending")
        self.assertEqual(intent["provider"], "ton")
        self.assertEqual(intent["currency"], "TON")
        self.assertEqual(deal["status"], "payment_pending")

    def test_two_admin_approved_ton_refund_requires_matching_outgoing_chain_transaction(self):
        deal_id, seller_wallet, buyer_wallet = self._create_ton_deal_with_verified_wallets()
        treasury = "0:" + "aa" * 32
        now = int(time.time())
        with marketplace_offers._db() as conn:
            transition_deal_status(
                conn, deal_id=deal_id, new_status="awaiting_payment", actor_user_id="202",
                idempotency_key=f"{deal_id}:ton-refund-test-awaiting",
                event_type="deal_payment_requested", event_data={}, now=now,
            )
            intent = create_payment_intent(
                conn, deal_id=deal_id, provider="ton", currency="TON", amount="9",
                idempotency_key=f"{deal_id}:ton-refund-test-intent",
                invoice_payload=f"ton-refund:{deal_id}", expires_at=now+900,
                metadata={"buyer_telegram_id":"202","buyer_wallet":buyer_wallet,"treasury":treasury}, now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="payment_pending", actor_user_id="202",
                idempotency_key=f"{deal_id}:ton-refund-test-pending",
                event_type="payment_pending", event_data={}, now=now,
            )
            transition_payment_intent(conn, intent["id"], "pending", now=now)
            transition_payment_intent(conn, intent["id"], "confirmed", provider_reference="ton-payment-tx", now=now)
            transition_deal_status(
                conn, deal_id=deal_id, new_status="paid", actor_user_id="202",
                idempotency_key=f"{deal_id}:ton-refund-test-paid",
                event_type="payment_confirmed", event_data={}, now=now,
            )
            post_ledger_transaction(
                conn, idempotency_key=f"{deal_id}:ton-refund-test-escrow",
                reference_type="deal", reference_id=deal_id, currency="TON",
                debit_account=("ton_treasury_clearing","ton-payment-tx"),
                credit_account=("escrow",deal_id), debit_amount="9", credit_amount="9", now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="disputed", actor_user_id="202",
                idempotency_key=f"{deal_id}:ton-refund-test-disputed",
                event_type="deal_disputed", event_data={"reason":"Refund requested"}, now=now,
            )
            for admin_id in ("202","303"):
                record_dispute_resolution_approval(
                    conn, deal_id=deal_id, admin_user_id=admin_id, decision="refund_buyer",
                    idempotency_key=f"{deal_id}:ton-refund-approval:{admin_id}", now=now,
                )
            conn.commit()

        class FakeTonResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "success": True,
                    "account": treasury,
                    "out_msgs": [{"destination":buyer_wallet,"value":"9000000000"}],
                }

        class FakeTonClient:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def get(self, url, headers):
                self.url = url
                return FakeTonResponse()

        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS":"202,303","TONAPI_TOKEN":"test-tonapi-token"}), patch(
            "marketplace_offers._require_ton_multisig_2_of_3", new=AsyncMock(return_value=None),
        ), patch(
            "httpx.AsyncClient", FakeTonClient
        ):
            refunded = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-refund",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"ab"*32,"idempotency_key":"ton-refund-execute-001"},
            )
            replay = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-refund",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"ab"*32,"idempotency_key":"ton-refund-execute-001"},
            )
        self.assertEqual(refunded.status_code, 200, refunded.text)
        self.assertEqual(refunded.json()["refund_status"], "refunded")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS":"202,303","TONAPI_TOKEN":"test-tonapi-token","BOT_TOKEN":"test-bot-token"}):
            wrong_hash_replay = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-refund",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"cd"*32,"idempotency_key":"ton-refund-wrong-hash-replay"},
            )
            cross_provider_replay = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-refund",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"idempotency_key":"stars-refund-must-not-replay-ton"},
            )
        self.assertEqual(wrong_hash_replay.status_code, 409, wrong_hash_replay.text)
        self.assertEqual(cross_provider_replay.status_code, 409, cross_provider_replay.text)
        with marketplace_offers._db() as conn:
            deal = conn.execute("SELECT status FROM marketplace_deals WHERE id=?",(deal_id,)).fetchone()
            intent = conn.execute("SELECT status FROM marketplace_payment_intents WHERE id=?",(intent["id"],)).fetchone()
            tx_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE reference_id=?",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(deal["status"], "cancelled")
        self.assertEqual(intent["status"], "refunded")
        self.assertEqual(tx_count, 2)

    def test_two_admin_approved_seller_release_requires_buyer_to_own_ton_nft(self):
        deal_id, seller_address, buyer_address = self._create_ton_deal_with_verified_wallets()
        now = int(time.time())
        with marketplace_offers._db() as conn:
            asset_address = conn.execute(
                """SELECT a.external_asset_id FROM marketplace_deals d
                   JOIN marketplace_orders o ON o.id=d.order_id
                   JOIN verified_assets a ON a.id=o.asset_id WHERE d.id=?""",
                (deal_id,),
            ).fetchone()["external_asset_id"]
        transfer_history = [{
            "operation": "transfer", "utime": now + 10, "lt": 123456789,
            "transaction_hash": "fa" * 32,
            "item": {"address": asset_address},
            "source": {"address": seller_address},
            "destination": {"address": buyer_address},
        }]
        treasury = "0:" + "aa" * 32
        with marketplace_offers._db() as conn:
            transition_deal_status(
                conn, deal_id=deal_id, new_status="awaiting_payment", actor_user_id="202",
                idempotency_key=f"{deal_id}:seller-release-test-awaiting",
                event_type="deal_payment_requested", event_data={}, now=now,
            )
            intent = create_payment_intent(
                conn, deal_id=deal_id, provider="ton", currency="TON", amount="9",
                idempotency_key=f"{deal_id}:seller-release-test-intent",
                invoice_payload=f"seller-release:{deal_id}", expires_at=now+900,
                metadata={"buyer_telegram_id":"202","treasury":treasury}, now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="payment_pending", actor_user_id="202",
                idempotency_key=f"{deal_id}:seller-release-test-pending",
                event_type="payment_pending", event_data={}, now=now,
            )
            transition_payment_intent(conn, intent["id"], "pending", now=now)
            transition_payment_intent(conn, intent["id"], "confirmed", provider_reference="seller-release-chain-tx", now=now)
            transition_deal_status(
                conn, deal_id=deal_id, new_status="paid", actor_user_id="202",
                idempotency_key=f"{deal_id}:seller-release-test-paid",
                event_type="payment_confirmed", event_data={}, now=now,
            )
            post_ledger_transaction(
                conn, idempotency_key=f"{deal_id}:seller-release-test-escrow",
                reference_type="deal", reference_id=deal_id, currency="TON",
                debit_account=("ton_treasury_clearing","seller-release-chain-tx"),
                credit_account=("escrow",deal_id), debit_amount="9", credit_amount="9", now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="delivery_pending", actor_user_id="101",
                idempotency_key=f"{deal_id}:seller-release-test-delivery-requested",
                event_type="asset_transfer_requested", event_data={"network":"ton-mainnet"}, now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="disputed", actor_user_id="202",
                idempotency_key=f"{deal_id}:seller-release-test-disputed",
                event_type="deal_disputed", event_data={"reason":"Buyer ownership is confirmed on TON"}, now=now,
            )
            for admin_id in ("202","303"):
                record_dispute_resolution_approval(
                    conn, deal_id=deal_id, admin_user_id=admin_id, decision="release_to_seller",
                    idempotency_key=f"{deal_id}:seller-release-approval:{admin_id}", now=now,
                )
            conn.commit()
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS":"202,303"}), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address":seller_address,"is_scam":False}),
        ), patch(
            "ton_nft_inventory.fetch_wallet_nft_history",
            new=AsyncMock(return_value=transfer_history),
        ):
            seller_still_owns = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-seller-release",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"idempotency_key":"seller-release-must-not-pay-without-delivery"},
            )
        self.assertEqual(seller_still_owns.status_code, 409, seller_still_owns.text)
        self.assertIn("buyer owns the NFT", seller_still_owns.text)
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS":"202,303"}), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address":buyer_address,"is_scam":False}),
        ), patch(
            "ton_nft_inventory.fetch_wallet_nft_history",
            new=AsyncMock(return_value=[]),
        ):
            missing_history = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-seller-release",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"idempotency_key":"seller-release-must-have-transfer-history"},
            )
        self.assertEqual(missing_history.status_code, 409, missing_history.text)
        self.assertIn("history", missing_history.text.lower())
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS":"202,303"}), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address":buyer_address,"is_scam":False}),
        ), patch(
            "ton_nft_inventory.fetch_wallet_nft_history",
            new=AsyncMock(return_value=transfer_history),
        ):
            released = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-seller-release",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"idempotency_key":"seller-release-execute-001"},
            )
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["status"], "completed")
        self.assertEqual(released.json()["seller_credit"], "recorded")
        self.assertEqual(released.json()["settlement_status"], "pending_manual_payout")
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS":"202,303"}):
            replay = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-seller-release",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"idempotency_key":"seller-release-execute-replay"},
            )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        with marketplace_offers._db() as conn:
            deal = conn.execute("SELECT status FROM marketplace_deals WHERE id=?",(deal_id,)).fetchone()
            asset = conn.execute(
                "SELECT owner_telegram_id FROM verified_assets WHERE id=(SELECT asset_id FROM marketplace_orders WHERE id=(SELECT order_id FROM marketplace_deals WHERE id=?))",
                (deal_id,),
            ).fetchone()
            ownership_count = conn.execute(
                "SELECT COUNT(*) FROM asset_ownership_history WHERE asset_id=(SELECT asset_id FROM marketplace_orders WHERE id=(SELECT order_id FROM marketplace_deals WHERE id=?)) AND new_owner_id='202'",
                (deal_id,),
            ).fetchone()[0]
            credit_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='seller_credit_recorded'",
                (deal_id,),
            ).fetchone()[0]
            ledger_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE idempotency_key=?",
                (f"{deal_id}:seller-release:seller-release-chain-tx",),
            ).fetchone()[0]
        self.assertEqual(deal["status"], "completed")
        self.assertEqual(asset["owner_telegram_id"], "202")
        self.assertEqual(ownership_count, 1)
        self.assertEqual(credit_count, 1)
        self.assertEqual(ledger_count, 1)

        class FakePayoutResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "success": True,
                    "account": treasury,
                    "out_msgs": [{"destination": seller_address, "value": "9000000000"}],
                }
        class FakePayoutClient:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def get(self, url, headers):
                return FakePayoutResponse()

        class FakeWrongDestinationResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "success": True,
                    "account": treasury,
                    "out_msgs": [{"destination": buyer_address, "value": "9000000000"}],
                }
        class FakeWrongDestinationClient(FakePayoutClient):
            async def get(self, url, headers):
                return FakeWrongDestinationResponse()
        class FakeBouncedResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "success": True,
                    "account": treasury,
                    "out_msgs": [{"destination": seller_address, "value": "9000000000", "bounced": True}],
                }
        class FakeBouncedClient(FakePayoutClient):
            async def get(self, url, headers):
                return FakeBouncedResponse()

        payout_flags = {
            "ADMIN_TELEGRAM_IDS":"202,303",
            "TONAPI_TOKEN":"test-tonapi-token",
            "HASSLE_TON_CUSTODY_ACKNOWLEDGED":"1",
            "HASSLE_TON_PAYOUT_MODE":"manual_multisig_2_of_3",
            "HASSLE_TON_TREASURY_ADDRESS":treasury,
        }
        with patch.dict(os.environ, payout_flags), patch(
            "marketplace_offers._require_ton_multisig_2_of_3", new=AsyncMock(return_value=None),
        ), patch("httpx.AsyncClient", FakeBouncedClient):
            bounced = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-seller-payout",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"bb"*32,"idempotency_key":"seller-payout-bounced"},
            )
        self.assertEqual(bounced.status_code, 409, bounced.text)
        with patch.dict(os.environ, payout_flags), patch(
            "marketplace_offers._require_ton_multisig_2_of_3", new=AsyncMock(return_value=None),
        ), patch("httpx.AsyncClient", FakeWrongDestinationClient):
            wrong_destination = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-seller-payout",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"ba"*32,"idempotency_key":"seller-payout-wrong-destination"},
            )
        self.assertEqual(wrong_destination.status_code, 409, wrong_destination.text)
        with patch.dict(os.environ, payout_flags), patch(
            "marketplace_offers._require_ton_multisig_2_of_3", new=AsyncMock(return_value=None),
        ), patch("httpx.AsyncClient", FakePayoutClient):
            payout = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-seller-payout",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"cd"*32,"idempotency_key":"seller-payout-001"},
            )
            payout_replay = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-seller-payout",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"cd"*32,"idempotency_key":"seller-payout-001"},
            )
            different_payout = self.client.post(
                f"/api/marketplace/admin/deals/{deal_id}/execute-ton-seller-payout",
                headers={"X-Telegram-Init-Data":"user-b"},
                json={"transaction_hash":"ef"*32,"idempotency_key":"seller-payout-002"},
            )
        self.assertEqual(payout.status_code, 200, payout.text)
        self.assertEqual(payout.json()["payout_status"], "paid")
        self.assertEqual(payout.json()["amount"], "9")
        self.assertEqual(payout_replay.status_code, 200, payout_replay.text)
        self.assertTrue(payout_replay.json()["idempotent_replay"])
        self.assertEqual(different_payout.status_code, 409, different_payout.text)
        with marketplace_offers._db() as conn:
            payout_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='payout_released'",
                (deal_id,),
            ).fetchone()[0]
            payout_ledger = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE idempotency_key=?",
                ("ton-seller-payout:" + "cd"*32,),
            ).fetchone()[0]
            seller_payable_debits = conn.execute(
                """SELECT COUNT(*) FROM marketplace_ledger_entries le
                   JOIN marketplace_ledger_transactions lt ON lt.id=le.transaction_id
                   WHERE lt.reference_id=? AND lt.reference_type='withdrawal'
                     AND le.account_type='seller_payable' AND le.direction='debit'""",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(payout_count, 1)
        self.assertEqual(payout_ledger, 1)
        self.assertEqual(seller_payable_debits, 1)

    def test_ton_payment_confirms_exact_incoming_transaction_once(self):
        deal_id, seller_wallet, buyer_wallet = self._create_ton_deal_with_verified_wallets()
        treasury = "0:" + "aa" * 32
        flags = {
            "HASSLE_TON_CHECKOUT_ENABLED": "1",
            "HASSLE_TON_CUSTODY_ACKNOWLEDGED": "1",
            "HASSLE_TON_PAYOUT_MODE": "manual_multisig_2_of_3",
            "HASSLE_TON_TREASURY_ADDRESS": treasury,
            "TONAPI_TOKEN": "test-tonapi-token",
            "PAYMENT_SUPPORT_CONTACT": "@hassle_support",
        }
        with patch.dict(os.environ, flags), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address":seller_wallet,"is_scam":False}),
        ):
            checkout = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-checkout",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "ton-confirm-invoice"},
            )
        self.assertEqual(checkout.status_code, 200, checkout.text)
        memo = checkout.json()["memo"]

        class FakeTonResponse:
            def raise_for_status(self):
                return None
            def json(self):
                return {"transactions": [{
                    "hash": "ton-chain-transaction-001",
                    "success": True,
                    "utime": int(time.time()),
                    "in_msg": {
                        "source": buyer_wallet,
                        "destination": treasury,
                        "value": "9000000000",
                        "decoded_body": {"text": memo},
                    },
                }]}

        class FakeTonClient:
            def __init__(self, **kwargs):
                self.calls = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def get(self, url, headers):
                self.calls.append((url, headers))
                return FakeTonResponse()

        fake = FakeTonClient()
        with patch.dict(os.environ, flags), patch("httpx.AsyncClient", return_value=fake):
            first = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-payment/confirm",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "ton-confirm-001"},
            )
            second = self.client.post(
                f"/api/marketplace/deals/{deal_id}/ton-payment/confirm",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"idempotency_key": "ton-confirm-002"},
            )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["status"], "paid")
        self.assertTrue(second.json()["idempotent_replay"])
        with marketplace_offers._db() as conn:
            transactions = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE reference_id=?",
                (deal_id,),
            ).fetchone()[0]
            entries = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_entries WHERE transaction_id IN "
                "(SELECT id FROM marketplace_ledger_transactions WHERE reference_id=?)",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(transactions, 1)
        self.assertEqual(entries, 2)

    def test_stars_precheckout_binds_user_amount_and_deal_and_is_one_time(self):
        deal_id, invoice_payload, intent_id = self._create_stars_deal_with_intent()
        ok, reason = validate_stars_pre_checkout(
            invoice_payload=invoice_payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000010,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        with marketplace_offers._db() as conn:
            intent = conn.execute(
                "SELECT status FROM marketplace_payment_intents WHERE id=?", (intent_id,)
            ).fetchone()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
        self.assertEqual(intent["status"], "pending")
        self.assertEqual(deal["status"], "payment_pending")
        rejected, _ = validate_stars_pre_checkout(
            invoice_payload=invoice_payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000011,
        )
        self.assertFalse(rejected)

    def test_stars_successful_payment_confirms_once_and_posts_balanced_escrow_ledger(self):
        deal_id, invoice_payload, intent_id = self._create_stars_deal_with_intent()
        ok, _ = validate_stars_pre_checkout(
            invoice_payload=invoice_payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000010,
        )
        self.assertTrue(ok)
        kwargs = {
            "invoice_payload": invoice_payload,
            "telegram_user_id": "202",
            "currency": "XTR",
            "total_amount": 9,
            "telegram_payment_charge_id": "tg-charge-001",
            "provider_payment_charge_id": "",
            "now": 1700000020,
        }
        confirmed = confirm_stars_payment(**kwargs)
        self.assertEqual(confirmed["deal_id"], deal_id)
        self.assertEqual(confirmed["status"], "paid")
        replay = confirm_stars_payment(**kwargs)
        self.assertEqual(replay["status"], "paid")
        with marketplace_offers._db() as conn:
            intent = conn.execute(
                "SELECT status, provider_reference FROM marketplace_payment_intents WHERE id=?",
                (intent_id,),
            ).fetchone()
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)
            ).fetchone()
            tx_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE reference_id=?",
                (deal_id,),
            ).fetchone()[0]
            entries = conn.execute(
                "SELECT direction, amount FROM marketplace_ledger_entries WHERE transaction_id IN "
                "(SELECT id FROM marketplace_ledger_transactions WHERE reference_id=?)",
                (deal_id,),
            ).fetchall()
            funded = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='escrow_funded'",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(intent["status"], "confirmed")
        self.assertEqual(intent["provider_reference"], "tg-charge-001")
        self.assertEqual(deal["status"], "paid")
        self.assertEqual(tx_count, 1)
        self.assertEqual(len(entries), 2)
        self.assertEqual(funded, 1)

    def test_stars_successful_payment_cannot_revive_deal_outside_payment_pending(self):
        deal_id, invoice_payload, _intent_id = self._create_stars_deal_with_intent()
        ok, _ = validate_stars_pre_checkout(
            invoice_payload=invoice_payload, telegram_user_id="202",
            currency="XTR", total_amount=9, now=1700000010,
        )
        self.assertTrue(ok)
        with marketplace_offers._db() as conn:
            conn.execute(
                "UPDATE marketplace_deals SET status='cancelled' WHERE id=?",
                (deal_id,),
            )
            conn.commit()
        with self.assertRaises(PaymentStateError):
            confirm_stars_payment(
                invoice_payload=invoice_payload,
                telegram_user_id="202",
                currency="XTR",
                total_amount=9,
                telegram_payment_charge_id="tg-charge-after-cancel",
                provider_payment_charge_id="",
                now=1700000020,
            )
        with marketplace_offers._db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_transactions WHERE reference_id=?",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_webhook_secret_is_stable_when_derived_from_bot_token(self):
        with patch.dict(os.environ, {}, clear=True):
            first = api._resolve_webhook_secret("test-bot-token")
            second = api._resolve_webhook_secret("test-bot-token")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_webhook_secret_uses_valid_explicit_configuration(self):
        with patch.dict(os.environ, {"BOT_WEBHOOK_SECRET": "Stable_secret-123"}, clear=True):
            self.assertEqual(api._resolve_webhook_secret("test-bot-token"), "Stable_secret-123")

    def test_webhook_secret_rejects_invalid_explicit_configuration(self):
        with patch.dict(os.environ, {"BOT_WEBHOOK_SECRET": "bad secret"}, clear=True):
            with self.assertRaises(RuntimeError):
                api._resolve_webhook_secret("test-bot-token")

    def test_readiness_probe_confirms_database_is_queryable(self):
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["dependencies"]["database"], "ok")
        self.assertEqual(response.json()["dependencies"]["database_schema"], "ok")

    def test_readiness_probe_returns_503_when_database_is_unavailable(self):
        with patch("marketplace._db", side_effect=RuntimeError("database unavailable")):
            response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["dependency"], "database")

    def test_readiness_probe_returns_503_when_critical_schema_is_missing(self):
        with marketplace._db() as connection:
            connection.execute("DROP TABLE verified_wallets")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["dependency"], "database_schema")
        self.assertIn("verified_wallets", response.json()["missing_tables"])

    def test_readiness_probe_checks_marketplace_offers_and_notifications_schema(self):
        with marketplace._db() as connection:
            connection.execute("DROP TABLE marketplace_notifications")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["dependency"], "database_schema")
        self.assertIn("marketplace_notifications", response.json()["missing_tables"])

    def test_readiness_probe_returns_503_when_required_column_is_missing(self):
        with marketplace._db() as connection:
            connection.execute("ALTER TABLE verified_wallets RENAME COLUMN address TO wallet_address")
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["dependency"], "database_schema")
        self.assertIn("verified_wallets.address", response.json()["missing_columns"])

    def test_marketplace_startup_never_deletes_existing_user_listings(self):
        now = int(time.time())
        rows = [
            ("startup-preserve-pepe", "101", "n0kl1pzz1k", "listing", "Plush Pepe #1", "70000", "TON", "active", now, now, ""),
            ("startup-preserve-legacy", "101", "n0kl1pzz1k", "listing", "ndmd", "999999", "TON", "active", now, now, ""),
        ]
        with marketplace._db() as connection:
            connection.executemany(
                """INSERT INTO marketplace_orders
                   (id, user_id, owner_name, kind, title, price, currency, status,
                    created_at, updated_at, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

        marketplace.init_marketplace_db()

        with marketplace._db() as connection:
            preserved = connection.execute(
                "SELECT id FROM marketplace_orders WHERE id IN (?, ?) ORDER BY id",
                ("startup-preserve-pepe", "startup-preserve-legacy"),
            ).fetchall()
        self.assertEqual([row["id"] for row in preserved], [
            "startup-preserve-legacy",
            "startup-preserve-pepe",
        ])

    def test_database_initializers_are_idempotent_and_foreign_keys_are_valid(self):
        marketplace.init_marketplace_db()
        marketplace_offers.init_marketplace_offers_db()
        api.init_ton_connect_db()

        with marketplace._db() as connection:
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(violations, [])

        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_database_connections_enforce_durability_and_lock_wait_policy(self):
        with marketplace._db() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        with marketplace_offers._db() as conn:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_initdata_replay_protection_is_independent_of_query_parameter_order(self):
        signature = "a" * 64
        first = "auth_date=123&user=%7B%7D&hash=" + signature
        reordered = "hash=" + signature + "&auth_date=123&user=%7B%7D"
        self.assertTrue(marketplace.consume_telegram_init_data(first, "101"))
        self.assertFalse(marketplace.consume_telegram_init_data(reordered, "101"))

    def test_security_audit_records_hashed_targets_and_only_allowlisted_metadata(self):
        wallet_address = "0:" + "a" * 64
        event_id = marketplace.record_security_audit_event(
            actor_telegram_id="101",
            action="ton_wallet_linked",
            target_type="wallet",
            target_id=wallet_address,
            metadata={
                "network": "-239",
                "reason": "self_logout",
                "signature": "must-not-be-stored",
                "session_token": "must-not-be-stored",
            },
            now=1234,
        )
        with marketplace._db() as conn:
            row = conn.execute(
                "SELECT actor_telegram_id, action, target_type, target_hash, metadata_json, created_at "
                "FROM security_audit_events WHERE id=?",
                (event_id,),
            ).fetchone()
        self.assertEqual(row["actor_telegram_id"], "101")
        self.assertEqual(row["action"], "ton_wallet_linked")
        self.assertEqual(row["target_type"], "wallet")
        self.assertEqual(len(row["target_hash"]), 64)
        self.assertNotEqual(row["target_hash"], wallet_address)
        self.assertEqual(json.loads(row["metadata_json"]), {"network": "-239", "reason": "self_logout"})
        self.assertEqual(row["created_at"], 1234)
        with marketplace._db() as conn:
            with self.assertRaises(sqlite3.IntegrityError, msg="security audit events are immutable"):
                conn.execute("UPDATE security_audit_events SET outcome='denied' WHERE id=?", (event_id,))
            with self.assertRaises(sqlite3.IntegrityError, msg="security audit events are immutable"):
                conn.execute("DELETE FROM security_audit_events WHERE id=?", (event_id,))

    def test_telegram_auth_persists_and_updates_user_profile(self):
        first = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["user"]["id"], "101")
        token = first.json()["session_token"]
        self.assertTrue(token.startswith("grs_"))
        self.assertEqual(first.json()["expires_in"], 43200)
        current = self.client.get("/api/auth/me", headers={"X-Telegram-Init-Data": token})
        self.assertEqual(current.status_code, 200)
        self.assertTrue(current.json()["authenticated"])
        self.assertEqual(current.json()["user"]["id"], "101")
        self.assertFalse(current.json()["is_admin"])
        replay = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(replay.status_code, 409)
        session_user = api._authenticate_telegram_credential(token)
        self.assertEqual(session_user["id"], "101")
        self.assertIsNone(marketplace.get_telegram_session_user("grs_invalid-token"))
        with marketplace._db() as conn:
            row = conn.execute("SELECT telegram_id, first_name, username FROM telegram_users WHERE telegram_id = ?", ("101",)).fetchone()
        self.assertEqual(row["first_name"], "Tester A")
        self.assertEqual(row["username"], "tester")
        marketplace.upsert_telegram_user({"id":"101", "first_name":"Updated", "last_name":"Name", "username":"updated_handle", "photo_url":"https://t.me/i/userpic/1.jpg", "language_code":"ru", "is_premium":True})
        with marketplace._db() as conn:
            row = conn.execute("SELECT first_name, last_name, username, is_premium FROM telegram_users WHERE telegram_id = ?", ("101",)).fetchone()
        self.assertEqual(row["first_name"], "Updated")
        self.assertEqual(row["last_name"], "Name")
        self.assertEqual(row["username"], "updated_handle")
        self.assertEqual(row["is_premium"], 1)

    def test_telegram_auth_exchange_is_rate_limited_per_verified_account(self):
        with patch.object(api, "_enforce_user_rate_limit") as limiter:
            response = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(response.status_code, 200, response.text)
        limiter.assert_called_once_with("101", "telegram_auth_exchange", 30, 60)

    def test_logout_writes_audit_event_without_storing_session_token(self):
        authenticated = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        self.assertEqual(authenticated.status_code, 200)
        session_token = authenticated.json()["session_token"]
        response = self.client.post(
            "/api/auth/logout",
            headers={"X-Telegram-Init-Data": session_token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["revoked"])
        with marketplace._db() as conn:
            event = conn.execute(
                "SELECT actor_telegram_id, action, target_type, target_hash FROM security_audit_events "
                "WHERE action='telegram_session_revoked' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(event["actor_telegram_id"], "101")
        self.assertEqual(event["target_type"], "session")
        self.assertEqual(len(event["target_hash"]), 64)
        self.assertNotIn(session_token, event["target_hash"])

    def test_session_revocation_rolls_back_if_audit_write_fails(self):
        authenticated = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        token = authenticated.json()["session_token"]
        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_security_audit_insert
                   BEFORE INSERT ON security_audit_events
                   BEGIN SELECT RAISE(ABORT, 'audit store unavailable'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            marketplace.revoke_telegram_session(token)
        self.assertIsNotNone(marketplace.get_telegram_session_user(token))

    def test_seller_profile_get_is_idempotent_and_rate_limited(self):
        authenticated = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        headers = {"X-Telegram-Init-Data": authenticated.json()["session_token"]}
        with patch.object(marketplace, "_enforce_user_rate_limit") as limiter:
            first = self.client.get("/api/marketplace/profiles/me", headers=headers)
            second = self.client.get("/api/marketplace/profiles/me", headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["profile"]["profile_id"], second.json()["profile"]["profile_id"])
        self.assertEqual(limiter.call_count, 2)
        limiter.assert_any_call("101", "get_seller_profile", 30)

    def test_private_notification_and_account_reads_are_rate_limited(self):
        authenticated = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
        headers = {"X-Telegram-Init-Data": authenticated.json()["session_token"]}
        with patch.object(marketplace_offers, "_enforce_user_rate_limit") as limiter:
            responses = [
                self.client.get("/api/marketplace/offers", headers=headers),
                self.client.get("/api/marketplace/notifications", headers=headers),
                self.client.patch("/api/marketplace/notifications/missing/read", headers=headers),
                self.client.post("/api/marketplace/notifications/read-all", headers=headers),
                self.client.get("/api/marketplace/account/stats", headers=headers),
                self.client.get("/api/marketplace/activity", headers=headers),
            ]
        self.assertEqual([response.status_code for response in responses], [200, 200, 404, 200, 200, 200])
        expected = {
            ("101", "list_offers", 60),
            ("101", "list_notifications", 60),
            ("101", "mark_notification_read", 60),
            ("101", "mark_all_notifications_read", 10),
            ("101", "account_stats", 30),
            ("101", "account_activity", 60),
        }
        actual = {(args[0], args[1], args[2]) for args, _kwargs in limiter.call_args_list}
        self.assertEqual(actual, expected)

    def test_order_and_asset_ownership_history_are_append_only(self):
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO marketplace_orders
                   (id,user_id,owner_name,kind,title,price,currency,status,created_at,updated_at)
                   VALUES ('audit-order','101','Owner','listing','Gift','1','TON','active',1,1)"""
            )
            conn.execute(
                """INSERT INTO verified_assets
                   (id,source,external_asset_id,owner_telegram_id,title,asset_type,
                    verification_method,verification_reference_hash,created_at,verified_at)
                   VALUES ('audit-asset','telegram_gift','gift-1','101','Gift','gift',
                           'telegram_gift_owner','hash-1',1,1)"""
            )
            conn.execute(
                "INSERT INTO marketplace_order_events(order_id,actor_user_id,event_type,created_at) "
                "VALUES ('audit-order','101','created',1)"
            )
            conn.execute(
                "INSERT INTO marketplace_order_revisions(order_id,actor_user_id,changed_fields,created_at) "
                "VALUES ('audit-order','101','[]',1)"
            )
            conn.execute(
                "INSERT INTO asset_ownership_history(asset_id,previous_owner_id,new_owner_id,verification_reference_hash,created_at) "
                "VALUES ('audit-asset','100','101','hash-1',1)"
            )
            for table, where in (
                ("marketplace_order_events", "order_id='audit-order'"),
                ("marketplace_order_revisions", "order_id='audit-order'"),
                ("asset_ownership_history", "asset_id='audit-asset'"),
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only history is immutable"):
                    conn.execute(f"UPDATE {table} SET created_at=2 WHERE {where}")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only history is immutable"):
                    conn.execute(f"DELETE FROM {table} WHERE {where}")

    def test_telegram_auth_reports_server_verified_admin_role(self):
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101"}):
            admin = self.client.post("/api/auth/telegram", json={"init_data": "user-a"})
            regular = self.client.post("/api/auth/telegram", json={"init_data": "user-b"})
        self.assertEqual(admin.status_code, 200)
        self.assertTrue(admin.json()["is_admin"])
        self.assertEqual(regular.status_code, 200)
        self.assertFalse(regular.json()["is_admin"])


    def test_test_only_asset_overrides_are_disabled_outside_isolated_tests(self):
        with patch.dict(os.environ, {
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true",
            "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true",
        }, clear=True):
            self.assertFalse(marketplace._test_unverified_assets_allowed())
            self.assertFalse(marketplace._test_ton_owner_recheck_skipped())

        with patch.dict(os.environ, {
            "APP_ENV": "test",
            "RAILWAY_ENVIRONMENT_NAME": "production",
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true",
            "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true",
        }, clear=True):
            self.assertFalse(marketplace._test_unverified_assets_allowed())
            self.assertFalse(marketplace._test_ton_owner_recheck_skipped())

        with patch.dict(os.environ, {
            "APP_ENV": "test",
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true",
            "HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true",
        }, clear=True):
            self.assertTrue(marketplace._test_unverified_assets_allowed())
            self.assertTrue(marketplace._test_ton_owner_recheck_skipped())

    def test_production_listing_requires_verified_asset_even_if_feature_flag_is_enabled(self):
        with patch.dict(os.environ, {
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
            "HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false",
        }):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "title": "Invented gift", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("verified asset", response.json()["detail"].lower())

    def test_verified_asset_registration_is_idempotent_and_listing_uses_server_metadata(self):
        attestation = {
            "source": "ton_nft", "external_asset_id": "EQB_verified_asset_001",
            "owner_telegram_id": "101", "title": "Verified Telegram Gift",
            "asset_type": "gift", "image_url": "https://cdn.example/gift.webp",
            "verification_method": "ton_connect_proof",
            "verification_reference": "one-time-server-verification-reference",
        }
        asset = marketplace.register_verified_asset(**attestation)
        repeated = marketplace.register_verified_asset(**attestation)
        self.assertEqual(asset["id"], repeated["id"])
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Seller spoofed title", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201)
        item = response.json()["item"]
        self.assertEqual(item["title"], "Verified Telegram Gift")
        self.assertEqual(item["asset_type"], "gift")
        self.assertEqual(item["asset_id"], asset["id"])
        self.assertEqual(item["image_url"], "https://cdn.example/gift.webp")

    def test_ton_nft_listing_rechecks_current_owner_against_linked_wallet(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="Live owner NFT", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_chain.verify_nft_owner_onchain",
            new=AsyncMock(return_value={"verified": True, "owner_address": wallet_address}),
        ), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address": "0:" + "c" * 64, "is_scam": False, "trust": "whitelist"}),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["item"]["title"], "Live owner NFT")

    def test_fragment_username_listing_uses_contract_owner_and_collection_recheck(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="@verifiedname", asset_type="username",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="ton-contract-v1:username:12345:verified",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_chain.verify_nft_collection_membership",
            new=AsyncMock(return_value={"verified": True, "block_seqno": 12346}),
        ) as verify, patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address": wallet_address, "is_scam": False, "trust": "whitelist"}),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        verify.assert_awaited_once_with(
            nft_address,
            "0:80d78a35f955a14b679faa887ff4cd5bfc0f43b4a4eea2a7e6927f3701b273c2",
            wallet_address,
        )
        self.assertEqual(response.json()["item"]["asset_type"], "username")
        self.assertEqual(response.json()["item"]["title"], "@verifiedname")

    def test_fragment_listing_is_revoked_when_contract_owner_no_longer_matches(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="+888 1234 5678", asset_type="number",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="ton-contract-v1:number:12345:verified",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_chain.verify_nft_collection_membership",
            new=AsyncMock(return_value={"verified": False, "block_seqno": 12346}),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 409)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id = ?", (asset["id"],)).fetchone()
        self.assertEqual(row["status"], "revoked")

    def test_fragment_listing_provider_failure_keeps_asset_verified(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="@temporarilyoffline", asset_type="username",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="ton-contract-v1:username:12345:verified",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_chain.verify_nft_collection_membership",
            new=AsyncMock(side_effect=RuntimeError("TON Center unavailable")),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 503)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id = ?", (asset["id"],)).fetchone()
        self.assertEqual(row["status"], "verified")

    def test_ton_nft_listing_is_rejected_and_asset_revoked_when_owner_changed(self):
        wallet_address = "0:" + "a" * 64
        nft_address = "0:" + "b" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="Transferred NFT", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "false"}), patch(
            "ton_connect.list_verified_wallets",
            return_value=[{"network": "-239", "address": wallet_address}],
        ), patch(
            "ton_nft_chain.verify_nft_owner_onchain",
            new=AsyncMock(return_value={"verified": False, "owner_address": "0:" + "c" * 64}),
        ), patch(
            "ton_nft_inventory.fetch_nft_owner",
            new=AsyncMock(return_value={"owner_address": wallet_address, "is_scam": False, "trust": "whitelist"}),
        ):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 409)
        with marketplace._db() as conn:
            row = conn.execute("SELECT status FROM verified_assets WHERE id = ?", (asset["id"],)).fetchone()
        self.assertEqual(row["status"], "revoked")

    def test_verified_asset_metadata_is_immutable_and_cannot_be_claimed_by_another_owner(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_immutable_asset",
            owner_telegram_id="101", title="Immutable gift", asset_type="gift",
            image_url="https://cdn.example/immutable.webp",
            verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with marketplace._db() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE verified_assets SET title = ? WHERE id = ?", ("Spoofed title", asset["id"]))
            # A direct owner reassignment must be rejected too. Only the
            # trusted registration path can create the one-use authorization.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE verified_assets SET owner_telegram_id = ?, title = ?, verification_reference_hash = ? WHERE id = ?",
                    ("202", "Claimed by B", "forged-reference-hash", asset["id"]),
                )
            pending_authorizations = conn.execute(
                "SELECT COUNT(*) FROM asset_owner_transfer_authorizations WHERE asset_id = ?",
                (asset["id"],),
            ).fetchone()[0]
            self.assertEqual(pending_authorizations, 0)
        with self.assertRaises(ValueError):
            marketplace.register_verified_asset(
                source="ton_nft", external_asset_id="EQB_immutable_asset",
                owner_telegram_id="202", title="Claimed by B", asset_type="gift",
                image_url="", verification_method="ton_connect_proof",
                verification_reference="different-one-time-verification-reference",
            )

    def test_revoked_ton_nft_can_move_to_new_verified_owner_without_resurrecting_old_listing(self):
        nft_address = "0:" + "b" * 64
        old_asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="101", title="NFT before transfer", asset_type="gift",
            image_url="https://cdn.example/before.webp",
            verification_method="ton_connect_proof",
            verification_reference="old-owner-one-time-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true"}):
            listing = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": old_asset["id"], "title": "Ignored", "price": "3", "currency": "TON"},
            )
        self.assertEqual(listing.status_code, 201)
        offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": listing.json()["item"]["id"], "amount": "2", "note": "Interested"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        new_asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=nft_address,
            owner_telegram_id="202", title="NFT after transfer", asset_type="gift",
            image_url="https://cdn.example/after.webp",
            verification_method="ton_connect_proof",
            verification_reference="new-owner-one-time-verification-reference",
            allow_owner_transfer=True,
        )
        self.assertEqual(new_asset["id"], old_asset["id"])
        self.assertEqual(new_asset["owner_telegram_id"], "202")
        self.assertEqual(new_asset["title"], "NFT after transfer")
        with marketplace._db() as conn:
            old_listing = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (listing.json()["item"]["id"],)
            ).fetchone()
            history = conn.execute(
                "SELECT previous_owner_id, new_owner_id FROM asset_ownership_history WHERE asset_id = ?",
                (old_asset["id"],),
            ).fetchone()
            pending_authorizations = conn.execute(
                "SELECT COUNT(*) FROM asset_owner_transfer_authorizations WHERE asset_id = ?",
                (old_asset["id"],),
            ).fetchone()[0]
            old_offer = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer.json()["item"]["id"],)
            ).fetchone()
        self.assertEqual(old_listing["status"], "cancelled")
        self.assertEqual(old_offer["status"], "declined")
        self.assertEqual(history["previous_owner_id"], "101")
        self.assertEqual(history["new_owner_id"], "202")
        self.assertEqual(pending_authorizations, 0)
        with marketplace._db() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE verified_assets SET owner_telegram_id = ?, verification_reference_hash = ? WHERE id = ?",
                    ("101", "old-owner-one-time-verification-reference", old_asset["id"]),
                )

    def test_asset_owner_transfer_rolls_back_all_marketplace_changes_on_history_failure(self):
        external_id = "0:" + "d" * 64
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id=external_id,
            owner_telegram_id="101", title="Original owner gift", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="original-owner-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_SKIP_TON_OWNER_RECHECK": "true"}):
            listing = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignored", "price": "3", "currency": "TON"},
            )
        self.assertEqual(listing.status_code, 201, listing.text)
        order_id = listing.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "2"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        # Inject a failure after the owner update. All related marketplace
        # changes must roll back with the ownership-history insert.
        with marketplace._db() as conn:
            conn.execute("""
                CREATE TRIGGER test_fail_ownership_history
                BEFORE INSERT ON asset_ownership_history
                BEGIN
                    SELECT RAISE(ABORT, 'injected ownership-history failure');
                END
            """)

        with self.assertRaises(sqlite3.IntegrityError):
            marketplace.register_verified_asset(
                source="ton_nft", external_asset_id=external_id,
                owner_telegram_id="202", title="New owner gift", asset_type="gift",
                image_url="", verification_method="ton_connect_proof",
                verification_reference="new-owner-verification-reference",
                allow_owner_transfer=True,
            )

        with marketplace._db() as conn:
            stored_asset = conn.execute(
                "SELECT owner_telegram_id, title, status FROM verified_assets WHERE id = ?",
                (asset["id"],),
            ).fetchone()
            stored_listing = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()
            stored_offer = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()
            history_count = conn.execute(
                "SELECT COUNT(*) FROM asset_ownership_history WHERE asset_id = ?",
                (asset["id"],),
            ).fetchone()[0]
            pending_authorizations = conn.execute(
                "SELECT COUNT(*) FROM asset_owner_transfer_authorizations WHERE asset_id = ?",
                (asset["id"],),
            ).fetchone()[0]

        self.assertEqual(stored_asset["owner_telegram_id"], "101")
        self.assertEqual(stored_asset["title"], "Original owner gift")
        self.assertEqual(stored_asset["status"], "verified")
        self.assertEqual(stored_listing["status"], "active")
        self.assertEqual(stored_offer["status"], "pending")
        self.assertEqual(history_count, 0)
        self.assertEqual(pending_authorizations, 0)


    def test_public_catalog_hides_legacy_unverified_listings_but_keeps_verified_assets(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_catalog_verified",
            owner_telegram_id="101", title="Verified catalogue gift", asset_type="gift",
            image_url="https://cdn.example/catalog.webp",
            verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            verified = self.client.post(
                "/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignore this title", "price": "2", "currency": "TON"},
            )
        self.assertEqual(verified.status_code, 201)
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "true"}):
            legacy = self.client.post(
                "/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-b"},
                json={"kind": "listing", "title": "Legacy unverified listing", "price": "1", "currency": "TON"},
            )
        self.assertEqual(legacy.status_code, 201)
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            catalog = self.client.get("/api/marketplace/orders?status=all")
        titles = [item["title"] for item in catalog.json()["items"]]
        self.assertIn("Verified catalogue gift", titles)
        self.assertNotIn("Legacy unverified listing", titles)

    def test_buy_request_does_not_require_owned_asset(self):
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            response = self.client.post(
                "/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "buy-order", "title": "Looking for a collectible gift", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["item"]["asset_id"])

    def test_verified_inventory_is_private_to_its_telegram_owner(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_private_inventory",
            owner_telegram_id="101", title="Private inventory gift", asset_type="gift",
            image_url="https://cdn.example/private.webp",
            verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        owner = self.client.get("/api/marketplace/assets/me", headers={"X-Telegram-Init-Data": "user-a"})
        other = self.client.get("/api/marketplace/assets/me", headers={"X-Telegram-Init-Data": "user-b"})
        anonymous = self.client.get("/api/marketplace/assets/me")
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["items"][0]["id"], asset["id"])
        self.assertEqual(other.json()["items"], [])
        self.assertEqual(anonymous.status_code, 401)

    def test_verified_asset_cannot_have_two_active_listings(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_single_listing",
            owner_telegram_id="101", title="One listing only", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        headers = {"X-Telegram-Init-Data": "user-a"}
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            first = self.client.post(
                "/api/marketplace/orders", headers=headers,
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignored", "price": "2", "currency": "TON"},
            )
            second = self.client.post(
                "/api/marketplace/orders", headers=headers,
                json={"kind": "listing", "asset_id": asset["id"], "title": "Ignored", "price": "3", "currency": "TON"},
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)

    def test_verified_asset_cannot_be_listed_by_another_telegram_user(self):
        asset = marketplace.register_verified_asset(
            source="ton_nft", external_asset_id="EQB_owned_by_a",
            owner_telegram_id="101", title="Owner A gift", asset_type="gift",
            image_url="", verification_method="ton_connect_proof",
            verification_reference="one-time-server-verification-reference",
        )
        with patch.dict(os.environ, {"HASSLE_TEST_ALLOW_UNVERIFIED_ASSETS": "false"}):
            response = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"kind": "listing", "asset_id": asset["id"], "title": "Spoof", "price": "2", "currency": "TON"},
            )
        self.assertEqual(response.status_code, 403)

    def test_listing_and_offer_mutations_are_closed_without_verified_asset_pipeline(self):
        with patch.dict(os.environ, {"HASSLE_VERIFIED_ASSETS_ENABLED": "false"}):
            listing = self.client.post(
                "/api/marketplace/orders",
                headers={"X-Telegram-Init-Data": "user-a"},
                json={"kind": "listing", "title": "Invented Gift", "price": "2", "currency": "TON"},
            )
            offer = self.client.post(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": "user-b"},
                json={"order_id": "gr-does-not-exist", "amount": "1"},
            )
        self.assertEqual(listing.status_code, 409)
        self.assertIn("ownership verification", listing.json()["detail"].lower())
        self.assertEqual(offer.status_code, 409)
        self.assertIn("verified inventory", offer.json()["detail"].lower())

    def test_unverified_marketplace_hides_legacy_orders_offers_and_activity(self):
        owner = {"X-Telegram-Init-Data": "user-a"}
        sender = {"X-Telegram-Init-Data": "user-b"}
        with patch.dict(os.environ, {"HASSLE_VERIFIED_ASSETS_ENABLED": "true"}):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner,
                json={"kind": "listing", "title": "Legacy Test Gift", "price": "2", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            offer = self.client.post(
                "/api/marketplace/offers", headers=sender,
                json={"order_id": order.json()["item"]["id"], "amount": "1"},
            )
            self.assertEqual(offer.status_code, 201)
        with patch.dict(os.environ, {"HASSLE_VERIFIED_ASSETS_ENABLED": "false"}):
            self.assertEqual(self.client.get("/api/marketplace/orders").json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/mine", headers=owner).json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/offers", headers=sender).json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/activity", headers=owner).json()["items"], [])
            self.assertEqual(self.client.get("/api/marketplace/notifications", headers=owner).json()["items"], [])

    def test_marketplace_favorites_are_persisted_and_account_scoped(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        other_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Favorite persistence asset", "price": "5", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]
        saved = self.client.put(
            f"/api/marketplace/favorites/{order_id}",
            headers=owner_headers,
            json={"gift": {"id": order_id, "title": "Favorite persistence asset", "currency": "TON"}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        owner_favorites = self.client.get("/api/marketplace/favorites", headers=owner_headers)
        self.assertEqual(owner_favorites.status_code, 200)
        self.assertEqual(owner_favorites.json()["items"][0]["id"], order_id)
        self.assertEqual(self.client.get("/api/marketplace/favorites", headers=other_headers).json()["items"], [])
        mismatch = self.client.put(
            f"/api/marketplace/favorites/{order_id}",
            headers=owner_headers,
            json={"gift": {"id": "different-id", "title": "Forged favorite"}},
        )
        self.assertEqual(mismatch.status_code, 422)
        removed = self.client.delete(f"/api/marketplace/favorites/{order_id}", headers=owner_headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/marketplace/favorites", headers=owner_headers).json()["items"], [])

    def test_seller_counteroffer_to_buy_request_is_disabled_until_asset_binding_exists(self):
        seller_headers = {"X-Telegram-Init-Data": "user-b"}
        buyer_headers = {"X-Telegram-Init-Data": "user-a"}
        request = self.client.post(
            "/api/marketplace/orders", headers=buyer_headers,
            json={"kind": "buy-order", "title": "Collectible gift", "price": "80", "currency": "TON"},
        )
        self.assertEqual(request.status_code, 201, request.text)
        response = self.client.post(
            "/api/marketplace/offers", headers=seller_headers,
            json={"order_id": request.json()["item"]["id"], "amount": "78", "note": "Collectible gift #7, verified asset"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("verified asset", response.json()["detail"])

    def test_seller_cannot_respond_to_buy_request_without_active_listing(self):
        buyer_headers = {"X-Telegram-Init-Data": "user-a"}
        seller_headers = {"X-Telegram-Init-Data": "user-b"}
        with patch.object(marketplace, "_verified_asset_actions_enabled", return_value=False):
            request = self.client.post(
                "/api/marketplace/orders", headers=buyer_headers,
                json={"kind": "buy-order", "title": "Collectible gift", "price": "80", "currency": "TON"},
            )
        self.assertEqual(request.status_code, 201, request.text)
        response = self.client.post(
            "/api/marketplace/offers", headers=seller_headers,
            json={"order_id": request.json()["item"]["id"], "amount": "75", "note": "Offering my collectible gift"},
        )
        self.assertEqual(response.status_code, 409)

    def test_seller_response_to_buy_request_requires_asset_description(self):
        buyer_headers = {"X-Telegram-Init-Data": "user-a"}
        seller_headers = {"X-Telegram-Init-Data": "user-b"}
        request = self.client.post(
            "/api/marketplace/orders", headers=buyer_headers,
            json={"kind": "buy-order", "title": "Collectible gift", "price": "80", "currency": "TON"},
        )
        self.assertEqual(request.status_code, 201, request.text)
        response = self.client.post(
            "/api/marketplace/offers", headers=seller_headers,
            json={"order_id": request.json()["item"]["id"], "amount": "75", "note": ""},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("verified asset", response.json()["detail"])

    def test_buy_requests_can_be_published_and_discovered_without_seller_asset_listing_gate(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        with patch.object(marketplace, "_verified_asset_actions_enabled", return_value=False):
            response = self.client.post(
                "/api/marketplace/orders",
                headers=owner_headers,
                json={"kind": "buy-order", "title": "Plush Pepe", "price": "50", "currency": "XTR"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            item = response.json()["item"]
            self.assertEqual(item["kind"], "buy-order")
            self.assertEqual(item["currency"], "XTR")
            self.assertEqual(item["status"], "active")
            self.assertIsNone(item.get("asset_id"))

            public = self.client.get("/api/marketplace/orders")
            self.assertEqual(public.status_code, 200)
            self.assertEqual([row["id"] for row in public.json()["items"]], [item["id"]])
            mine = self.client.get("/api/marketplace/mine", headers=owner_headers)
            self.assertEqual(mine.status_code, 200)
            self.assertEqual([row["id"] for row in mine.json()["items"]], [item["id"]])
            stats = self.client.get("/api/marketplace/stats")
            self.assertEqual(stats.json()["active_buy_requests"], 1)
            self.assertEqual(stats.json()["active_listings"], 0)

    def test_orders_are_shared_and_stats_are_server_calculated(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Plush Pepe", "price": "2.5", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        mine = self.client.get("/api/marketplace/mine", headers={"X-Telegram-Init-Data": "user-a"})
        self.assertEqual(mine.status_code, 200)
        self.assertEqual([item["id"] for item in mine.json()["items"]], [order_id])
        public = self.client.get("/api/marketplace/orders", headers={"X-Telegram-Init-Data": "user-b"})
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["total"], 1)
        self.assertEqual(public.json()["items"][0]["id"], order_id)
        self.assertFalse(public.json()["items"][0]["isMine"])
        no_simulation = self.client.post(
            f"/api/marketplace/orders/{order_id}/simulate-fill",
            headers={"X-Telegram-Init-Data": "user-b"},
        )
        self.assertEqual(no_simulation.status_code, 404)
        stats = self.client.get("/api/marketplace/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["total_orders"], 1)
        self.assertEqual(stats.json()["floor_ton"], 2.5)
        self.assertEqual(stats.json()["floor_stars"], 0)
        unchanged = self.client.get("/api/marketplace/orders?status=active")
        self.assertTrue(any(item["id"] == order_id for item in unchanged.json()["items"]))

    def test_synthetic_fill_route_is_absent_and_owner_can_cancel(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "buy-order", "title": "Cap", "price": 50, "currency": "XTR"},
        )
        order_id = created.json()["item"]["id"]
        fill = self.client.post(
            f"/api/marketplace/orders/{order_id}/simulate-fill",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(fill.status_code, 404)
        cancelled = self.client.delete(
            f"/api/marketplace/orders/{order_id}",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["item"]["status"], "cancelled")

    def test_offer_history_pagination_keeps_account_scope(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        for index in range(3):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner_headers,
                json={"kind": "listing", "title": f"Paginated Gift {index}", "price": "1", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            offered = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order.json()["item"]["id"], "amount": "1"},
            )
            self.assertEqual(offered.status_code, 201)

        first_page = self.client.get(
            "/api/marketplace/offers?limit=2&offset=0", headers=sender_headers,
        )
        second_page = self.client.get(
            "/api/marketplace/offers?limit=2&offset=2", headers=sender_headers,
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 3)
        self.assertEqual(len(first_page.json()["items"]), 2)
        self.assertEqual(len(second_page.json()["items"]), 1)
        self.assertNotEqual(
            first_page.json()["items"][0]["id"],
            first_page.json()["items"][1]["id"],
        )

        unrelated = self.client.get(
            "/api/marketplace/offers?limit=10&offset=0",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.json()["total"], 0)
        self.assertEqual(unrelated.json()["items"], [])

    def test_accepting_offer_creates_an_unpaid_reservation_and_private_deal_history(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=seller_headers,
            json={"kind": "listing", "title": "Verified marketplace asset", "price": "2.5", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers",
            headers=buyer_headers,
            json={"order_id": order_id, "amount": "2.2", "note": "offer"},
        )
        self.assertEqual(offered.status_code, 201, offered.text)
        offer_id = offered.json()["item"]["id"]
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["reservation_created"])
        self.assertFalse(accepted.json()["payments_enabled"])
        self.assertEqual(accepted.json()["deal_status"], "payment_not_enabled")
        self.assertEqual(accepted.json()["item"]["status"], "accepted")

        listing = self.client.get("/api/marketplace/orders?status=matched")
        reserved = next(item for item in listing.json()["items"] if item["id"] == order_id)
        self.assertEqual(reserved["status"], "matched")

        seller_deals = self.client.get("/api/marketplace/deals", headers=seller_headers)
        buyer_deals = self.client.get("/api/marketplace/deals", headers=buyer_headers)
        outsider_deals = self.client.get(
            "/api/marketplace/deals", headers={"X-Telegram-Init-Data": "user-c"}
        )
        self.assertEqual(seller_deals.status_code, 200)
        self.assertEqual(buyer_deals.status_code, 200)
        self.assertEqual(len(seller_deals.json()["items"]), 1)
        self.assertEqual(len(buyer_deals.json()["items"]), 1)
        self.assertEqual(outsider_deals.json()["items"], [])
        deal = buyer_deals.json()["items"][0]
        self.assertEqual(deal["id"], accepted.json()["deal_id"])
        self.assertEqual(deal["status"], "payment_not_enabled")
        self.assertTrue(deal["reservation_active"])
        self.assertFalse(deal["payments_enabled"])
        self.assertFalse(deal["delivery_completed"])

        events = self.client.get(
            f"/api/marketplace/deals/{deal['id']}/events", headers=buyer_headers
        )
        self.assertEqual(events.status_code, 200)
        self.assertEqual(
            {item["type"] for item in events.json()["items"]},
            {"deal_created", "listing_reserved", "payment_not_enabled"},
        )

    def test_offer_creation_rolls_back_when_received_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Offer creation rollback", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_offer_received_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_received'
                   BEGIN SELECT RAISE(ABORT, 'injected offer notification failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                "/api/marketplace/offers",
                headers=sender_headers,
                json={"order_id": order_id, "amount": "1.8"},
            )

        with marketplace._db() as conn:
            offer_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_offers WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
            notification_count = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE order_id = ? AND event_type = 'offer_received'""",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(offer_count, 0)
        self.assertEqual(notification_count, 0)

    def test_offer_permissions_and_withdrawal_are_enforced(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Gift", "price": "3", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        own_offer = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"order_id": order_id, "amount": "2"},
        )
        self.assertEqual(own_offer.status_code, 403)

        offered = self.client.post(
            "/api/marketplace/offers",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"order_id": order_id, "amount": "2"},
        )
        offer_id = offered.json()["item"]["id"]
        wrong_actor = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"action": "accept"},
        )
        self.assertEqual(wrong_actor.status_code, 403)

        withdrawn = self.client.patch(
            f"/api/marketplace/offers/{offer_id}",
            headers={"X-Telegram-Init-Data": "user-b"},
            json={"action": "withdraw"},
        )
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.json()["item"]["status"], "withdrawn")

    def test_offer_author_can_edit_pending_offer_and_currency_rules_are_enforced(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Editable offer asset", "price": "3", "currency": "TON"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "2", "note": "initial"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        edited = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=buyer_headers,
            json={"action": "edit", "amount": "2.4", "note": "updated offer"},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertTrue(edited.json()["edited"])
        self.assertEqual(edited.json()["item"]["amount"], "2.4")
        self.assertEqual(edited.json()["item"]["note"], "updated offer")
        self.assertEqual(edited.json()["item"]["status"], "pending")

        owner_edit = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=owner_headers,
            json={"action": "edit", "amount": "2.5"},
        )
        self.assertEqual(owner_edit.status_code, 403)

        stars_listing = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Stars offer asset", "price": "100", "currency": "XTR"},
        )
        self.assertEqual(stars_listing.status_code, 201, stars_listing.text)
        invalid_stars_create = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": stars_listing.json()["item"]["id"], "amount": "90.5"},
        )
        self.assertEqual(invalid_stars_create.status_code, 422)
        stars_offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": stars_listing.json()["item"]["id"], "amount": "90"},
        )
        self.assertEqual(stars_offer.status_code, 201, stars_offer.text)
        invalid_stars_edit = self.client.patch(
            f"/api/marketplace/offers/{stars_offer.json()['item']['id']}",
            headers=buyer_headers,
            json={"action": "edit", "amount": "90.5"},
        )
        self.assertEqual(invalid_stars_edit.status_code, 422)

    def test_offer_decline_rolls_back_when_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Offer notification rollback", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers",
            headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_direct_offer_decline_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_declined'
                   BEGIN SELECT RAISE(ABORT, 'injected notification write failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.patch(
                f"/api/marketplace/offers/{offer_id}",
                headers=owner_headers,
                json={"action": "decline"},
            )

        with marketplace._db() as conn:
            offer_status = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()["status"]
            declined_notifications = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE offer_id = ? AND event_type = 'offer_declined'""",
                (offer_id,),
            ).fetchone()[0]
        self.assertEqual(offer_status, "pending")
        self.assertEqual(declined_notifications, 0)

    def test_offer_withdrawal_rolls_back_when_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Withdraw rollback gift", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers",
            headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_offer_withdraw_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_withdrawn'
                   BEGIN SELECT RAISE(ABORT, 'injected withdrawal notification failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.patch(
                f"/api/marketplace/offers/{offer_id}",
                headers=sender_headers,
                json={"action": "withdraw"},
            )

        with marketplace._db() as conn:
            offer_status = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()["status"]
            withdrawn_notifications = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE offer_id = ? AND event_type = 'offer_withdrawn'""",
                (offer_id,),
            ).fetchone()[0]
        self.assertEqual(offer_status, "pending")
        self.assertEqual(withdrawn_notifications, 0)

    def test_profile_update_rolls_back_initial_profile_insert_when_update_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_seller_profile_update
                   BEFORE UPDATE ON user_profiles
                   BEGIN SELECT RAISE(ABORT, 'injected seller profile update failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.put(
                "/api/marketplace/profiles/me",
                headers=owner_headers,
                json={"display_name": "Atomic Seller", "bio": "Must not persist"},
            )

        with marketplace._db() as conn:
            profile_count = conn.execute(
                "SELECT COUNT(*) FROM user_profiles WHERE telegram_id = '101'"
            ).fetchone()[0]
        self.assertEqual(profile_count, 0)

    def test_order_creation_rolls_back_when_created_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        title = "Order creation rollback gift"

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_order_created_event
                   BEFORE INSERT ON marketplace_order_events
                   WHEN NEW.event_type = 'created'
                   BEGIN SELECT RAISE(ABORT, 'injected order event failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                "/api/marketplace/orders",
                headers=owner_headers,
                json={"kind": "listing", "title": title, "price": "2", "currency": "TON"},
            )

        with marketplace._db() as conn:
            order_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_orders WHERE user_id = '101' AND title = ?",
                (title,),
            ).fetchone()[0]
            event_count = conn.execute(
                """SELECT COUNT(*) FROM marketplace_order_events
                   WHERE order_id IN (
                       SELECT id FROM marketplace_orders WHERE user_id = '101' AND title = ?
                   ) AND event_type = 'created'""",
                (title,),
            ).fetchone()[0]
        self.assertEqual(order_count, 0)
        self.assertEqual(event_count, 0)

    def test_telegram_stars_listing_and_edit_prices_must_be_whole_numbers(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        fractional = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Fractional Stars", "price": "12.5", "currency": "XTR"},
        )
        self.assertEqual(fractional.status_code, 422)

        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Whole Stars", "price": "12", "currency": "XTR"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]

        fractional_edit = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "13.25"},
        )
        self.assertEqual(fractional_edit.status_code, 422)
        unchanged = self.client.get("/api/marketplace/mine", headers=owner_headers)
        saved = next(item for item in unchanged.json()["items"] if item["id"] == order_id)
        self.assertEqual(saved["price"], "12")

        whole_edit = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "15"},
        )
        self.assertEqual(whole_edit.status_code, 200, whole_edit.text)
        self.assertEqual(whole_edit.json()["item"]["price"], "15")

    def test_order_owner_can_edit_active_order_fields(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Original Gift", "price": "2.5", "currency": "TON", "note": "old"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]

        updated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "3.75", "note": "new note"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["item"]["title"], "Original Gift")
        self.assertEqual(updated.json()["item"]["price"], "3.75")
        self.assertEqual(updated.json()["item"]["note"], "new note")
        self.assertGreaterEqual(updated.json()["item"]["updatedAt"], created.json()["item"]["updatedAt"])

    def test_order_edit_rolls_back_when_revision_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Edit rollback gift", "price": "2.5", "currency": "TON", "note": "original"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_order_revision_insert
                   BEFORE INSERT ON marketplace_order_revisions
                   BEGIN SELECT RAISE(ABORT, 'injected order revision failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.patch(
                f"/api/marketplace/orders/{order_id}",
                headers=owner_headers,
                json={"price": "3.75", "note": "must not persist"},
            )

        with marketplace._db() as conn:
            order = conn.execute(
                "SELECT price, note FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()
            revision_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_order_revisions WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(order["price"], "2.5")
        self.assertEqual(order["note"], "original")
        self.assertEqual(revision_count, 0)

    def test_order_edits_are_recorded_in_private_history_and_activity(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        other_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Before edit", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        updated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "2.5"},
        )
        self.assertEqual(updated.status_code, 200)

        history = self.client.get("/api/marketplace/history", headers=owner_headers)
        own_order = next(item for item in history.json()["items"] if item["id"] == order_id)
        self.assertEqual([event["type"] for event in own_order["events"]], ["created", "updated"])

        activity = self.client.get("/api/marketplace/activity", headers=owner_headers)
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.json()["total"], 2)
        self.assertIn("order_updated", {item["type"] for item in activity.json()["items"]})

        unrelated = self.client.get("/api/marketplace/activity", headers=other_headers)
        self.assertEqual(unrelated.json()["items"], [])
        self.assertEqual(unrelated.json()["total"], 0)

    def test_deal_cancellation_rolls_back_when_lifecycle_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Rollback deal gift", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]
        deal_id = "deal-rollback-atomicity"

        with marketplace._db() as conn:
            conn.execute(
                "UPDATE marketplace_orders SET status = 'matched' WHERE id = ?",
                (order_id,),
            )
            conn.execute(
                """INSERT INTO marketplace_deals
                   (id, order_id, offer_id, listing_owner_id, offer_sender_id,
                    agreed_amount, currency, created_at, status)
                   VALUES (?, ?, ?, '101', '202', '1.8', 'TON', ?, 'payment_not_enabled')""",
                (deal_id, order_id, offer_id, int(time.time())),
            )
            conn.execute(
                """CREATE TRIGGER fail_deal_lifecycle_insert
                   BEFORE INSERT ON marketplace_deal_lifecycle_events
                   BEGIN SELECT RAISE(ABORT, 'injected lifecycle write failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(
                f"/api/marketplace/deals/{deal_id}/actions",
                headers=sender_headers,
                json={"action": "cancel"},
            )

        with marketplace._db() as conn:
            deal = conn.execute(
                "SELECT status FROM marketplace_deals WHERE id = ?", (deal_id,)
            ).fetchone()
            listing = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()
            lifecycle_count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_deal_lifecycle_events WHERE deal_id = ?",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(deal["status"], "payment_not_enabled")
        self.assertEqual(listing["status"], "matched")
        self.assertEqual(lifecycle_count, 0)

    def test_cancelling_order_declines_pending_offers_and_notifies_senders(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Cancelled order gift", "price": "2", "currency": "TON"},
        )
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        offer_id = offer.json()["item"]["id"]

        cancelled = self.client.delete(f"/api/marketplace/orders/{order_id}", headers=owner_headers)
        self.assertEqual(cancelled.status_code, 200)

        sender_offers = self.client.get("/api/marketplace/offers", headers=sender_headers)
        updated_offer = next(item for item in sender_offers.json()["items"] if item["id"] == offer_id)
        self.assertEqual(updated_offer["status"], "declined")
        notifications = self.client.get("/api/marketplace/notifications", headers=sender_headers)
        self.assertIn("offer_declined", {item["type"] for item in notifications.json()["items"]})

        owner_offers = self.client.get("/api/marketplace/offers", headers=owner_headers)
        self.assertEqual(
            next(item for item in owner_offers.json()["items"] if item["id"] == offer_id)["status"],
            "declined",
        )

    def test_order_cancellation_rolls_back_when_cancelled_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        order = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Order cancellation event rollback", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_order_cancelled_event
                   BEFORE INSERT ON marketplace_order_events
                   WHEN NEW.event_type = 'cancelled'
                   BEGIN SELECT RAISE(ABORT, 'injected cancellation event failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.delete(
                f"/api/marketplace/orders/{order_id}",
                headers=owner_headers,
            )

        with marketplace._db() as conn:
            order_status = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()["status"]
            cancelled_event_count = conn.execute(
                """SELECT COUNT(*) FROM marketplace_order_events
                   WHERE order_id = ? AND event_type = 'cancelled'""",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(order_status, "active")
        self.assertEqual(cancelled_event_count, 0)

    def test_order_cancellation_rolls_back_offer_decline_when_notification_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Rollback cancellation gift", "price": "2", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201, order.text)
        order_id = order.json()["item"]["id"]
        offer = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        offer_id = offer.json()["item"]["id"]

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_offer_decline_notification
                   BEFORE INSERT ON marketplace_notifications
                   WHEN NEW.event_type = 'offer_declined'
                   BEGIN SELECT RAISE(ABORT, 'injected notification write failure'); END"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.client.delete(
                f"/api/marketplace/orders/{order_id}",
                headers=owner_headers,
            )

        with marketplace._db() as conn:
            order_status = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()["status"]
            offer_status = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()["status"]
            declined_notifications = conn.execute(
                """SELECT COUNT(*) FROM marketplace_notifications
                   WHERE offer_id = ? AND event_type = 'offer_declined'""",
                (offer_id,),
            ).fetchone()[0]
        self.assertEqual(order_status, "active")
        self.assertEqual(offer_status, "pending")
        self.assertEqual(declined_notifications, 0)

    def test_order_reports_require_auth_and_prevent_duplicates_and_self_reports(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        reporter_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Reportable gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]

        missing_auth = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports", json={"reason": "scam"},
        )
        self.assertEqual(missing_auth.status_code, 401)
        self_report = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=owner_headers, json={"reason": "scam"},
        )
        self.assertEqual(self_report.status_code, 403)

        reported = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers,
            json={"reason": "misleading", "details": "  Description does not match the gift.  "},
        )
        self.assertEqual(reported.status_code, 201)
        self.assertEqual(reported.json()["report"]["status"], "open")
        duplicate = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers, json={"reason": "spam"},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_admin_order_removal_rolls_back_when_cancel_event_insert_fails(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        reporter_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=owner_headers,
            json={"kind": "listing", "title": "Admin removal rollback", "price": "3", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]
        report = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers,
            json={"reason": "scam"},
        )
        self.assertEqual(report.status_code, 201, report.text)
        report_id = report.json()["report"]["id"]

        def admin_check(value):
            if value != "admin-session":
                raise api.HTTPException(status_code=403, detail="Administrator access only")
            return {"id": "999"}

        with marketplace._db() as conn:
            conn.execute(
                """CREATE TRIGGER fail_admin_cancel_event
                   BEFORE INSERT ON marketplace_order_events
                   WHEN NEW.event_type = 'cancelled'
                   BEGIN SELECT RAISE(ABORT, 'injected admin cancellation event failure'); END"""
            )

        with patch.object(api, "_require_admin", side_effect=admin_check):
            with self.assertRaises(sqlite3.IntegrityError):
                self.client.patch(
                    f"/api/marketplace/admin/reports/{report_id}",
                    headers={"X-Telegram-Init-Data": "admin-session"},
                    json={"action": "remove_order"},
                )

        with marketplace._db() as conn:
            order_status = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()["status"]
            report_status = conn.execute(
                "SELECT status FROM marketplace_order_reports WHERE id = ?", (report_id,)
            ).fetchone()["status"]
            cancellation_events = conn.execute(
                """SELECT COUNT(*) FROM marketplace_order_events
                   WHERE order_id = ? AND event_type = 'cancelled'""",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(order_status, "active")
        self.assertEqual(report_status, "open")
        self.assertEqual(cancellation_events, 0)

    def test_admin_report_listing_is_rate_limited(self):
        with patch.object(api, "_require_admin", return_value={"id": "909"}), \
             patch.object(marketplace, "_enforce_user_rate_limit") as limiter:
            response = self.client.get(
                "/api/marketplace/admin/reports",
                headers={"X-Telegram-Init-Data": "admin-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        limiter.assert_called_once_with("909", "list_order_reports", 60)

    def test_admin_security_audit_view_is_rate_limited(self):
        with patch.object(api, "_require_admin", return_value={"id": "909"}), \
             patch.object(marketplace, "_enforce_user_rate_limit") as limiter:
            response = self.client.get(
                "/api/marketplace/admin/security-audit",
                headers={"X-Telegram-Init-Data": "admin-session"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        limiter.assert_called_once_with("909", "list_security_audit", 30)

    def test_admin_can_dismiss_or_remove_reported_orders(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        reporter_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Moderated gift", "price": "3", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        report = self.client.post(
            f"/api/marketplace/orders/{order_id}/reports",
            headers=reporter_headers, json={"reason": "scam", "details": "Suspicious listing"},
        )
        report_id = report.json()["report"]["id"]

        def admin_check(value):
            if value != "admin-session":
                raise api.HTTPException(status_code=403, detail="Administrator access only")
            return {"id": "999"}

        with patch.object(api, "_require_admin", side_effect=admin_check):
            forbidden = self.client.get("/api/marketplace/admin/reports", headers=reporter_headers)
            self.assertEqual(forbidden.status_code, 403)
            queue = self.client.get(
                "/api/marketplace/admin/reports",
                headers={"X-Telegram-Init-Data": "admin-session"},
            )
            self.assertEqual(queue.status_code, 200)
            self.assertEqual(queue.json()["total"], 1)
            self.assertEqual(queue.json()["items"][0]["reason"], "scam")

            removed = self.client.patch(
                f"/api/marketplace/admin/reports/{report_id}",
                headers={"X-Telegram-Init-Data": "admin-session"},
                json={"action": "remove_order"},
            )
            self.assertEqual(removed.status_code, 200)
            self.assertTrue(removed.json()["order_removed"])

            second_listing = self.client.post(
                "/api/marketplace/orders",
                headers=owner_headers,
                json={"kind": "listing", "title": "Dismissed listing", "price": "2", "currency": "TON"},
            )
            second_report = self.client.post(
                f"/api/marketplace/orders/{second_listing.json()['item']['id']}/reports",
                headers=reporter_headers,
                json={"reason": "misleading"},
            )
            dismissed = self.client.patch(
                f"/api/marketplace/admin/reports/{second_report.json()['report']['id']}",
                headers={"X-Telegram-Init-Data": "admin-session"},
                json={"action": "dismiss"},
            )
            self.assertEqual(dismissed.status_code, 200, dismissed.text)
            self.assertEqual(dismissed.json()["status"], "dismissed")

            outsider_audit = self.client.get(
                "/api/marketplace/admin/security-audit",
                headers=reporter_headers,
            )
            self.assertEqual(outsider_audit.status_code, 403)
            audit = self.client.get(
                "/api/marketplace/admin/security-audit",
                headers={"X-Telegram-Init-Data": "admin-session"},
            )
            self.assertEqual(audit.status_code, 200, audit.text)
            self.assertEqual(audit.json()["total"], 2)
            actions = {event["action"] for event in audit.json()["items"]}
            self.assertEqual(actions, {"admin_order_removed", "admin_report_dismissed"})
            removed_event = next(event for event in audit.json()["items"] if event["action"] == "admin_order_removed")
            dismissed_event = next(event for event in audit.json()["items"] if event["action"] == "admin_report_dismissed")
            self.assertEqual(removed_event["actor_telegram_id"], "999")
            self.assertEqual(removed_event["target_type"], "order")
            self.assertEqual(removed_event["metadata"], {"report_id": report_id})
            self.assertEqual(dismissed_event["actor_telegram_id"], "999")
            self.assertEqual(dismissed_event["target_type"], "order_report")
            self.assertEqual(len(removed_event["target_hash"]), 64)
            self.assertEqual(len(dismissed_event["target_hash"]), 64)

        mine = self.client.get("/api/marketplace/mine", headers=owner_headers)
        removed_listing = next(item for item in mine.json()["items"] if item["id"] == order_id)
        self.assertEqual(removed_listing["status"], "cancelled")
        with marketplace._db() as conn:
            status = conn.execute(
                "SELECT status FROM marketplace_order_reports WHERE id = ?", (report_id,),
            ).fetchone()["status"]
        self.assertEqual(status, "removed")

    def test_order_edit_rejects_non_owner_and_inactive_orders(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        other_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Protected Gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        forbidden = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=other_headers,
            json={"title": "Hijacked"},
        )
        self.assertEqual(forbidden.status_code, 403)

        cancelled = self.client.delete(f"/api/marketplace/orders/{order_id}", headers=owner_headers)
        self.assertEqual(cancelled.status_code, 200)
        inactive = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "4"},
        )
        self.assertEqual(inactive.status_code, 409)

    def test_buy_request_author_can_edit_requested_asset_title(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers,
            json={"kind": "buy-order", "title": "Plush Pepe", "price": "50", "currency": "XTR"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]
        updated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=headers,
            json={"title": "Plush Pepe #123", "price": "55"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["item"]["title"], "Plush Pepe #123")
        self.assertEqual(updated.json()["item"]["price"], "55")

    def test_order_asset_metadata_cannot_be_edited(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers,
            json={"kind": "listing", "title": "Original Gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        response = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=headers,
            json={"title": "Invented Gift"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("immutable", response.json()["detail"].lower())

    def test_order_edit_validates_payload_and_requires_auth(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Gift", "price": "2", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        blank = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"title": "   "},
        )
        self.assertEqual(blank.status_code, 422)
        invalid_price = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers,
            json={"price": "-1"},
        )
        self.assertEqual(invalid_price.status_code, 422)
        empty = self.client.patch(
            f"/api/marketplace/orders/{order_id}", headers=owner_headers, json={},
        )
        self.assertEqual(empty.status_code, 422)
        unauthenticated = self.client.patch(
            f"/api/marketplace/orders/{order_id}", json={"title": "No auth"},
        )
        self.assertEqual(unauthenticated.status_code, 401)

    def test_mutations_require_telegram_auth(self):
        response = self.client.post(
            "/api/marketplace/orders",
            json={"kind": "listing", "title": "Gift", "price": 1, "currency": "TON"},
        )
        self.assertEqual(response.status_code, 401)


    def test_create_order_rate_limit_is_persistent_and_returns_retry_after(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        payload = {"kind": "listing", "title": "Gift", "price": 1, "currency": "TON"}
        for _ in range(8):
            response = self.client.post("/api/marketplace/orders", headers=headers, json=payload)
            self.assertEqual(response.status_code, 201)
        limited = self.client.post("/api/marketplace/orders", headers=headers, json=payload)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_offer_status_mutations_are_rate_limited(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order = self.client.post(
            "/api/marketplace/orders", headers=owner_headers,
            json={"kind": "listing", "title": "Rate limit offer update", "price": "1", "currency": "TON"},
        )
        self.assertEqual(order.status_code, 201)
        offered = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order.json()["item"]["id"], "amount": "1"},
        )
        self.assertEqual(offered.status_code, 201)
        offer_id = offered.json()["item"]["id"]

        for index in range(30):
            response = self.client.patch(
                f"/api/marketplace/offers/{offer_id}", headers=owner_headers,
                json={"action": "decline"},
            )
            self.assertIn(response.status_code, (200, 409))
        limited = self.client.patch(
            f"/api/marketplace/offers/{offer_id}", headers=owner_headers,
            json={"action": "decline"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_create_offer_rate_limit_is_persistent_and_returns_retry_after(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        order_ids = []
        with marketplace._db() as conn:
            now = 1700000000
            for index in range(16):
                order_id = f"gr-rate-{index:02d}"
                conn.execute(
                    """INSERT INTO marketplace_orders
                    (id, user_id, owner_name, kind, title, price, currency, status, created_at, updated_at, note)
                    VALUES (?, '101', 'Tester A', 'listing', ?, '1', 'TON', 'active', ?, ?, '')""",
                    (order_id, f"Gift {index}", now, now),
                )
                order_ids.append(order_id)
        for order_id in order_ids[:15]:
            response = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order_id, "amount": "1"},
            )
            self.assertEqual(response.status_code, 201)
        limited = self.client.post(
            "/api/marketplace/offers", headers=sender_headers,
            json={"order_id": order_ids[15], "amount": "1"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_cors_rejects_untrusted_origins(self):
        response = self.client.options(
            "/api/marketplace/orders",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_cors_allows_patch_methods_used_by_marketplace(self):
        response = self.client.options(
            "/api/marketplace/offers/demo-offer",
            headers={
                "Origin": "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type,x-telegram-init-data",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
        )
        self.assertIn("PATCH", response.headers.get("access-control-allow-methods", ""))

    def test_cors_allows_production_mini_app_origin(self):
        response = self.client.options(
            "/api/marketplace/orders",
            headers={
                "Origin": "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-telegram-init-data",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://hassle-nft-market.fj7kmzkhn9.workers.dev",
        )


    def test_notification_history_pagination_is_private(self):
        owner_headers = {"X-Telegram-Init-Data": "user-a"}
        sender_headers = {"X-Telegram-Init-Data": "user-b"}
        for index in range(3):
            order = self.client.post(
                "/api/marketplace/orders", headers=owner_headers,
                json={"kind": "listing", "title": f"Notification Page Gift {index}", "price": "1", "currency": "TON"},
            )
            self.assertEqual(order.status_code, 201)
            offered = self.client.post(
                "/api/marketplace/offers", headers=sender_headers,
                json={"order_id": order.json()["item"]["id"], "amount": "1"},
            )
            self.assertEqual(offered.status_code, 201)

        first_page = self.client.get(
            "/api/marketplace/notifications?limit=2&offset=0", headers=owner_headers,
        )
        second_page = self.client.get(
            "/api/marketplace/notifications?limit=2&offset=2", headers=owner_headers,
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 3)
        self.assertEqual(len(first_page.json()["items"]), 2)
        self.assertEqual(len(second_page.json()["items"]), 1)
        unrelated = self.client.get(
            "/api/marketplace/notifications?limit=10",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.json()["total"], 0)
        self.assertEqual(unrelated.json()["items"], [])

    def test_account_notifications_are_private_and_read_state_persists(self):
        headers_a = {"X-Telegram-Init-Data": "user-a"}
        headers_b = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers_a,
            json={"kind": "listing", "title": "Notification gift", "price": "4", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        offered = self.client.post(
            "/api/marketplace/offers", headers=headers_b,
            json={"order_id": created.json()["item"]["id"], "amount": "3.5"},
        )
        self.assertEqual(offered.status_code, 201)
        owner_feed = self.client.get("/api/marketplace/notifications", headers=headers_a)
        self.assertEqual(owner_feed.status_code, 200)
        self.assertEqual(owner_feed.json()["items"][0]["type"], "offer_received")
        notification_id = owner_feed.json()["items"][0]["id"]
        sender_feed = self.client.get("/api/marketplace/notifications", headers=headers_b)
        self.assertEqual(sender_feed.json()["items"], [])
        forbidden = self.client.patch(
            f"/api/marketplace/notifications/{notification_id}/read", headers=headers_b,
        )
        self.assertEqual(forbidden.status_code, 404)
        marked = self.client.patch(
            f"/api/marketplace/notifications/{notification_id}/read", headers=headers_a,
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(
            self.client.get("/api/marketplace/notifications", headers=headers_a).json()["unread_count"],
            0,
        )
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offered.json()['item']['id']}", headers=headers_a,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        buyer_deals = self.client.get("/api/marketplace/deals", headers=headers_b)
        self.assertEqual(len(buyer_deals.json()["items"]), 1)
        self.assertEqual(buyer_deals.json()["items"][0]["status"], "payment_not_enabled")
        self.assertFalse(buyer_deals.json()["payments_enabled"])
        sender_stats = self.client.get("/api/marketplace/account/stats", headers=headers_b)
        self.assertEqual(sender_stats.status_code, 200)
        self.assertEqual(sender_stats.json()["deals"], 1)

    def test_account_activity_timeline_is_private_and_paginated(self):
        headers_a = {"X-Telegram-Init-Data": "user-a"}
        headers_b = {"X-Telegram-Init-Data": "user-b"}
        headers_c = {"X-Telegram-Init-Data": "user-c"}
        created = self.client.post(
            "/api/marketplace/orders", headers=headers_a,
            json={"kind": "listing", "title": "Timeline gift", "price": "4", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers", headers=headers_b,
            json={"order_id": order_id, "amount": "3.5"},
        )
        self.assertEqual(offered.status_code, 201)
        owner_activity = self.client.get("/api/marketplace/activity?limit=10", headers=headers_a)
        self.assertEqual(owner_activity.status_code, 200)
        owner_types = {item["type"] for item in owner_activity.json()["items"]}
        self.assertIn("order_created", owner_types)
        self.assertIn("offer_received", owner_types)
        sender_activity = self.client.get("/api/marketplace/activity?limit=10", headers=headers_b)
        self.assertIn("offer_sent", {item["type"] for item in sender_activity.json()["items"]})
        unrelated = self.client.get("/api/marketplace/activity?limit=10", headers=headers_c)
        self.assertEqual(unrelated.json()["items"], [])

    def test_dispute_resolution_requires_two_distinct_admins_and_never_executes(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Admin dispute gift", "price": "10", "currency": "TON"},
        )
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "admin dispute"},
        )
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            conn.execute("UPDATE marketplace_deals SET status='disputed' WHERE id=?", (deal_id,))
            conn.commit()
        endpoint = f"/api/marketplace/admin/deals/{deal_id}/dispute-approvals"
        with patch.dict(os.environ, {"ADMIN_TELEGRAM_IDS": "101,202"}):
            first = self.client.post(
                endpoint, headers=seller_headers,
                json={"decision": "refund_buyer", "idempotency_key": f"{deal_id}:admin-101"},
            )
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["approvals"], 1)
            self.assertFalse(first.json()["ready_for_execution"])
            self.assertFalse(first.json()["execution_enabled"])
            conflict = self.client.post(
                endpoint, headers=buyer_headers,
                json={"decision": "release_to_seller", "idempotency_key": f"{deal_id}:admin-102-conflict"},
            )
            self.assertEqual(conflict.status_code, 409)
            second = self.client.post(
                endpoint, headers=buyer_headers,
                json={"decision": "refund_buyer", "idempotency_key": f"{deal_id}:admin-102"},
            )
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(second.json()["approvals"], 2)
            self.assertTrue(second.json()["ready_for_execution"])
            self.assertFalse(second.json()["execution_enabled"])
            non_admin = self.client.post(
                endpoint,
                headers={"X-Telegram-Init-Data": "user-c"},
                json={"decision": "refund_buyer", "idempotency_key": f"{deal_id}:admin-103"},
            )
            self.assertEqual(non_admin.status_code, 403)

    def test_paid_delivery_timeout_moves_deal_to_dispute_without_releasing_escrow(self):
        deal_id, _, _ = self._create_ton_deal_with_verified_wallets()
        now = int(time.time())
        with marketplace_offers._db() as conn:
            transition_deal_status(
                conn, deal_id=deal_id, new_status="awaiting_payment", actor_user_id="202",
                idempotency_key=f"{deal_id}:delivery-timeout-awaiting",
                event_type="deal_payment_requested", event_data={}, now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="payment_pending", actor_user_id="202",
                idempotency_key=f"{deal_id}:delivery-timeout-pending",
                event_type="payment_pending", event_data={}, now=now,
            )
            transition_deal_status(
                conn, deal_id=deal_id, new_status="paid", actor_user_id="202",
                idempotency_key=f"{deal_id}:delivery-timeout-paid",
                event_type="payment_confirmed", event_data={"provider":"ton"}, now=now,
            )
            post_ledger_transaction(
                conn, idempotency_key=f"{deal_id}:delivery-timeout-escrow",
                reference_type="deal", reference_id=deal_id, currency="TON",
                debit_account=("ton_treasury_clearing","timeout-test"),
                credit_account=("escrow",deal_id), debit_amount="9", credit_amount="9", now=now,
            )
            conn.commit()
        result = escalate_stalled_paid_deals(now=now+601, timeout_seconds=600)
        self.assertEqual(result["stalled_deliveries_disputed"], 1)
        with marketplace_offers._db() as conn:
            deal = conn.execute("SELECT status FROM marketplace_deals WHERE id=?",(deal_id,)).fetchone()
            escrow = conn.execute(
                "SELECT COUNT(*) FROM marketplace_ledger_entries WHERE account_type='escrow' AND account_id=?",
                (deal_id,),
            ).fetchone()[0]
            refund_events = conn.execute(
                "SELECT COUNT(*) FROM marketplace_escrow_events WHERE deal_id=? AND event_type='refund_confirmed'",
                (deal_id,),
            ).fetchone()[0]
        self.assertEqual(deal["status"], "disputed")
        self.assertEqual(escrow, 1)
        self.assertEqual(refund_events, 0)

    def test_unpaid_reservation_timeout_expires_deal_and_releases_listing(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Timeout gift", "price": "10", "currency": "TON"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "timeout"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            created_at = conn.execute("SELECT created_at FROM marketplace_deals WHERE id=?", (deal_id,)).fetchone()["created_at"]
        result = marketplace_offers.expire_unpaid_marketplace_deals(now=created_at + 301, ttl_seconds=300)
        self.assertEqual(result, {"expired_deals": 1, "reservations_released": 1, "skipped_confirmed_payment": 0})
        with marketplace_offers._db() as conn:
            deal = conn.execute("SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)).fetchone()
            order = conn.execute("SELECT status FROM marketplace_orders WHERE id=?", (listing.json()["item"]["id"],)).fetchone()
        self.assertEqual(deal["status"], "expired")
        self.assertEqual(order["status"], "active")

    def test_unpaid_timeout_never_releases_a_deal_with_confirmed_payment(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Paid timeout gift", "price": "10", "currency": "TON"},
        )
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "paid timeout"},
        )
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            created_at = conn.execute("SELECT created_at FROM marketplace_deals WHERE id=?", (deal_id,)).fetchone()["created_at"]
            conn.execute("UPDATE marketplace_deals SET status='paid' WHERE id=?", (deal_id,))
            conn.commit()
        result = marketplace_offers.expire_unpaid_marketplace_deals(now=created_at + 86401, ttl_seconds=300)
        self.assertEqual(result["expired_deals"], 0)
        self.assertEqual(result["skipped_confirmed_payment"], 0)
        with marketplace_offers._db() as conn:
            deal = conn.execute("SELECT status FROM marketplace_deals WHERE id=?", (deal_id,)).fetchone()
            order = conn.execute("SELECT status FROM marketplace_orders WHERE id=?", (listing.json()["item"]["id"],)).fetchone()
        self.assertEqual(deal["status"], "paid")
        self.assertEqual(order["status"], "matched")

    def test_dispute_requires_confirmed_payment_and_is_idempotent_and_private(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Dispute test gift", "price": "10", "currency": "TON"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "dispute test"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        url = f"/api/marketplace/deals/{deal_id}/disputes"
        payload = {"reason": "The asset delivered does not match the listing.", "idempotency_key": f"{deal_id}:dispute-1"}
        blocked = self.client.post(url, headers=buyer_headers, json=payload)
        self.assertEqual(blocked.status_code, 409)
        with marketplace_offers._db() as conn:
            conn.execute("UPDATE marketplace_deals SET status='paid' WHERE id=?", (deal_id,))
            conn.commit()
        opened = self.client.post(url, headers=buyer_headers, json=payload)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["status"], "disputed")
        replay = self.client.post(url, headers=buyer_headers, json=payload)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        unrelated = self.client.post(
            url,
            headers={"X-Telegram-Init-Data": "user-c"},
            json={**payload, "idempotency_key": f"{deal_id}:dispute-2"},
        )
        self.assertEqual(unrelated.status_code, 404)

    def test_payment_status_endpoint_is_participant_only_and_hides_provider_secrets(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Payment status gift", "price": "10", "currency": "TON"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "payment status"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            from marketplace_payments import create_payment_intent
            conn.execute("UPDATE marketplace_deals SET status='awaiting_payment' WHERE id=?", (deal_id,))
            create_payment_intent(
                conn, deal_id=deal_id, provider="ton", currency="TON", amount="9",
                idempotency_key=f"{deal_id}:ton-checkout", expires_at=1900000000,
                invoice_payload=f"pay:{deal_id}:one", now=1700000000,
            )
            conn.commit()
        response = self.client.get(
            f"/api/marketplace/deals/{deal_id}/payment-status", headers=buyer_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["intents"][0]["provider"], "ton")
        self.assertEqual(response.json()["intents"][0]["status"], "created")
        self.assertNotIn("invoice_payload", response.json()["intents"][0])
        self.assertNotIn("provider_reference", response.json()["intents"][0])
        unrelated = self.client.get(
            f"/api/marketplace/deals/{deal_id}/payment-status",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.status_code, 404)

    def test_escrow_events_are_included_in_private_deal_timeline(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        listing = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Escrow timeline gift", "price": "10", "currency": "TON"},
        )
        self.assertEqual(listing.status_code, 201, listing.text)
        offer = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": listing.json()["item"]["id"], "amount": "9", "note": "timeline"},
        )
        self.assertEqual(offer.status_code, 201, offer.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        with marketplace_offers._db() as conn:
            from marketplace_payments import append_escrow_event
            append_escrow_event(
                conn, deal_id=deal_id, actor_user_id="101",
                event_type="payment_pending", event_key=f"{deal_id}:payment-pending",
                event_data={"provider": "ton"}, now=1700000001,
            )
            conn.commit()
        timeline = self.client.get(f"/api/marketplace/deals/{deal_id}/events", headers=buyer_headers)
        self.assertEqual(timeline.status_code, 200, timeline.text)
        self.assertTrue(any(item["type"] == "payment_pending" for item in timeline.json()["items"]))
        unrelated = self.client.get(
            f"/api/marketplace/deals/{deal_id}/events",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(unrelated.status_code, 404)

    def test_deal_reservation_events_are_included_in_private_account_activity(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Activity timeline reservation", "price": "3", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        offered = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": created.json()["item"]["id"], "amount": "2.8"},
        )
        self.assertEqual(offered.status_code, 201, offered.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offered.json()['item']['id']}",
            headers=seller_headers, json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

        expected = {"deal_created", "listing_reserved", "payment_not_enabled"}
        for headers in (seller_headers, buyer_headers):
            activity = self.client.get("/api/marketplace/activity?limit=100", headers=headers)
            self.assertEqual(activity.status_code, 200, activity.text)
            types = {item["type"] for item in activity.json()["items"]}
            self.assertTrue(expected.issubset(types))
            self.assertEqual(activity.json()["total"], len(activity.json()["items"]))
        outsider = self.client.get(
            "/api/marketplace/activity?limit=100",
            headers={"X-Telegram-Init-Data": "user-c"},
        )
        self.assertEqual(outsider.status_code, 200)
        self.assertEqual(outsider.json()["items"], [])
        self.assertEqual(outsider.json()["total"], 0)

    def test_account_activity_requires_verified_telegram_session(self):
        response = self.client.get("/api/marketplace/activity")
        self.assertEqual(response.status_code, 401)

    def test_account_stats_require_verified_telegram_session(self):
        response = self.client.get("/api/marketplace/account/stats")
        self.assertEqual(response.status_code, 401)
    
    def test_account_stats_do_not_count_matched_reservations_as_completed(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "Reserved but unpaid", "price": "4", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        with marketplace._db() as connection:
            connection.execute(
                "UPDATE marketplace_orders SET status='matched' WHERE id=?",
                (order_id,),
            )

        response = self.client.get(
            "/api/marketplace/account/stats",
            headers={"X-Telegram-Init-Data": "user-a"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_orders"], 0)
        self.assertEqual(response.json()["completed_orders"], 0)



    def test_account_stats_include_real_deals_and_private_seller_settlement_balances(self):
        deal_id, _, _ = self._create_stars_deal_with_intent()
        with marketplace_offers._db() as conn:
            conn.execute("UPDATE marketplace_deals SET status='completed' WHERE id=?", (deal_id,))
            post_ledger_transaction(
                conn,
                idempotency_key=f"{deal_id}:stats-seller-credit",
                reference_type="deal",
                reference_id=deal_id,
                currency="XTR",
                debit_account=("escrow", deal_id),
                credit_account=("seller_payable", "101"),
                debit_amount="9",
                credit_amount="9",
                now=1700000100,
            )
            conn.commit()

        seller_stats = self.client.get(
            "/api/marketplace/account/stats",
            headers={"X-Telegram-Init-Data":"user-a"},
        )
        buyer_stats = self.client.get(
            "/api/marketplace/account/stats",
            headers={"X-Telegram-Init-Data":"user-b"},
        )
        self.assertEqual(seller_stats.status_code, 200, seller_stats.text)
        self.assertEqual(seller_stats.json()["deals"], 1)
        self.assertEqual(seller_stats.json()["completed_orders"], 1)
        self.assertEqual(
            seller_stats.json()["seller_pending_settlements"],
            [{
                "currency":"XTR",
                "amount":"9",
                "status":"internal_xtr_credit",
                "automatic_withdrawal_available":False,
            }],
        )
        self.assertEqual(buyer_stats.json()["deals"], 1)
        self.assertEqual(buyer_stats.json()["completed_orders"], 1)
        self.assertEqual(buyer_stats.json()["seller_pending_settlements"], [])

    def test_competing_offer_acceptance_reserves_listing_once_and_declines_competitors(self):
        created = self.client.post(
            "/api/marketplace/orders",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"kind": "listing", "title": "One of One", "price": "8", "currency": "TON"},
        )
        order_id = created.json()["item"]["id"]
        offer_ids = []
        for user_token, amount in (("user-b", "7"), ("user-c", "6.5")):
            offered = self.client.post(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": user_token},
                json={"order_id": order_id, "amount": amount},
            )
            self.assertEqual(offered.status_code, 201)
            offer_ids.append(offered.json()["item"]["id"])
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offer_ids[0]}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        rejected_competitor = self.client.patch(
            f"/api/marketplace/offers/{offer_ids[1]}",
            headers={"X-Telegram-Init-Data": "user-a"},
            json={"action": "accept"},
        )
        self.assertEqual(rejected_competitor.status_code, 409)
        order = self.client.get("/api/marketplace/orders?status=matched")
        self.assertTrue(any(item["id"] == order_id for item in order.json()["items"]))
        for user_token, expected in (("user-b", "accepted"), ("user-c", "declined")):
            offers = self.client.get(
                "/api/marketplace/offers",
                headers={"X-Telegram-Init-Data": user_token},
            )
            own = next(item for item in offers.json()["items"] if item["order_id"] == order_id)
            self.assertEqual(own["status"], expected)

    def test_offer_acceptance_rolls_back_if_deal_timeline_insert_fails(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Atomic reservation asset", "price": "2", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": order_id, "amount": "1.8"},
        )
        self.assertEqual(offered.status_code, 201, offered.text)
        offer_id = offered.json()["item"]["id"]
        with marketplace._db() as conn:
            conn.execute("""
                CREATE TRIGGER fail_deal_event_insert
                BEFORE INSERT ON marketplace_deal_events
                BEGIN
                    SELECT RAISE(ABORT, 'injected deal event failure');
                END
            """)
        guarded_client = TestClient(api.app, raise_server_exceptions=False)
        try:
            failed = guarded_client.patch(
                f"/api/marketplace/offers/{offer_id}", headers=seller_headers,
                json={"action": "accept"},
            )
        finally:
            guarded_client.close()
        self.assertEqual(failed.status_code, 500)
        with marketplace._db() as conn:
            order = conn.execute(
                "SELECT status FROM marketplace_orders WHERE id = ?", (order_id,)
            ).fetchone()
            offer = conn.execute(
                "SELECT status FROM marketplace_offers WHERE id = ?", (offer_id,)
            ).fetchone()
            deals = conn.execute(
                "SELECT COUNT(*) FROM marketplace_deals WHERE order_id = ?", (order_id,)
            ).fetchone()[0]
        self.assertEqual(order["status"], "active")
        self.assertEqual(offer["status"], "pending")
        self.assertEqual(deals, 0)

    def test_participants_can_cancel_unpaid_reservation_and_reopen_listing(self):
        seller_headers = {"X-Telegram-Init-Data": "user-a"}
        buyer_headers = {"X-Telegram-Init-Data": "user-b"}
        created = self.client.post(
            "/api/marketplace/orders", headers=seller_headers,
            json={"kind": "listing", "title": "Reservation release asset", "price": "4", "currency": "XTR"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["item"]["id"]
        offered = self.client.post(
            "/api/marketplace/offers", headers=buyer_headers,
            json={"order_id": order_id, "amount": "3"},
        )
        self.assertEqual(offered.status_code, 201, offered.text)
        accepted = self.client.patch(
            f"/api/marketplace/offers/{offered.json()['item']['id']}", headers=seller_headers,
            json={"action": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        deal_id = accepted.json()["deal_id"]
        cancelled = self.client.post(
            f"/api/marketplace/deals/{deal_id}/actions", headers=buyer_headers,
            json={"action": "cancel"},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertTrue(cancelled.json()["reservation_released"])
        self.assertFalse(cancelled.json()["payments_enabled"])
        deal = self.client.get("/api/marketplace/deals", headers=seller_headers).json()["items"][0]
        self.assertEqual(deal["status"], "cancelled")
        self.assertFalse(deal["reservation_active"])
        active_orders = self.client.get("/api/marketplace/orders?status=active")
        self.assertTrue(any(item["id"] == order_id for item in active_orders.json()["items"]))


    def test_legacy_table_names_migrate_without_losing_listing_or_event_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = os.path.join(temp_dir, "legacy.sqlite3")
            conn = sqlite3.connect(database)
            conn.execute("""
                CREATE TABLE demo_orders (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, owner_name TEXT NOT NULL,
                    kind TEXT NOT NULL, title TEXT NOT NULL, price TEXT NOT NULL,
                    currency TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "INSERT INTO demo_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("legacy-listing-1", "1001", "seller", "listing", "Real gift", "2",
                 "TON", "active", 10, 10, ""),
            )
            conn.execute("""
                CREATE TABLE demo_order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL, actor_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('created', 'simulated_fill', 'cancelled')),
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES demo_orders(id)
                )
            """)
            conn.execute(
                "INSERT INTO demo_order_events(order_id, actor_user_id, event_type, created_at) VALUES (?, ?, ?, ?)",
                ("legacy-listing-1", "1001", "simulated_fill", 11),
            )
            conn.commit()
            conn.close()

            with patch.object(marketplace, "DB_PATH", database), patch.object(marketplace_offers, "DB_PATH", database):
                marketplace.init_marketplace_db()
                marketplace_offers.init_marketplace_offers_db()
                with marketplace._db() as migrated:
                    listing = migrated.execute(
                        "SELECT title FROM marketplace_orders WHERE id = ?",
                        ("legacy-listing-1",),
                    ).fetchone()
                    event = migrated.execute(
                        "SELECT event_type FROM marketplace_order_events WHERE order_id = ?",
                        ("legacy-listing-1",),
                    ).fetchone()
                    legacy_table = migrated.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_orders'"
                    ).fetchone()
                    foreign_key_errors = migrated.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(listing["title"], "Real gift")
            self.assertEqual(event["event_type"], "legacy_unverified")
            self.assertIsNone(legacy_table)
            self.assertEqual(foreign_key_errors, [])


    def test_legacy_unverified_events_are_excluded_from_activity_items_and_total(self):
        headers = {"X-Telegram-Init-Data": "user-a"}
        created = self.client.post(
            "/api/marketplace/orders",
            headers=headers,
            json={"kind": "listing", "title": "Legacy activity count", "price": "2", "currency": "TON"},
        )
        self.assertEqual(created.status_code, 201)
        order_id = created.json()["item"]["id"]
        with marketplace._db() as conn:
            conn.execute(
                """INSERT INTO marketplace_order_events
                   (order_id, actor_user_id, event_type, created_at)
                   VALUES (?, ?, 'legacy_unverified', ?)""",
                (order_id, "101", int(time.time()) + 1),
            )
        response = self.client.get("/api/marketplace/activity?limit=50", headers=headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], len(payload["items"]))
        self.assertNotIn("legacy_unverified", {item["type"] for item in payload["items"]})


class RequestBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    class FakeRequest:
        def __init__(self, chunks, headers=None):
            self.headers = headers or {}
            self._chunks = list(chunks)
            self.yielded = 0

        async def stream(self):
            for chunk in self._chunks:
                self.yielded += 1
                yield chunk

    async def test_streaming_body_reader_accepts_body_at_limit(self):
        request = self.FakeRequest([b"abc", b"def"])
        result = await api._read_limited_request_body(request, 6)
        self.assertEqual(result, b"abcdef")
        self.assertEqual(request.yielded, 2)

    async def test_streaming_body_reader_rejects_chunked_body_before_reading_later_chunks(self):
        request = self.FakeRequest([b"abc", b"def", b"must-not-be-read"])
        with self.assertRaises(api.HTTPException) as raised:
            await api._read_limited_request_body(request, 5)
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(request.yielded, 2)

    async def test_streaming_body_reader_rejects_oversized_declared_length_without_reading(self):
        request = self.FakeRequest([b"unused"], headers={"content-length": "6"})
        with self.assertRaises(api.HTTPException) as raised:
            await api._read_limited_request_body(request, 5)
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(request.yielded, 0)

    async def test_streaming_body_reader_rejects_invalid_or_negative_content_length(self):
        for value in ("unknown", "-1"):
            request = self.FakeRequest([b"unused"], headers={"content-length": value})
            with self.assertRaises(api.HTTPException) as raised:
                await api._read_limited_request_body(request, 5)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(request.yielded, 0)

