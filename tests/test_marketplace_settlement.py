import asyncio
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from sqlalchemy import create_engine, text

from payment_routes import PaymentEvidence, PaymentIntent, TonUsdtTestnetPaymentRoute

from financial_ledger import (
    get_account_balance, invoices, payment_events,
    post_journal_entry,
)
from marketplace_inventory import (
    confirm_nft_escrow_deposit, create_escrow_pending_listing,
    record_wallet_owned_nft, reserve_verified_listing, save_verified_wallet, cancel_listing_reservation,
)
from marketplace_settlement import (
    create_deal_from_reservation, init_marketplace_settlement_schema,
    create_deal_payment_intent_via_route, confirm_deal_payment_via_route,
    mark_deal_nft_transfer_pending, reserve_verified_deal_payment,
    confirm_deal_nft_transfer, refund_unfulfilled_deal, expire_unpaid_marketplace_deals, open_deal_dispute, resolve_deal_dispute, list_reconciliation_required_deals, add_deal_reconciliation_note, marketplace_deal_events,
)


class MarketplaceSettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'settlement.sqlite3'}", future=True)
        init_marketplace_settlement_schema(self.engine)
        self.seller_wallet = "0:" + "11" * 32
        self.buyer_wallet = "0:" + "55" * 32
        self.collection = "0:" + "22" * 32
        self.item = "0:" + "33" * 32
        self.escrow = "0:" + "44" * 32
        self.evidence = {
            "verified": True, "owner_wallet_address": self.seller_wallet,
            "collection_address": self.collection, "token_id": "7",
            "item_address": self.item, "evidence_hash": "a" * 64,
            "provider_ids": ["tonapi", "toncenter"],
        }
        asset = record_wallet_owned_nft(
            user_id="101", wallet_address=self.seller_wallet,
            asset_type="ton_nft", collection_address=self.collection,
            token_id="7", item_address=self.item, evidence=self.evidence,
            engine=self.engine, now=100,
        )
        self.asset_id = asset["asset_id"]
        listing = create_escrow_pending_listing(
            user_id="101", asset_id=self.asset_id, price_raw=250,
            currency="GRAM", idempotency_key="listing-settlement-0001",
            engine=self.engine, now=110,
        )
        self.listing_id = listing["listing_id"]
        confirm_nft_escrow_deposit(
            listing_id=self.listing_id, tx_hash="ab" * 32, event_index=0,
            verified_escrow_owner=self.escrow, expected_escrow_owner=self.escrow,
            evidence_hash="b" * 64, provider_ids=["tonapi", "toncenter"],
            engine=self.engine, now=120,
        )
        save_verified_wallet(user_id="202", address=self.buyer_wallet,
                             network="ton-mainnet", engine=self.engine, now=125)
        reservation = reserve_verified_listing(
            listing_id=self.listing_id, buyer_user_id="202", engine=self.engine, now=130,
        )
        self.reservation_id = reservation["reservation_id"]
        deal = create_deal_from_reservation(
            reservation_id=self.reservation_id, buyer_user_id="202",
            buyer_wallet_address=self.buyer_wallet, idempotency_key="deal-settlement-0001",
            engine=self.engine, now=135,
        )
        self.deal_id = deal["deal_id"]
        self.invoice_id = "invoice-settlement-0001"
        with self.engine.begin() as conn:
            conn.execute(invoices.insert().values(
                invoice_id=self.invoice_id, user_id="202", asset_id="GRAM",
                chain="ton-mainnet", master_address="", asset_kind="native",
                recipient_address=self.escrow, amount_raw="250", payload="gri_settlement",
                expires_at=1000, status="paid", created_at=140,
            ))
            conn.execute(payment_events.insert().values(
                payment_event_id="payment-event-1", invoice_id=self.invoice_id,
                chain="ton-mainnet", tx_hash="cd" * 32, event_index=10,
                asset_id="GRAM", amount_raw="250", recipient_address=self.escrow,
                created_at=141,
            ))
        post_journal_entry(
            idempotency_key="test-credit-buyer-1", source_type="test_payment",
            source_reference=self.invoice_id,
            postings=[
                {"account_id": "asset:clearing:GRAM", "account_type": "asset",
                 "currency": "GRAM", "amount_raw": 250},
                {"account_id": "liability:user:202:GRAM", "account_type": "liability",
                 "currency": "GRAM", "amount_raw": -250},
            ], engine=self.engine,
        )

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()





    def test_payment_route_confirms_deal_without_touching_ledger(self):
        class Gateway:
            async def create_intent(self, **kwargs):
                return PaymentIntent(
                    deal_id=kwargs["deal_id"], amount=kwargs["amount"],
                    currency=kwargs["currency"], reference="grpay:" + kwargs["deal_id"],
                    network="testnet",
                )

            async def verify_payment(self, intent):
                return PaymentEvidence(
                    deal_id=intent.deal_id, reference=intent.reference,
                    amount=intent.amount, currency="tUSDT",
                    transaction_id="ef" * 32, finalized=True, network="testnet",
                )

            async def refund(self, evidence):
                raise AssertionError("Refund is deliberately not part of this test")

        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE marketplace_deals SET currency='USDT' WHERE deal_id=:deal_id"
            ), {"deal_id": self.deal_id})
        buyer_before = get_account_balance(
            "liability:user:202:GRAM", "GRAM", engine=self.engine,
        )
        hold_before = get_account_balance(
            f"liability:deal_hold:{self.deal_id}:USDT", "USDT", engine=self.engine,
        )
        route = TonUsdtTestnetPaymentRoute(
            Gateway(), network="testnet", escrow_address=self.escrow,
            jetton_master="0:" + "66" * 32,
        )
        intent = asyncio.run(create_deal_payment_intent_via_route(
            deal_id=self.deal_id, route=route, actor_user_id="202",
            engine=self.engine,
        ))
        result = asyncio.run(confirm_deal_payment_via_route(
            deal_id=self.deal_id, intent=intent, route=route,
            actor_user_id="202", engine=self.engine, now=150,
        ))
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["status"], "payment_confirmed")
        self.assertEqual(buyer_before, get_account_balance(
            "liability:user:202:GRAM", "GRAM", engine=self.engine,
        ))
        self.assertEqual(hold_before, get_account_balance(
            f"liability:deal_hold:{self.deal_id}:USDT", "USDT", engine=self.engine,
        ))
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT status,payment_invoice_id,payment_event_id FROM marketplace_deals WHERE deal_id=:id"
            ), {"id": self.deal_id}).one()
            self.assertEqual(row.status, "payment_confirmed")
            self.assertEqual(row.payment_invoice_id, intent.reference)
            self.assertEqual(row.payment_event_id, "ef" * 32)
            self.assertEqual(conn.execute(text(
                "SELECT COUNT(*) FROM marketplace_deal_events WHERE deal_id=:id AND event_type='payment_route_confirmed'"
            ), {"id": self.deal_id}).scalar_one(), 1)
        duplicate = asyncio.run(confirm_deal_payment_via_route(
            deal_id=self.deal_id, intent=intent, route=route,
            actor_user_id="202", engine=self.engine, now=151,
        ))
        self.assertTrue(duplicate["duplicate"])

    def test_buyer_can_open_dispute_before_transfer_and_duplicate_is_idempotent(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        result = open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="202",
            reason="Seller has not provided the promised gift",
            engine=self.engine, now=160,
        )
        self.assertEqual(result, {
            "opened": True, "duplicate": False, "status": "disputed"
        })
        duplicate = open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="101",
            reason="Seller has not provided the promised gift",
            engine=self.engine, now=161,
        )
        self.assertTrue(duplicate["duplicate"])
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:id"
            ), {"id": self.deal_id}).scalar_one(), "disputed")

    def test_unrelated_user_cannot_open_deal_dispute(self):
        with self.assertRaises(PermissionError):
            open_deal_dispute(
                deal_id=self.deal_id, actor_user_id="303",
                reason="I want to dispute this transaction",
                engine=self.engine, now=160,
            )

    def test_dispute_during_nft_transfer_requires_reconciliation(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        mark_deal_nft_transfer_pending(
            deal_id=self.deal_id, engine=self.engine, now=155,
        )
        result = open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="101",
            reason="The NFT transfer has an uncertain result",
            engine=self.engine, now=160,
        )
        self.assertEqual(result["status"], "reconciliation_required")
        with self.assertRaises(PermissionError):
            refund_unfulfilled_deal(
                deal_id=self.deal_id, reason="Dispute",
                admin_user_id="999", engine=self.engine, now=170,
            )


    def test_manual_reconciliation_queue_and_admin_notes_are_read_only_and_idempotent(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        mark_deal_nft_transfer_pending(
            deal_id=self.deal_id, engine=self.engine, now=155,
        )
        open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="202",
            reason="Buyer cannot confirm the NFT transfer result",
            engine=self.engine, now=160,
        )
        with self.engine.begin() as conn:
            for event_number in range(24):
                conn.execute(marketplace_deal_events.insert().values(
                    event_id=f"reconciliation-test-{event_number}-{self.deal_id}",
                    deal_id=self.deal_id, actor_type="system", actor_id="",
                    event_type="chain_observation_recorded", event_data="{}",
                    created_at=161 + event_number,
                ))
        queue = list_reconciliation_required_deals(
            engine=self.engine, limit=10, offset=0,
        )
        self.assertEqual(queue["total"], 1)
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(len(queue["items"][0]["events"]), 25)
        item = queue["items"][0]
        self.assertEqual(item["deal_id"], self.deal_id)
        self.assertEqual(item["custody_state"], "escrowed")
        proof_sources = {
            proof["proof_source"]: proof for proof in item["ownership_proofs"]
        }
        self.assertIn("ton_dual_provider", proof_sources)
        self.assertIn("ton_escrow_dual_provider", proof_sources)
        self.assertEqual(proof_sources["ton_dual_provider"]["evidence_hash"], "a" * 64)
        self.assertEqual(proof_sources["ton_escrow_dual_provider"]["evidence_hash"], "b" * 64)
        self.assertTrue(any(
            event["event_type"] == "dispute_requires_reconciliation"
            for event in item["events"]
        ))
        result = add_deal_reconciliation_note(
            deal_id=self.deal_id, admin_user_id="999",
            note="Provider A and provider B evidence need comparison",
            idempotency_key="reconcile-note-0001",
            engine=self.engine, now=170,
        )
        self.assertTrue(result["recorded"])
        duplicate = add_deal_reconciliation_note(
            deal_id=self.deal_id, admin_user_id="999",
            note="Provider A and provider B evidence need comparison",
            idempotency_key="reconcile-note-0001",
            engine=self.engine, now=171,
        )
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(ValueError):
            add_deal_reconciliation_note(
                deal_id=self.deal_id, admin_user_id="999",
                note="A different note using the same key",
                idempotency_key="reconcile-note-0001",
                engine=self.engine, now=172,
            )
        updated = list_reconciliation_required_deals(
            engine=self.engine, limit=10, offset=0,
        )
        self.assertTrue(any(
            event["event_type"] == "reconciliation_note_added"
            for event in updated["items"][0]["events"]
        ))
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:id"
            ), {"id": self.deal_id}).scalar_one(), "reconciliation_required")
        self.assertEqual(get_account_balance(
            f"liability:deal_hold:{self.deal_id}:GRAM", "GRAM", self.engine
        ), -250)

    def test_reconciliation_notes_rejected_for_non_reconciliation_deals(self):
        with self.assertRaises(PermissionError):
            add_deal_reconciliation_note(
                deal_id=self.deal_id, admin_user_id="999",
                note="This deal is not awaiting reconciliation",
                idempotency_key="reconcile-note-0002",
                engine=self.engine, now=170,
            )

    def test_admin_can_dismiss_pre_transfer_dispute_without_releasing_hold(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="202",
            reason="The seller has not responded to messages",
            engine=self.engine, now=160,
        )
        result = resolve_deal_dispute(
            deal_id=self.deal_id, decision="dismiss",
            reason="Evidence did not support the complaint",
            admin_user_id="999", engine=self.engine, now=170,
        )
        self.assertEqual(result["status"], "payment_confirmed")
        self.assertEqual(get_account_balance(
            f"liability:deal_hold:{self.deal_id}:GRAM", "GRAM", self.engine
        ), -250)
        duplicate = resolve_deal_dispute(
            deal_id=self.deal_id, decision="dismiss",
            reason="Evidence did not support the complaint",
            admin_user_id="999", engine=self.engine, now=171,
        )
        self.assertTrue(duplicate["duplicate"])

    def test_admin_refund_resolution_requires_open_dispute(self):
        with self.assertRaises(PermissionError):
            resolve_deal_dispute(
                deal_id=self.deal_id, decision="refund",
                reason="Refund requested after review",
                admin_user_id="999", engine=self.engine, now=160,
            )

    def test_admin_can_refund_pre_transfer_dispute(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="202",
            reason="The seller has not responded to messages",
            engine=self.engine, now=160,
        )
        result = resolve_deal_dispute(
            deal_id=self.deal_id, decision="refund",
            reason="Seller failed to deliver the listed NFT",
            admin_user_id="999", engine=self.engine, now=170,
        )
        self.assertTrue(result["refunded"])
        self.assertEqual(get_account_balance(
            "liability:user:202:GRAM", "GRAM", self.engine
        ), -250)

    def test_admin_cannot_dismiss_dispute_after_transfer_started(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        mark_deal_nft_transfer_pending(
            deal_id=self.deal_id, engine=self.engine, now=155,
        )
        open_deal_dispute(
            deal_id=self.deal_id, actor_user_id="101",
            reason="The transfer appears to be stuck in chain",
            engine=self.engine, now=160,
        )
        with self.assertRaises(PermissionError):
            resolve_deal_dispute(
                deal_id=self.deal_id, decision="dismiss",
                reason="Close this dispute",
                admin_user_id="999", engine=self.engine, now=170,
            )
        with self.assertRaises(PermissionError):
            resolve_deal_dispute(
                deal_id=self.deal_id, decision="refund",
                reason="Refund this disputed transfer",
                admin_user_id="999", engine=self.engine, now=170,
            )


    def test_admin_dispute_resolution_rejects_unknown_decision(self):
        with self.assertRaises(ValueError):
            resolve_deal_dispute(
                deal_id=self.deal_id, decision="pay_seller",
                reason="Attempt unsupported dispute decision",
                admin_user_id="999", engine=self.engine, now=160,
            )

    def test_buyer_cannot_cancel_reservation_after_deal_creation(self):
        with self.assertRaises(PermissionError):
            cancel_listing_reservation(
                reservation_id=self.reservation_id, actor_user_id="202",
                engine=self.engine, now=140,
            )
        with self.assertRaises(PermissionError):
            reserve_verified_deal_payment(
                deal_id=self.deal_id, invoice_id=self.invoice_id,
                engine=self.engine, now=431,
            )

    def test_paid_deal_is_never_expired_by_reservation_worker(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        result = expire_unpaid_marketplace_deals(engine=self.engine, now=431)
        self.assertEqual(result, {"expired": 0, "relisted": 0, "reverification_required": 0})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:id"
            ), {"id": self.deal_id}).scalar_one(), "payment_confirmed")
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": self.listing_id}).scalar_one(), "reserved")

    def test_expired_unpaid_deal_releases_listing_once(self):
        result = expire_unpaid_marketplace_deals(
            engine=self.engine, now=431,
        )
        self.assertEqual(result, {"expired": 1, "relisted": 1, "reverification_required": 0})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:id"
            ), {"id": self.deal_id}).scalar_one(), "expired")
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_reservations WHERE reservation_id=:id"
            ), {"id": self.reservation_id}).scalar_one(), "expired")
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": self.listing_id}).scalar_one(), "active")
        self.assertEqual(
            expire_unpaid_marketplace_deals(engine=self.engine, now=432),
            {"expired": 0, "relisted": 0, "reverification_required": 0},
        )

    def test_expired_unpaid_deal_does_not_relist_stale_escrow_proof(self):
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE marketplace_assets SET ownership_expires_at=400 WHERE asset_id=:id"
            ), {"id": self.asset_id})
        result = expire_unpaid_marketplace_deals(engine=self.engine, now=431)
        self.assertEqual(result, {"expired": 1, "relisted": 0, "reverification_required": 1})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": self.listing_id}).scalar_one(), "pending_verification")

    def test_only_authenticated_buyer_can_reserve_paid_invoice(self):
        with self.assertRaises(PermissionError):
            reserve_verified_deal_payment(
                deal_id=self.deal_id, invoice_id=self.invoice_id,
                actor_user_id="999", engine=self.engine, now=150,
            )
        with self.engine.connect() as conn:
            status = conn.execute(text(
                "SELECT status FROM marketplace_deals WHERE deal_id=:id"
            ), {"id": self.deal_id}).scalar_one()
        self.assertEqual(status, "reserved")

    def test_payment_is_held_until_two_provider_nft_delivery(self):
        result = reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id, engine=self.engine, now=150,
        )
        self.assertTrue(result["reserved"])
        self.assertEqual(get_account_balance("liability:user:202:GRAM", "GRAM", self.engine), 0)
        hold = get_account_balance(f"liability:deal_hold:{self.deal_id}:GRAM", "GRAM", self.engine)
        self.assertEqual(hold, -250)
        mark_deal_nft_transfer_pending(deal_id=self.deal_id, engine=self.engine, now=160)
        proofs = [
            {"provider_id": "tonapi", "successful": True, "finalized": True,
             "tx_hash": "ef" * 32, "item_address": self.item, "old_owner": self.escrow,
             "new_owner": self.buyer_wallet, "evidence_hash": "f" * 64},
            {"provider_id": "toncenter", "successful": True, "finalized": True,
             "tx_hash": "ef" * 32, "item_address": self.item, "old_owner": self.escrow,
             "new_owner": self.buyer_wallet, "evidence_hash": "f" * 64},
        ]
        with mock.patch.dict(os.environ, {"GIFT_RADAR_ESCROW_ADDRESS": self.escrow}):
            settled = confirm_deal_nft_transfer(
                deal_id=self.deal_id, transfer_proofs=proofs,
                current_owner_addresses=[self.buyer_wallet, self.buyer_wallet],
                engine=self.engine, now=170,
            )
        self.assertTrue(settled["settled"])
        self.assertEqual(get_account_balance(f"liability:deal_hold:{self.deal_id}:GRAM", "GRAM", self.engine), 0)
        self.assertEqual(get_account_balance("liability:seller:101:GRAM", "GRAM", self.engine), -250)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": self.listing_id}).scalar_one(), "sold")

    def test_provider_disagreement_never_pays_seller(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id, engine=self.engine, now=150,
        )
        mark_deal_nft_transfer_pending(deal_id=self.deal_id, engine=self.engine, now=160)
        proofs = [
            {"provider_id": "tonapi", "successful": True, "finalized": True,
             "tx_hash": "ef" * 32, "item_address": self.item, "old_owner": self.escrow,
             "new_owner": self.buyer_wallet, "evidence_hash": "f" * 64},
            {"provider_id": "toncenter", "successful": True, "finalized": True,
             "tx_hash": "aa" * 32, "item_address": self.item, "old_owner": self.escrow,
             "new_owner": self.buyer_wallet, "evidence_hash": "f" * 64},
        ]
        with self.assertRaises(ValueError):
            with unittest.mock.patch.dict(os.environ, {"GIFT_RADAR_ESCROW_ADDRESS": self.escrow}):
                confirm_deal_nft_transfer(
                    deal_id=self.deal_id, transfer_proofs=proofs,
                    current_owner_addresses=[self.buyer_wallet], engine=self.engine, now=170,
                )
        self.assertEqual(get_account_balance("liability:seller:101:GRAM", "GRAM", self.engine), 0)


    def test_refund_releases_listing_only_with_fresh_escrow_proof(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        result = refund_unfulfilled_deal(
            deal_id=self.deal_id, reason="seller cannot fulfil",
            admin_user_id="999", engine=self.engine, now=160,
        )
        self.assertTrue(result["refunded"])
        self.assertEqual(get_account_balance(
            "liability:user:202:GRAM", "GRAM", self.engine
        ), -250)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": self.listing_id}).scalar_one(), "active")

    def test_refund_routes_stale_escrow_listing_back_to_verification(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id,
            engine=self.engine, now=150,
        )
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE marketplace_assets SET ownership_expires_at=155 WHERE asset_id=:id"
            ), {"id": self.asset_id})
        result = refund_unfulfilled_deal(
            deal_id=self.deal_id, reason="seller cannot fulfil",
            admin_user_id="999", engine=self.engine, now=160,
        )
        self.assertTrue(result["refunded"])
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(
                "SELECT status FROM marketplace_listings WHERE listing_id=:id"
            ), {"id": self.listing_id}).scalar_one(), "pending_verification")

    def test_ambiguous_nft_transfer_cannot_be_refunded_automatically(self):
        reserve_verified_deal_payment(
            deal_id=self.deal_id, invoice_id=self.invoice_id, engine=self.engine, now=150,
        )
        mark_deal_nft_transfer_pending(deal_id=self.deal_id, engine=self.engine, now=160)
        with self.assertRaises(PermissionError):
            refund_unfulfilled_deal(
                deal_id=self.deal_id, reason="timeout", admin_user_id="999",
                engine=self.engine, now=170,
            )


if __name__ == "__main__":
    unittest.main()
