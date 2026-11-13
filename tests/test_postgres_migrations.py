"""Live PostgreSQL migration and adapter smoke tests for CI.

These tests are skipped unless GIFT_RADAR_POSTGRES_CI=1 is explicitly set.
"""
from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import create_engine, inspect, text

from postgres_compat import connect_postgres


@unittest.skipUnless(os.getenv("GIFT_RADAR_POSTGRES_CI") == "1", "PostgreSQL CI service is not enabled")
class PostgresMigrationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.getenv("DATABASE_URL", "").strip()
        if not url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("PostgreSQL CI requires a PostgreSQL DATABASE_URL")
        cls.engine = create_engine(url, future=True, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_schema_is_at_latest_revision_and_contains_core_tables(self):
        with self.engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            self.assertEqual(revision, "0005_marketplace_inventory")
            tables = set(inspect(connection).get_table_names())
        expected = {
            "telegram_users", "telegram_sessions", "demo_orders", "demo_offers",
            "demo_deals", "demo_deal_events", "demo_notifications",
            "catalogue_gifts", "ledger_accounts", "ledger_entries", "ledger_lines",
            "payment_invoices", "payment_events", "telegram_stars_invoices",
            "telegram_stars_payments", "telegram_stars_refund_events", "telegram_stars_terms_acceptances", "marketplace_assets", "marketplace_ownership_proofs", "marketplace_listings", "marketplace_reservations", "marketplace_listing_events",
        }
        self.assertTrue(expected.issubset(tables), f"Missing tables: {sorted(expected - tables)}")

    def test_financial_journal_is_append_only_in_postgres(self):
        suffix = uuid.uuid4().hex
        entry_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO ledger_entries(entry_id,idempotency_key,source_type,source_reference,payload_hash,created_at) "
                "VALUES (:entry,:key,'ci_smoke',:ref,:hash,1)"
            ), {"entry": entry_id, "key": "ci:" + suffix, "ref": suffix, "hash": "0" * 64})
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE ledger_entries SET source_reference='tampered' WHERE entry_id=:entry"
                ), {"entry": entry_id})
    def test_terms_acceptance_is_append_only_in_postgres(self):
        acceptance_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO telegram_stars_terms_acceptances "
                "(acceptance_id,user_id,terms_version,terms_url,accepted_at) "
                "VALUES (:id,:user,'ci-v1','https://example.invalid/terms',1)"
            ), {"id": acceptance_id, "user": str(900000000 + uuid.uuid4().int % 900000000)})
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE telegram_stars_terms_acceptances SET terms_url='https://tampered.invalid' "
                    "WHERE acceptance_id=:id"
                ), {"id": acceptance_id})

    def test_marketplace_inventory_requires_canonical_verified_identity(self):
        asset_id = "ci-asset-" + uuid.uuid4().hex
        proof_id = "ci-proof-" + uuid.uuid4().hex
        listing_id = "ci-listing-" + uuid.uuid4().hex
        user_id = str(900000000 + uuid.uuid4().int % 900000000)
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO marketplace_assets "
                "(asset_id,asset_type,network,canonical_asset_id,collection_address,token_id,"
                "telegram_gift_id,owner_user_id,ownership_state,ownership_checked_at,"
                "ownership_expires_at,created_at,updated_at) "
                "VALUES (:id,'ton_nft','ton-mainnet',:canonical,'EQcollection','42','',:user,"
                "'verified',100,200,100,100)"
            ), {"id": asset_id, "canonical": "ton-mainnet:EQcollection:42", "user": user_id})
            connection.execute(text(
                "INSERT INTO marketplace_ownership_proofs "
                "(proof_id,asset_id,owner_user_id,proof_source,evidence_reference,evidence_hash,"
                "verification_status,verified_at,expires_at,recorded_at) "
                "VALUES (:proof,:asset,:user,'ci-authority','ci-reference',:hash,'verified',100,200,100)"
            ), {"proof": proof_id, "asset": asset_id, "user": user_id, "hash": "a" * 64})
            connection.execute(text(
                "INSERT INTO marketplace_listings "
                "(listing_id,asset_id,seller_user_id,price_raw,currency,status,idempotency_key,created_at,updated_at) "
                "VALUES (:listing,:asset,:user,10,'XTR','active',:idem,100,100)"
            ), {"listing": listing_id, "asset": asset_id, "user": user_id, "idem": "ci-" + uuid.uuid4().hex})
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE marketplace_ownership_proofs SET evidence_reference='tampered' WHERE proof_id=:id"
                ), {"id": proof_id})
        with self.assertRaises(Exception):
            with self.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO marketplace_listings "
                    "(listing_id,asset_id,seller_user_id,price_raw,currency,status,idempotency_key,created_at,updated_at) "
                    "VALUES (:listing,:asset,:user,11,'XTR','active',:idem,101,101)"
                ), {"listing": "ci-listing-" + uuid.uuid4().hex, "asset": asset_id,
                     "user": user_id, "idem": "ci-" + uuid.uuid4().hex})

    def test_postgres_compat_adapter_uses_real_driver_and_row_shapes(self):
        url = os.getenv("DATABASE_URL", "").strip()
        connection = connect_postgres(url)
        try:
            row = connection.execute("SELECT ?::text AS marker", ("gift-radar-ci",)).fetchone()
            self.assertEqual(row[0], "gift-radar-ci")
            self.assertEqual(row["marker"], "gift-radar-ci")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
