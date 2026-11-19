"""PostgreSQL regression coverage for offer notification lifecycle and privacy."""

import os
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest


def test_postgres_offer_notifications_follow_transitions_and_are_private():
    dsn = os.getenv("HASSLE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL service is not configured")

    import psycopg
    import database
    import marketplace
    import marketplace_offers
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from marketplace import create_marketplace_router
    from marketplace_offers import create_marketplace_offers_router

    schema = "offer_notifications_" + uuid.uuid4().hex[:10]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    @contextmanager
    def scoped_connection():
        connection = database.connect()
        try:
            yield connection
        finally:
            connection.close()

    try:
        with patch.dict(os.environ, {
            "HASSLE_NFT_DB_BACKEND": "postgres",
            "DATABASE_URL": dsn,
            "HASSLE_POSTGRES_SCHEMA": schema,
            "HASSLE_VERIFIED_ASSETS_ENABLED": "true",
        }, clear=False), patch.object(
            marketplace, "_db", side_effect=scoped_connection
        ), patch.object(
            marketplace_offers, "_db", side_effect=scoped_connection
        ):
            marketplace.init_marketplace_db()
            marketplace_offers.init_marketplace_offers_db()
            marketplace.upsert_telegram_user({
                "id": "101", "username": "seller", "first_name": "Seller",
            })
            marketplace.upsert_telegram_user({
                "id": "202", "username": "buyer", "first_name": "Buyer",
            })
            marketplace.upsert_telegram_user({
                "id": "303", "username": "outsider", "first_name": "Outsider",
            })
            asset = marketplace.register_verified_asset(
                source="ton_nft",
                external_asset_id="offer-notification-asset",
                owner_telegram_id="101",
                title="Notification lifecycle gift",
                asset_type="gift",
                image_url="",
                verification_method="ton_connect_proof",
                verification_reference="offer-notification-verified-reference",
            )

            def verify_user(value):
                users = {
                    "seller": ("101", "Seller", "seller"),
                    "buyer": ("202", "Buyer", "buyer"),
                    "outsider": ("303", "Outsider", "outsider"),
                }
                user_id, first_name, username = users[value]
                return {"id": user_id, "first_name": first_name, "username": username}

            api = FastAPI()
            api.include_router(create_marketplace_router(verify_user))
            api.include_router(create_marketplace_offers_router(verify_user))

            with patch.object(
                marketplace, "_verified_asset_actions_enabled", return_value=True
            ), patch.object(
                marketplace, "_test_ton_owner_recheck_skipped", return_value=True
            ), TestClient(api) as client:
                listing = client.post(
                    "/api/marketplace/orders",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={
                        "kind": "listing",
                        "asset_id": asset["id"],
                        "title": "Ignored client title",
                        "price": "5",
                        "currency": "TON",
                    },
                )
                assert listing.status_code == 201, listing.text
                order_id = listing.json()["item"]["id"]

                first_offer = client.post(
                    "/api/marketplace/offers",
                    headers={"X-Telegram-Init-Data": "buyer"},
                    json={"order_id": order_id, "amount": "4"},
                )
                assert first_offer.status_code == 201, first_offer.text
                first_offer_id = first_offer.json()["item"]["id"]

                seller_inbox = client.get(
                    "/api/marketplace/notifications",
                    headers={"X-Telegram-Init-Data": "seller"},
                )
                assert seller_inbox.status_code == 200, seller_inbox.text
                seller_items = seller_inbox.json()["items"]
                assert len(seller_items) == 1
                assert seller_items[0]["type"] == "offer_received"
                assert seller_items[0]["offer_id"] == first_offer_id
                seller_notification_id = seller_items[0]["id"]

                outsider_read = client.patch(
                    f"/api/marketplace/notifications/{seller_notification_id}/read",
                    headers={"X-Telegram-Init-Data": "outsider"},
                )
                assert outsider_read.status_code == 404

                declined = client.patch(
                    f"/api/marketplace/offers/{first_offer_id}",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={"action": "decline"},
                )
                assert declined.status_code == 200, declined.text
                buyer_inbox = client.get(
                    "/api/marketplace/notifications",
                    headers={"X-Telegram-Init-Data": "buyer"},
                )
                assert buyer_inbox.status_code == 200, buyer_inbox.text
                buyer_items = buyer_inbox.json()["items"]
                assert [(item["type"], item["offer_id"]) for item in buyer_items] == [
                    ("offer_declined", first_offer_id)
                ]

                # A repeated status update is rejected by the API and cannot
                # emit a second notification for the same transition.
                repeated_decline = client.patch(
                    f"/api/marketplace/offers/{first_offer_id}",
                    headers={"X-Telegram-Init-Data": "seller"},
                    json={"action": "decline"},
                )
                assert repeated_decline.status_code == 409

                second_offer = client.post(
                    "/api/marketplace/offers",
                    headers={"X-Telegram-Init-Data": "buyer"},
                    json={"order_id": order_id, "amount": "3.5"},
                )
                assert second_offer.status_code == 201, second_offer.text
                second_offer_id = second_offer.json()["item"]["id"]

                withdrawn = client.patch(
                    f"/api/marketplace/offers/{second_offer_id}",
                    headers={"X-Telegram-Init-Data": "buyer"},
                    json={"action": "withdraw"},
                )
                assert withdrawn.status_code == 200, withdrawn.text

                seller_after_withdraw = client.get(
                    "/api/marketplace/notifications",
                    headers={"X-Telegram-Init-Data": "seller"},
                )
                assert seller_after_withdraw.status_code == 200
                seller_events = [
                    (item["type"], item["offer_id"])
                    for item in seller_after_withdraw.json()["items"]
                ]
                assert seller_events.count(("offer_received", second_offer_id)) == 1
                assert seller_events.count(("offer_withdrawn", second_offer_id)) == 1

                # Read state is scoped to the owning Telegram account.
                mark_read = client.patch(
                    f"/api/marketplace/notifications/{seller_notification_id}/read",
                    headers={"X-Telegram-Init-Data": "seller"},
                )
                assert mark_read.status_code == 200
                assert mark_read.json() == {"read": True, "changed": True}
                read_again = client.patch(
                    f"/api/marketplace/notifications/{seller_notification_id}/read",
                    headers={"X-Telegram-Init-Data": "seller"},
                )
                assert read_again.status_code == 200
                assert read_again.json() == {"read": True, "changed": False}

            with database.db() as conn:
                counts = conn.execute(
                    "SELECT user_id, event_type, offer_id, COUNT(*) AS total "
                    "FROM marketplace_notifications GROUP BY user_id, event_type, offer_id"
                ).fetchall()
            observed = {
                (row["user_id"], row["event_type"], row["offer_id"]): row["total"]
                for row in counts
            }
            assert observed == {
                ("101", "offer_received", first_offer_id): 1,
                ("202", "offer_declined", first_offer_id): 1,
                ("101", "offer_received", second_offer_id): 1,
                ("101", "offer_withdrawn", second_offer_id): 1,
            }

    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute("SET search_path TO public")
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
