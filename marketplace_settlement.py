"""Atomic settlement lifecycle for escrow-backed native NFT listings.

This module only moves internal ledger liabilities. It never signs, broadcasts,
or claims that an NFT transfer happened. The transfer confirmation boundary
requires two independent provider proofs and the live item owner to match.
"""
from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, ForeignKey, Index, String, Table, Text, func,
    insert, select, update,
)
from sqlalchemy.engine import Engine

from financial_ledger import (
    _post, _account_balance_in_connection, get_engine, init_financial_schema,
    accounts, invoices, payment_events, stars_invoices, stars_payments, metadata,
)
from marketplace_inventory import (
    init_inventory_schema, marketplace_assets, marketplace_listings,
    marketplace_listing_events, marketplace_reservations, marketplace_ownership_proofs, verified_wallets,
)
from ton_nft_providers import canonical_ton_address


marketplace_deals = Table(
    "marketplace_deals", metadata,
    Column("deal_id", String(64), primary_key=True),
    Column("listing_id", String(64), ForeignKey("marketplace_listings.listing_id"), nullable=False),
    Column("reservation_id", String(64), ForeignKey("marketplace_reservations.reservation_id"), nullable=False, unique=True),
    Column("asset_id", String(64), ForeignKey("marketplace_assets.asset_id"), nullable=False),
    Column("buyer_user_id", String(64), nullable=False),
    Column("seller_user_id", String(64), nullable=False),
    Column("buyer_wallet_address", String(128), nullable=False),
    Column("seller_wallet_address", String(128)),
    Column("escrow_contract_address", String(128)),
    Column("network", String(16), nullable=False, default="ton-mainnet"),
    Column("currency", String(8), nullable=False),
    Column("price_raw", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("payment_invoice_id", String(64), unique=True),
    Column("payment_event_id", String(64), unique=True),
    Column("payment_query_id", BigInteger),
    Column("payment_reference", String(128), unique=True),
    Column("payment_network", String(16)),
    Column("nft_transfer_tx_hash", String(128), unique=True),
    Column("nft_return_tx_hash", String(128), unique=True),
    Column("payout_query_id", BigInteger),
    Column("payout_tx_hash", String(128), unique=True),
    Column("payout_verified_at", BigInteger),
    Column("refund_query_id", BigInteger),
    Column("refund_tx_hash", String(128), unique=True),
    Column("refund_verified_at", BigInteger),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    CheckConstraint("price_raw > 0", name="ck_marketplace_deals_positive_price"),
    CheckConstraint("currency IN ('GRAM','USDT','tUSDT','XTR')", name="ck_marketplace_deals_currency"),
    CheckConstraint("network IN ('ton-mainnet','ton-testnet')", name="ck_marketplace_deals_network"),
    CheckConstraint(
        "status IN ('reserved','payment_confirmed','nft_transfer_pending','payout_pending','refund_pending','settled','disputed','refunded','reconciliation_required','expired')",
        name="ck_marketplace_deals_status",
    ),
)
marketplace_deal_events = Table(
    "marketplace_deal_events", metadata,
    Column("event_id", String(64), primary_key=True),
    Column("deal_id", String(64), ForeignKey("marketplace_deals.deal_id"), nullable=False),
    Column("actor_type", String(16), nullable=False),
    Column("actor_id", String(64), nullable=False, default=""),
    Column("event_type", String(40), nullable=False),
    Column("event_data", Text, nullable=False, default="{}"),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("actor_type IN ('buyer','seller','system','admin')", name="ck_marketplace_deal_event_actor"),
)
Index("idx_marketplace_deals_status_updated", marketplace_deals.c.status, marketplace_deals.c.updated_at)
Index("idx_marketplace_deal_events_deal_created", marketplace_deal_events.c.deal_id, marketplace_deal_events.c.created_at)


def init_marketplace_settlement_schema(engine: Engine | None = None) -> None:
    db = engine or get_engine()
    init_financial_schema(db)
    init_inventory_schema(db)
    if db.dialect.name == "postgresql":
        with db.connect() as conn:
            version = conn.execute(__import__("sqlalchemy").text(
                "SELECT version_num FROM alembic_version"
            )).scalar_one_or_none()
        from financial_ledger import migration_revision_number
        if migration_revision_number(version) < 15:
            raise RuntimeError("Marketplace settlement and inventory migrations through 0015 are required")
        return
    metadata.create_all(db)
    if db.dialect.name == "sqlite":
        from sqlalchemy import text
        with db.begin() as conn:
            for table_name in ("marketplace_deal_events",):
                conn.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {table_name}_no_update BEFORE UPDATE ON {table_name} "
                    "BEGIN SELECT RAISE(ABORT, 'marketplace deal events are append-only'); END"
                ))
                conn.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {table_name}_no_delete BEFORE DELETE ON {table_name} "
                    "BEGIN SELECT RAISE(ABORT, 'marketplace deal events are append-only'); END"
                ))


def _event(conn, *, deal_id: str, event_type: str, actor_type: str = "system",
           actor_id: str = "", data: dict[str, Any] | None = None, now: int) -> None:
    conn.execute(insert(marketplace_deal_events).values(
        event_id=uuid.uuid4().hex, deal_id=deal_id, actor_type=actor_type,
        actor_id=str(actor_id)[:64], event_type=event_type,
        event_data=json.dumps(data or {}, sort_keys=True, separators=(",", ":"))[:2000],
        created_at=now,
    ))


def create_deal_from_reservation(*, reservation_id: str, buyer_user_id: str,
                                 buyer_wallet_address: str, idempotency_key: str,
                                 engine: Engine | None = None,
                                 now: int | None = None) -> dict[str, Any]:
    """Create a deal only from a live reservation and a server-verified buyer wallet."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    uid = str(buyer_user_id)
    if not uid.isdigit() or int(uid) <= 0 or not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("Invalid buyer or idempotency key")
    wallet = canonical_ton_address(buyer_wallet_address)
    if not wallet.startswith("0:"):
        raise ValueError("Only TON basechain wallets on supported networks are accepted")
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        existing = conn.execute(select(marketplace_deals).where(
            marketplace_deals.c.idempotency_key == idempotency_key
        )).mappings().first()
        if existing:
            if existing["reservation_id"] != reservation_id or existing["buyer_user_id"] != uid:
                raise ValueError("Deal idempotency key was reused with different details")
            return {**dict(existing), "duplicate": True}
        reservation_q = select(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == reservation_id
        )
        if lock:
            reservation_q = reservation_q.with_for_update()
        reservation = conn.execute(reservation_q).mappings().first()
        if (reservation is None or reservation["status"] != "active"
                or reservation["buyer_user_id"] != uid or int(reservation["expires_at"]) <= current):
            raise ValueError("Reservation is missing, expired or belongs to another buyer")
        listing_q = select(marketplace_listings).where(
            marketplace_listings.c.listing_id == reservation["listing_id"]
        )
        if lock:
            listing_q = listing_q.with_for_update()
        listing = conn.execute(listing_q).mappings().first()
        if listing is None or listing["status"] != "reserved":
            raise ValueError("Listing is no longer reserved")
        if listing["seller_user_id"] == uid:
            raise PermissionError("Seller cannot buy their own listing")
        asset = conn.execute(select(marketplace_assets).where(
            marketplace_assets.c.asset_id == listing["asset_id"]
        )).mappings().one()
        network = str(asset["network"])
        if network not in {"ton-mainnet", "ton-testnet"}:
            raise ValueError("Marketplace deals require a supported TON network")
        wallet_row = conn.execute(select(verified_wallets).where(
            verified_wallets.c.user_id == uid,
            verified_wallets.c.network == network,
            verified_wallets.c.address == wallet,
            verified_wallets.c.revoked_at.is_(None),
        )).mappings().first()
        if wallet_row is None:
            raise PermissionError("Buyer wallet must be verified on the asset's network")
        if network == "ton-testnet" and listing["currency"] != "tUSDT":
            raise ValueError("TON testnet listings must use the testnet-only tUSDT currency")
        if network == "ton-mainnet" and listing["currency"] == "tUSDT":
            raise ValueError("tUSDT cannot be used for a mainnet asset")
        seller_wallet_source = asset["seller_wallet_address"] or (
            asset["owner_wallet_address"] if asset["custody_state"] == "wallet_owned" else ""
        )
        seller_wallet = canonical_ton_address(seller_wallet_source or "")
        if not seller_wallet.startswith("0:"):
            raise ValueError("Immutable seller wallet snapshot is missing; asset must be re-verified before sale")
        escrow_address = str(
            asset["escrow_contract_address"]
            or os.getenv("TON_TESTNET_ESCROW_ADDRESS" if network == "ton-testnet" else "GIFT_RADAR_ESCROW_ADDRESS", "")
        ).strip()
        escrow_address = canonical_ton_address(escrow_address) if escrow_address else ""
        if not escrow_address.startswith("0:"):
            raise ValueError("The asset has no configured escrow contract address")
        if asset["custody_state"] != "escrowed" or int(asset["ownership_expires_at"] or 0) <= current:
            raise PermissionError("NFT escrow ownership proof is stale")
        deal_id = "grd_" + uuid.uuid4().hex
        conn.execute(insert(marketplace_deals).values(
            deal_id=deal_id, listing_id=listing["listing_id"],
            reservation_id=reservation_id, asset_id=listing["asset_id"],
            buyer_user_id=uid, seller_user_id=listing["seller_user_id"],
            buyer_wallet_address=wallet, seller_wallet_address=seller_wallet,
            escrow_contract_address=escrow_address,
            network=network, currency=listing["currency"],
            price_raw=int(listing["price_raw"]), status="reserved",
            idempotency_key=idempotency_key, created_at=current, updated_at=current,
        ))
        _event(conn, deal_id=deal_id, event_type="deal_reserved", actor_type="buyer",
               actor_id=uid, data={"listing_id": listing["listing_id"]}, now=current)
        return {"deal_id": deal_id, "status": "reserved", "currency": listing["currency"],
                "price_raw": int(listing["price_raw"]), "duplicate": False}


async def create_deal_payment_intent_via_route(*, deal_id: str, route: Any,
                                           actor_user_id: str,
                                           engine: Engine | None = None) -> Any:
    """Create a payment intent for a reserved deal without touching the Ledger."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    with db.connect() as conn:
        deal = conn.execute(select(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        )).mappings().first()
    if deal is None:
        raise ValueError("Unknown marketplace deal")
    if str(deal["buyer_user_id"]) != str(actor_user_id):
        raise PermissionError("Only the deal buyer can create its payment intent")
    if (deal["network"] != "ton-testnet" or deal["currency"] != "tUSDT"
            or deal["status"] not in {"reserved", "payment_confirmed"}):
        raise ValueError("Deal is not eligible for the TON testnet tUSDT route")
    if not getattr(route, "enabled", False) or route.route_id != "ton_usdt_testnet":
        raise PermissionError("The selected payment route is not enabled for testnet deals")
    if canonical_ton_address(deal["escrow_contract_address"] or "") != canonical_ton_address(route.escrow_address):
        raise PermissionError("Payment route escrow does not match the deal's escrow contract")
    intent = await route.create_intent(
        deal_id=deal_id, amount=int(deal["price_raw"]), currency="tUSDT",
        payer_address=str(deal["buyer_wallet_address"]),
    )
    current = int(time.time())
    with db.begin() as conn:
        query = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if db.dialect.name == "postgresql":
            query = query.with_for_update()
        current_deal = conn.execute(query).mappings().first()
        if current_deal is None:
            raise ValueError("Unknown marketplace deal")
        if current_deal["payment_reference"] not in (None, intent.reference):
            raise ValueError("Deal already has a different payment reference")
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        ).values(
            payment_invoice_id=intent.reference,
            payment_reference=intent.reference,
            payment_network="testnet",
            updated_at=current,
        ))
        _event(conn, deal_id=deal_id, event_type="testnet_payment_intent_created",
               actor_type="system", data={"reference": intent.reference, "network": "testnet"}, now=current)
    return intent


async def confirm_deal_payment_via_route(*, deal_id: str, intent: Any, route: Any,
                                         actor_user_id: str,
                                         engine: Engine | None = None,
                                         now: int | None = None) -> dict[str, Any]:
    """Confirm a direct escrow payment from independent chain evidence, never via Ledger."""
    if not getattr(route, "enabled", False) or route.route_id != "ton_usdt_testnet":
        raise PermissionError("The selected payment route is not enabled for testnet deals")
    if intent.deal_id != deal_id or intent.network != "testnet" or intent.currency != "tUSDT":
        raise ValueError("Payment intent does not match the TON testnet deal")
    if not getattr(route, "enabled", False) or route.route_id != "ton_usdt_testnet":
        raise PermissionError("The selected payment route is not enabled for testnet deals")
    evidence = await route.verify_payment(intent)
    if evidence is None:
        return {"confirmed": False, "status": "reserved", "reason": "payment_not_finalized"}
    if (evidence.deal_id != deal_id or evidence.reference != intent.reference
            or evidence.amount != intent.amount or evidence.currency != "tUSDT"
            or evidence.network != "testnet" or not evidence.finalized
            or not evidence.transaction_id or not evidence.query_id
            or int(evidence.query_id) <= 0):
        raise ValueError("Payment route returned mismatched or incomplete evidence")
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        query = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            query = query.with_for_update()
        deal = conn.execute(query).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if (deal["network"] != "ton-testnet" or deal["currency"] != "tUSDT"
                or canonical_ton_address(deal["escrow_contract_address"] or "")
                != canonical_ton_address(route.escrow_address)):
            raise PermissionError("Deal network, currency or escrow does not match the payment route")
        if deal["payment_reference"] != intent.reference:
            raise ValueError("Payment intent reference does not match the stored deal reference")
        if str(actor_user_id) in {str(deal["buyer_user_id"]), str(deal["seller_user_id"])}:
            raise PermissionError("Deal participants cannot independently confirm their own payment")
        if deal["status"] == "payment_confirmed":
            if (deal["payment_invoice_id"] == intent.reference
                    and deal["payment_event_id"] == evidence.transaction_id
                    and int(deal["payment_query_id"] or 0) == int(evidence.query_id)):
                return {"confirmed": True, "duplicate": True, "status": "payment_confirmed"}
            raise ValueError("Deal already has a different payment confirmation")
        if (deal["status"] != "reserved" or deal["network"] != "ton-testnet"
                or deal["currency"] != "tUSDT"):
            raise ValueError("Deal is not accepting TON testnet tUSDT payment")
        if int(deal["price_raw"]) != int(evidence.amount):
            raise ValueError("Payment amount does not match the reserved deal")
        reservation = conn.execute(select(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == deal["reservation_id"]
        )).mappings().first()
        if (reservation is None or reservation["status"] != "active"
                or int(reservation["expires_at"]) <= current):
            raise PermissionError("Marketplace reservation has expired or been cancelled")
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "reserved",
        ).values(
            status="payment_confirmed",
            payment_invoice_id=intent.reference,
            payment_event_id=evidence.transaction_id,
            payment_query_id=int(evidence.query_id),
            payment_reference=evidence.reference,
            payment_network="testnet",
            updated_at=current,
        ))
        _event(
            conn, deal_id=deal_id, event_type="payment_route_confirmed",
            actor_type="admin", actor_id=str(actor_user_id),
            data={
                "route_id": route.route_id, "network": "testnet",
                "currency": "tUSDT", "transaction_id": evidence.transaction_id,
                "reference": intent.reference, "amount_raw": int(evidence.amount),
                "query_id": int(evidence.query_id),
            }, now=current,
        )
    return {
        "confirmed": True, "duplicate": False, "status": "payment_confirmed",
        "route_id": route.route_id, "network": "testnet",
        "transaction_id": evidence.transaction_id,
        "payment_query_id": int(evidence.query_id),
    }


def reserve_verified_deal_payment(*, deal_id: str, invoice_id: str,
                                  actor_user_id: str | None = None,
                                  engine: Engine | None = None,
                                  now: int | None = None) -> dict[str, Any]:
    """Move an already-confirmed invoice credit into an immutable deal hold atomically."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        q = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            q = q.with_for_update()
        deal = conn.execute(q).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if actor_user_id is not None and str(actor_user_id) != str(deal["buyer_user_id"]):
            raise PermissionError("Only the authenticated buyer can fund this marketplace deal")
        if deal["status"] == "payment_confirmed":
            if deal["payment_invoice_id"] != invoice_id:
                raise ValueError("Deal already has a different payment invoice")
            return {"reserved": False, "duplicate": True, "status": deal["status"]}
        if deal["status"] != "reserved":
            raise ValueError("Deal is not accepting payment")
        reservation_query = select(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == deal["reservation_id"]
        )
        if lock:
            reservation_query = reservation_query.with_for_update()
        reservation = conn.execute(reservation_query).mappings().first()
        if (reservation is None or reservation["status"] != "active"
                or int(reservation["expires_at"]) <= current):
            raise PermissionError("Marketplace reservation has expired or been cancelled")
        if deal["currency"] == "XTR":
            invoice = conn.execute(select(stars_invoices).where(
                stars_invoices.c.invoice_id == invoice_id
            )).mappings().first()
            if invoice is None or invoice["status"] != "paid":
                raise PermissionError("Only a paid Telegram Stars invoice can fund an XTR deal")
            if (invoice["buyer_user_id"] != deal["buyer_user_id"]
                    or invoice["seller_user_id"] != deal["seller_user_id"]
                    or int(invoice["amount_stars"]) != int(deal["price_raw"])
                    or invoice["currency"] != "XTR"):
                raise ValueError("Stars invoice does not match deal participants and amount")
            payment = conn.execute(select(stars_payments).where(
                stars_payments.c.invoice_id == invoice_id
            )).mappings().first()
            if payment is None:
                raise PermissionError("Stars invoice has no successful payment receipt")
            source_hold = "liability:deal_hold:" + str(invoice["deal_id"]) + ":XTR"
            if lock:
                conn.execute(select(accounts.c.account_id).where(
                    accounts.c.account_id == source_hold,
                    accounts.c.currency == "XTR",
                ).with_for_update()).first()
            available = -_account_balance_in_connection(conn, source_hold, "XTR")
            if available < int(deal["price_raw"]):
                raise RuntimeError("Stars proceeds are not held for the original deal")
            journal = _post(conn,
                idempotency_key="marketplace-deal-stars-reserve:" + deal_id,
                source_type="marketplace_deal_stars_reserve", source_reference=deal_id,
                postings=[
                    {"account_id": source_hold, "account_type": "liability",
                     "currency": "XTR", "amount_raw": int(deal["price_raw"])},
                    {"account_id": "liability:deal_hold:" + deal_id + ":XTR",
                     "account_type": "liability", "currency": "XTR",
                     "amount_raw": -int(deal["price_raw"])},
                ], now=current)
            conn.execute(update(marketplace_deals).where(
                marketplace_deals.c.deal_id == deal_id
            ).values(status="payment_confirmed", payment_invoice_id=invoice_id,
                     payment_event_id=payment["payment_event_id"], updated_at=current))
            _event(conn, deal_id=deal_id, event_type="payment_confirmed",
                   data={"invoice_id": invoice_id, "payment_event_id": payment["payment_event_id"],
                         "journal_id": journal["entry_id"]}, now=current)
            return {"reserved": True, "duplicate": False, "status": "payment_confirmed",
                    "journal_id": journal["entry_id"]}
        invoice = conn.execute(select(invoices).where(
            invoices.c.invoice_id == invoice_id
        )).mappings().first()
        if invoice is None or invoice["status"] != "paid":
            raise PermissionError("Only a finalized, paid invoice can fund a deal")
        if (invoice["user_id"] != deal["buyer_user_id"]
                or invoice["asset_id"] != deal["currency"]
                or int(invoice["amount_raw"]) != int(deal["price_raw"])):
            raise ValueError("Paid invoice does not match deal buyer, asset and exact amount")
        payment = conn.execute(select(payment_events).where(
            payment_events.c.invoice_id == invoice_id
        )).mappings().first()
        if payment is None:
            raise PermissionError("Invoice has no unique settled chain payment event")
        if (payment["asset_id"] != deal["currency"]
                or int(payment["amount_raw"]) != int(deal["price_raw"])):
            raise ValueError("Payment event does not match the agreed deal amount")
        user_account = f"liability:user:{deal['buyer_user_id']}:{deal['currency']}"
        if lock:
            conn.execute(select(accounts.c.account_id).where(
                accounts.c.account_id == user_account,
                accounts.c.currency == deal["currency"],
            ).with_for_update()).first()
        available = -_account_balance_in_connection(conn, user_account, deal["currency"])
        if available < int(deal["price_raw"]):
            raise RuntimeError("Buyer ledger balance is not backed by the paid invoice")
        journal = _post(conn,
            idempotency_key="marketplace-deal-reserve:" + deal_id,
            source_type="marketplace_deal_reserve", source_reference=deal_id,
            postings=[
                {"account_id": user_account, "account_type": "liability",
                 "currency": deal["currency"], "amount_raw": int(deal["price_raw"])},
                {"account_id": f"liability:deal_hold:{deal_id}:{deal['currency']}",
                 "account_type": "liability", "currency": deal["currency"],
                 "amount_raw": -int(deal["price_raw"])},
            ], now=current)
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        ).values(status="payment_confirmed", payment_invoice_id=invoice_id,
                 payment_event_id=payment["payment_event_id"], updated_at=current))
        _event(conn, deal_id=deal_id, event_type="payment_confirmed",
               data={"invoice_id": invoice_id, "payment_event_id": payment["payment_event_id"],
                     "journal_id": journal["entry_id"]}, now=current)
        return {"reserved": True, "duplicate": False, "status": "payment_confirmed",
                "journal_id": journal["entry_id"]}


def mark_deal_nft_transfer_pending(*, deal_id: str, engine: Engine | None = None,
                                   now: int | None = None) -> None:
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    with db.begin() as conn:
        changed = conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "payment_confirmed",
        ).values(status="nft_transfer_pending", updated_at=current))
        if changed.rowcount != 1:
            raise ValueError("Only a paid deal can enter NFT transfer")
        _event(conn, deal_id=deal_id, event_type="nft_transfer_pending", now=current)


def confirm_deal_nft_transfer(*, deal_id: str, transfer_proofs: list[dict[str, Any]],
                              current_owner_addresses: list[str],
                              engine: Engine | None = None,
                              now: int | None = None) -> dict[str, Any]:
    """Pay seller only after two independent providers agree NFT reached buyer."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        q = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            q = q.with_for_update()
        deal = conn.execute(q).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if deal["status"] == "settled":
            return {"settled": False, "duplicate": True}
        if deal["status"] != "nft_transfer_pending":
            raise ValueError("Deal is not awaiting NFT transfer confirmation")
        if len(transfer_proofs) < 2:
            raise PermissionError("Two independent NFT transfer proofs are required")
        normalized = []
        for proof in transfer_proofs:
            if proof.get("provider_id") not in {"tonapi", "toncenter"}:
                raise PermissionError("Untrusted NFT transfer provider")
            if proof.get("successful") is not True or proof.get("finalized") is not True:
                raise ValueError("NFT transfer is not successful and finalized")
            normalized.append({
                "provider_id": proof["provider_id"],
                "tx_hash": str(proof.get("tx_hash") or "").lower().removeprefix("0x"),
                "item_address": canonical_ton_address(proof.get("item_address") or ""),
                "old_owner": canonical_ton_address(proof.get("old_owner") or ""),
                "new_owner": canonical_ton_address(proof.get("new_owner") or ""),
                "evidence_hash": str(proof.get("evidence_hash") or ""),
            })
        if len({item["provider_id"] for item in normalized}) != 2:
            raise PermissionError("Two distinct providers are required")
        if (len({item["tx_hash"] for item in normalized}) != 1
                or len(normalized[0]["tx_hash"]) != 64
                or any(ch not in "0123456789abcdef" for ch in normalized[0]["tx_hash"])):
            raise ValueError("Providers disagree on NFT transfer transaction")
        if len({item["item_address"] for item in normalized}) != 1:
            raise ValueError("Providers disagree on NFT item")
        if len({item["old_owner"] for item in normalized}) != 1 or len({item["new_owner"] for item in normalized}) != 1:
            raise ValueError("Providers disagree on NFT transfer parties")
        if len({item["evidence_hash"] for item in normalized}) != 1 or not normalized[0]["evidence_hash"]:
            raise ValueError("Providers disagree on NFT transfer evidence")
        expected_buyer = deal["buyer_wallet_address"]
        asset = conn.execute(select(marketplace_assets).where(
            marketplace_assets.c.asset_id == deal["asset_id"]
        )).mappings().one()
        if asset["custody_state"] != "escrowed" or int(asset["ownership_expires_at"] or 0) <= current:
            raise PermissionError("NFT is no longer verified in marketplace escrow")
        escrow_fallback = os.getenv(
            "TON_TESTNET_ESCROW_ADDRESS" if deal["network"] == "ton-testnet" else "GIFT_RADAR_ESCROW_ADDRESS",
            "",
        )
        escrow = canonical_ton_address(deal["escrow_contract_address"] or escrow_fallback)
        if not escrow or normalized[0]["old_owner"] != escrow:
            raise PermissionError("NFT transfer did not originate from this deal's escrow contract")
        if asset["escrow_contract_address"] and canonical_ton_address(asset["escrow_contract_address"]) != escrow:
            raise PermissionError("NFT asset escrow address does not match the deal snapshot")
        if normalized[0]["new_owner"] != expected_buyer:
            raise PermissionError("NFT was not delivered to the buyer's verified wallet")
        from marketplace_inventory import marketplace_ownership_proofs
        proof_row = conn.execute(select(
            marketplace_ownership_proofs.c.evidence_reference,
            marketplace_ownership_proofs.c.owner_user_id,
            marketplace_ownership_proofs.c.proof_source,
            marketplace_ownership_proofs.c.expires_at,
        ).where(
            marketplace_ownership_proofs.c.asset_id == deal["asset_id"],
            marketplace_ownership_proofs.c.owner_user_id == deal["seller_user_id"],
            marketplace_ownership_proofs.c.proof_source == "ton_dual_provider",
            marketplace_ownership_proofs.c.verification_status == "verified",
        ).order_by(
            marketplace_ownership_proofs.c.recorded_at.desc()
        ).limit(1)).mappings().first()
        if (proof_row is None or int(proof_row["expires_at"] or 0) <= current
                or normalized[0]["item_address"] != canonical_ton_address(proof_row["evidence_reference"])):
            raise ValueError("NFT item does not match a fresh seller ownership proof for the reserved listing")
        owner_addresses = {canonical_ton_address(value) for value in current_owner_addresses}
        if owner_addresses != {expected_buyer}:
            raise PermissionError("Live NFT state does not show the buyer as current owner")

        if deal["network"] == "ton-testnet" and deal["currency"] == "tUSDT":
            if not deal["seller_wallet_address"]:
                raise ValueError("Immutable seller payout address snapshot is missing")
            payout_query_id = int.from_bytes(
                hashlib.sha256(f"{deal_id}:payout".encode()).digest()[:8], "big"
            ) or 1
            conn.execute(update(marketplace_deals).where(
                marketplace_deals.c.deal_id == deal_id,
                marketplace_deals.c.status == "nft_transfer_pending",
            ).values(
                status="payout_pending",
                nft_transfer_tx_hash=normalized[0]["tx_hash"],
                payout_query_id=payout_query_id,
                updated_at=current,
            ))
            conn.execute(update(marketplace_reservations).where(
                marketplace_reservations.c.reservation_id == deal["reservation_id"]
            ).values(status="consumed"))
            conn.execute(update(marketplace_listings).where(
                marketplace_listings.c.listing_id == deal["listing_id"],
                marketplace_listings.c.status == "reserved",
            ).values(status="payout_pending", updated_at=current))
            conn.execute(update(marketplace_assets).where(
                marketplace_assets.c.asset_id == deal["asset_id"]
            ).values(
                owner_user_id=deal["buyer_user_id"], owner_wallet_address=expected_buyer,
                custody_state="transferred", ownership_state="verified",
                ownership_checked_at=current, ownership_expires_at=current + 300,
                updated_at=current,
            ))
            conn.execute(insert(marketplace_listing_events).values(
                event_id=uuid.uuid4().hex, listing_id=deal["listing_id"],
                actor_user_id=deal["buyer_user_id"], event_type="payout_pending",
                event_data=json.dumps({
                    "deal_id": deal_id, "tx_hash": normalized[0]["tx_hash"],
                    "payout_query_id": payout_query_id,
                }),
                created_at=current,
            ))
            _event(conn, deal_id=deal_id, event_type="nft_transfer_confirmed_payout_pending",
                   data={"tx_hash": normalized[0]["tx_hash"], "payout_query_id": payout_query_id},
                   now=current)
            return {
                "settled": False, "duplicate": False, "status": "payout_pending",
                "payout_query_id": payout_query_id, "tx_hash": normalized[0]["tx_hash"],
            }

        total = int(deal["price_raw"])
        journal = _post(conn,
            idempotency_key="marketplace-deal-payout:" + deal_id,
            source_type="marketplace_deal_payout", source_reference=deal_id,
            postings=[
                {"account_id": f"liability:deal_hold:{deal_id}:{deal['currency']}",
                 "account_type": "liability", "currency": deal["currency"], "amount_raw": total},
                {"account_id": f"liability:seller:{deal['seller_user_id']}:{deal['currency']}",
                 "account_type": "liability", "currency": deal["currency"], "amount_raw": -total},
            ], now=current)
        if deal["currency"] == "XTR":
            stars_invoice = conn.execute(select(stars_invoices).where(
                stars_invoices.c.invoice_id == deal["payment_invoice_id"]
            )).mappings().first()
            if (stars_invoice is None or stars_invoice["status"] != "paid"
                    or stars_invoice["buyer_user_id"] != deal["buyer_user_id"]
                    or stars_invoice["seller_user_id"] != deal["seller_user_id"]
                    or int(stars_invoice["amount_stars"]) != total):
                raise PermissionError("Stars invoice participants and amount no longer match the delivered deal")
            if str(stars_invoice["deal_id"]).startswith("grd_"):
                if str(stars_invoice["deal_id"]) != str(deal_id):
                    raise PermissionError("Native Stars invoice is bound to a different marketplace deal")
            else:
                completed = conn.execute(__import__("sqlalchemy").text(
                    "UPDATE demo_deals SET status='completed' "
                    "WHERE id=:deal_id AND status IN ('paid','delivery_pending')"
                ), {"deal_id": stars_invoice["deal_id"]})
                if completed.rowcount != 1:
                    raise ValueError("Original Telegram Stars deal is not in a fulfillable state")
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        ).values(status="settled", nft_transfer_tx_hash=normalized[0]["tx_hash"],
                 updated_at=current))
        conn.execute(update(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == deal["reservation_id"]
        ).values(status="consumed"))
        conn.execute(update(marketplace_listings).where(
            marketplace_listings.c.listing_id == deal["listing_id"],
            marketplace_listings.c.status == "reserved",
        ).values(status="sold", updated_at=current))
        conn.execute(update(marketplace_assets).where(
            marketplace_assets.c.asset_id == deal["asset_id"]
        ).values(owner_user_id=deal["buyer_user_id"], owner_wallet_address=expected_buyer,
                 custody_state="transferred", ownership_state="verified",
                 ownership_checked_at=current, ownership_expires_at=current + 300,
                 updated_at=current))
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=deal["listing_id"],
            actor_user_id=deal["buyer_user_id"], event_type="sold",
            event_data=json.dumps({"deal_id": deal_id, "tx_hash": normalized[0]["tx_hash"]}),
            created_at=current,
        ))
        _event(conn, deal_id=deal_id, event_type="deal_settled",
               data={"tx_hash": normalized[0]["tx_hash"], "journal_id": journal["entry_id"]},
               now=current)
        return {"settled": True, "duplicate": False, "journal_id": journal["entry_id"],
                "tx_hash": normalized[0]["tx_hash"]}




async def _build_testnet_deal_intent(deal: Any, route: Any) -> Any:
    if (deal["network"] != "ton-testnet" or deal["currency"] != "tUSDT"
            or not getattr(route, "enabled", False) or route.route_id != "ton_usdt_testnet"):
        raise PermissionError("Deal is not eligible for the enabled TON testnet tUSDT route")
    if canonical_ton_address(deal["escrow_contract_address"] or "") != canonical_ton_address(route.escrow_address):
        raise PermissionError("Payment route escrow does not match the deal escrow snapshot")
    return await route.create_intent(
        deal_id=str(deal["deal_id"]), amount=int(deal["price_raw"]), currency="tUSDT",
        payer_address=str(deal["buyer_wallet_address"]),
    )


def _validate_dual_owner_proofs(proofs: list[dict[str, Any]], expected_owner: str) -> None:
    if len(proofs) < 2:
        raise PermissionError("Two independent live NFT owner proofs are required")
    provider_ids = set()
    owners = set()
    for proof in proofs:
        provider_id = str(proof.get("provider_id") or "")
        if provider_id not in {"tonapi", "toncenter"} or provider_id in provider_ids:
            raise PermissionError("Two distinct trusted NFT providers are required")
        if proof.get("successful") is not True:
            raise ValueError("NFT owner lookup did not succeed")
        provider_ids.add(provider_id)
        owners.add(canonical_ton_address(proof.get("owner_address") or ""))
    if len(provider_ids) != 2 or owners != {canonical_ton_address(expected_owner)}:
        raise PermissionError("Independent NFT owner providers do not agree with the expected owner")


async def confirm_deal_testnet_payout(*, deal_id: str, route: Any,
                                      actor_user_id: str,
                                      current_owner_proofs: list[dict[str, Any]],
                                      engine: Engine | None = None,
                                      now: int | None = None) -> dict[str, Any]:
    """Settle a tUSDT deal only after two independent providers verify seller payout."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    with db.connect() as conn:
        deal = conn.execute(select(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        )).mappings().first()
    if deal is None:
        raise ValueError("Unknown marketplace deal")
    if str(actor_user_id) in {str(deal["buyer_user_id"]), str(deal["seller_user_id"])}:
        raise PermissionError("Deal participants cannot independently confirm seller payout")
    if deal["status"] == "settled" and deal["payout_tx_hash"]:
        return {"confirmed": True, "duplicate": True, "status": "settled",
                "payout_tx_hash": deal["payout_tx_hash"]}
    if deal["status"] != "payout_pending" or not deal["seller_wallet_address"]:
        raise ValueError("Deal is not awaiting a verifiable seller payout")
    _validate_dual_owner_proofs(current_owner_proofs, str(deal["buyer_wallet_address"]))
    query_id = int(deal["payout_query_id"] or 0)
    if query_id <= 0:
        raise ValueError("Payout query ID is missing")
    intent = await _build_testnet_deal_intent(deal, route)
    evidence = await route.verify_payout(
        intent, seller_address=str(deal["seller_wallet_address"]), query_id=query_id,
    )
    if evidence is None:
        return {"confirmed": False, "status": "payout_pending", "reason": "payout_not_finalized"}
    if (evidence.deal_id != deal_id or evidence.reference != f"grpayout:{deal_id}:{query_id}"
            or evidence.amount != int(deal["price_raw"])
            or evidence.currency != "tUSDT" or evidence.network != "testnet"
            or evidence.query_id != query_id or not evidence.finalized
            or not evidence.transaction_id):
        raise ValueError("Independent providers returned mismatched seller payout evidence")
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        query = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            query = query.with_for_update()
        current_deal = conn.execute(query).mappings().first()
        if current_deal is None:
            raise ValueError("Unknown marketplace deal")
        if current_deal["status"] == "settled":
            if current_deal["payout_tx_hash"] == evidence.transaction_id:
                return {"confirmed": True, "duplicate": True, "status": "settled",
                        "payout_tx_hash": evidence.transaction_id}
            raise ValueError("Deal already has a different payout confirmation")
        if (current_deal["status"] != "payout_pending"
                or int(current_deal["payout_query_id"] or 0) != query_id):
            raise ValueError("Deal payout state changed during independent verification")
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "payout_pending",
        ).values(
            status="settled", payout_tx_hash=evidence.transaction_id,
            payout_verified_at=current, updated_at=current,
        ))
        conn.execute(update(marketplace_listings).where(
            marketplace_listings.c.listing_id == current_deal["listing_id"],
            marketplace_listings.c.status == "payout_pending",
        ).values(status="sold", updated_at=current))
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=current_deal["listing_id"],
            actor_user_id=current_deal["buyer_user_id"], event_type="sold",
            event_data=json.dumps({
                "deal_id": deal_id, "payout_tx_hash": evidence.transaction_id,
            }),
            created_at=current,
        ))
        _event(conn, deal_id=deal_id, event_type="testnet_payout_verified",
               actor_type="admin", actor_id=str(actor_user_id),
               data={"payout_tx_hash": evidence.transaction_id, "query_id": query_id},
               now=current)
    return {"confirmed": True, "duplicate": False, "status": "settled",
            "payout_tx_hash": evidence.transaction_id, "query_id": query_id}



def mark_deal_testnet_dispute_release_pending(*, deal_id: str, actor_user_id: str,
                                              engine: Engine | None = None,
                                              now: int | None = None) -> dict[str, Any]:
    """Move a dismissed paid testnet dispute back to NFT delivery after resolver execution."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        query = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            query = query.with_for_update()
        deal = conn.execute(query).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if str(actor_user_id) in {str(deal["buyer_user_id"]), str(deal["seller_user_id"])}:
            raise PermissionError("Deal participants cannot mark their own dispute resolution")
        if (deal["network"] != "ton-testnet" or deal["currency"] != "tUSDT"
                or deal["status"] not in {"disputed", "nft_transfer_pending"}
                or not deal["payment_query_id"]):
            raise ValueError("Only a paid disputed testnet tUSDT deal can return to delivery")
        if deal["status"] == "nft_transfer_pending":
            return {"marked": False, "duplicate": True, "status": "nft_transfer_pending"}
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "disputed",
        ).values(status="nft_transfer_pending", updated_at=current))
        _event(conn, deal_id=deal_id, event_type="testnet_dispute_release_pending",
               actor_type="admin", actor_id=str(actor_user_id),
               data={"decision": "release_to_buyer"}, now=current)
    return {"marked": True, "duplicate": False, "status": "nft_transfer_pending"}


def mark_deal_testnet_refund_pending(*, deal_id: str, actor_user_id: str,
                                     engine: Engine | None = None,
                                     now: int | None = None) -> dict[str, Any]:
    """Record that the approved on-chain dispute resolver has initiated a paid refund."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    query_id = int.from_bytes(hashlib.sha256(f"{deal_id}:refund".encode()).digest()[:8], "big") or 1
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        query = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            query = query.with_for_update()
        deal = conn.execute(query).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if str(actor_user_id) in {str(deal["buyer_user_id"]), str(deal["seller_user_id"])}:
            raise PermissionError("Deal participants cannot mark their own refund as initiated")
        if (deal["network"] != "ton-testnet" or deal["currency"] != "tUSDT"
                or deal["status"] not in {"disputed", "refund_pending"}
                or not deal["payment_query_id"]):
            raise ValueError("Only a paid disputed testnet tUSDT deal can enter refund pending")
        if deal["status"] == "refund_pending":
            if int(deal["refund_query_id"] or 0) != query_id:
                raise ValueError("Deal already has a different refund query")
            return {"marked": False, "duplicate": True, "status": "refund_pending",
                    "refund_query_id": query_id}
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "disputed",
        ).values(status="refund_pending", refund_query_id=query_id, updated_at=current))
        _event(conn, deal_id=deal_id, event_type="testnet_refund_pending",
               actor_type="admin", actor_id=str(actor_user_id),
               data={"refund_query_id": query_id}, now=current)
    return {"marked": True, "duplicate": False, "status": "refund_pending",
            "refund_query_id": query_id}


async def confirm_deal_testnet_refund(*, deal_id: str, route: Any,
                                      actor_user_id: str,
                                      nft_return_proofs: list[dict[str, Any]],
                                      current_owner_proofs: list[dict[str, Any]],
                                      engine: Engine | None = None,
                                      now: int | None = None) -> dict[str, Any]:
    """Close a paid dispute only after two providers verify the on-chain buyer refund."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    with db.connect() as conn:
        deal = conn.execute(select(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        )).mappings().first()
    if deal is None:
        raise ValueError("Unknown marketplace deal")
    if str(actor_user_id) in {str(deal["buyer_user_id"]), str(deal["seller_user_id"])}:
        raise PermissionError("Deal participants cannot independently confirm their own refund")
    if deal["status"] == "refunded" and deal["refund_tx_hash"]:
        return {"confirmed": True, "duplicate": True, "status": "refunded",
                "refund_tx_hash": deal["refund_tx_hash"]}
    if deal["status"] != "refund_pending" or not deal["seller_wallet_address"]:
        raise ValueError("Deal is not awaiting a verifiable buyer refund")
    query_id = int(deal["refund_query_id"] or 0)
    if query_id <= 0:
        raise ValueError("Refund query ID is missing")
    if len(nft_return_proofs) < 2:
        raise PermissionError("Two independent NFT return proofs are required")
    normalized = []
    for proof in nft_return_proofs:
        if proof.get("provider_id") not in {"tonapi", "toncenter"}:
            raise PermissionError("Untrusted NFT return provider")
        if proof.get("successful") is not True or proof.get("finalized") is not True:
            raise ValueError("NFT return is not successful and finalized")
        normalized.append({
            "provider_id": proof["provider_id"],
            "tx_hash": str(proof.get("tx_hash") or "").lower().removeprefix("0x"),
            "item_address": canonical_ton_address(proof.get("item_address") or ""),
            "old_owner": canonical_ton_address(proof.get("old_owner") or ""),
            "new_owner": canonical_ton_address(proof.get("new_owner") or ""),
            "evidence_hash": str(proof.get("evidence_hash") or ""),
        })
    if len({item["provider_id"] for item in normalized}) != 2:
        raise PermissionError("Two distinct providers are required for NFT return")
    if (len({item["tx_hash"] for item in normalized}) != 1
            or len(normalized[0]["tx_hash"]) != 64
            or any(ch not in "0123456789abcdef" for ch in normalized[0]["tx_hash"])):
        raise ValueError("Providers disagree on NFT return transaction")
    if (len({item["item_address"] for item in normalized}) != 1
            or len({item["old_owner"] for item in normalized}) != 1
            or len({item["new_owner"] for item in normalized}) != 1
            or len({item["evidence_hash"] for item in normalized}) != 1
            or not normalized[0]["evidence_hash"]):
        raise ValueError("Providers disagree on NFT return evidence")
    escrow = canonical_ton_address(deal["escrow_contract_address"] or "")
    seller_wallet = canonical_ton_address(deal["seller_wallet_address"])
    if normalized[0]["old_owner"] != escrow or normalized[0]["new_owner"] != seller_wallet:
        raise PermissionError("NFT return did not move from this escrow to the snapshotted seller")
    _validate_dual_owner_proofs(current_owner_proofs, seller_wallet)
    intent = await _build_testnet_deal_intent(deal, route)
    evidence = await route.verify_refund(
        intent, buyer_address=str(deal["buyer_wallet_address"]), query_id=query_id,
    )
    if evidence is None:
        return {"confirmed": False, "status": "refund_pending", "reason": "refund_not_finalized"}
    if (evidence.deal_id != deal_id or evidence.reference != f"grrefund:{deal_id}:{query_id}"
            or evidence.amount != int(deal["price_raw"])
            or evidence.currency != "tUSDT" or evidence.network != "testnet"
            or evidence.query_id != query_id or not evidence.finalized
            or not evidence.transaction_id):
        raise ValueError("Independent providers returned mismatched buyer refund evidence")
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        query = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            query = query.with_for_update()
        current_deal = conn.execute(query).mappings().first()
        if current_deal is None:
            raise ValueError("Unknown marketplace deal")
        asset = conn.execute(select(marketplace_assets).where(
            marketplace_assets.c.asset_id == current_deal["asset_id"]
        )).mappings().one()
        if (asset["escrow_contract_address"]
                and canonical_ton_address(asset["escrow_contract_address"]) != escrow):
            raise PermissionError("Asset escrow address does not match the deal refund escrow")
        if (not asset["item_address"]
                or normalized[0]["item_address"] != canonical_ton_address(asset["item_address"])):
            raise ValueError("NFT return item does not match the immutable asset item address")
        if current_deal["status"] == "refunded":
            if current_deal["refund_tx_hash"] == evidence.transaction_id:
                return {"confirmed": True, "duplicate": True, "status": "refunded",
                        "refund_tx_hash": evidence.transaction_id}
            raise ValueError("Deal already has a different refund confirmation")
        if (current_deal["status"] != "refund_pending"
                or int(current_deal["refund_query_id"] or 0) != query_id):
            raise ValueError("Deal refund state changed during independent verification")
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "refund_pending",
        ).values(
            status="refunded", refund_tx_hash=evidence.transaction_id,
            nft_return_tx_hash=normalized[0]["tx_hash"],
            refund_verified_at=current, updated_at=current,
        ))
        conn.execute(update(marketplace_assets).where(
            marketplace_assets.c.asset_id == current_deal["asset_id"]
        ).values(
            owner_user_id=current_deal["seller_user_id"],
            owner_wallet_address=current_deal["seller_wallet_address"],
            custody_state="wallet_owned", ownership_state="verified",
            ownership_checked_at=current, ownership_expires_at=current + 300,
            escrow_contract_address=None, updated_at=current,
        ))
        conn.execute(update(marketplace_listings).where(
            marketplace_listings.c.listing_id == current_deal["listing_id"]
        ).values(status="cancelled", updated_at=current))
        conn.execute(update(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == current_deal["reservation_id"]
        ).values(status="cancelled"))
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=current_deal["listing_id"],
            actor_user_id=current_deal["seller_user_id"], event_type="cancelled",
            event_data=json.dumps({
                "deal_id": deal_id, "refund_tx_hash": evidence.transaction_id,
            }),
            created_at=current,
        ))
        _event(conn, deal_id=deal_id, event_type="testnet_refund_verified",
               actor_type="admin", actor_id=str(actor_user_id),
               data={"refund_tx_hash": evidence.transaction_id, "query_id": query_id},
               now=current)
    return {"confirmed": True, "duplicate": False, "status": "refunded",
            "refund_tx_hash": evidence.transaction_id, "query_id": query_id}


def open_deal_dispute(*, deal_id: str, actor_user_id: str, reason: str,
                      engine: Engine | None = None,
                      now: int | None = None) -> dict[str, Any]:
    """Open one buyer/seller dispute; ambiguous in-flight transfers go to reconciliation."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    uid = str(actor_user_id)
    clean = " ".join(str(reason or "").split())
    if not uid.isdigit() or int(uid) <= 0 or not 8 <= len(clean) <= 500:
        raise ValueError("A valid user and dispute reason of 8-500 characters are required")
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        query = select(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        )
        if lock:
            query = query.with_for_update()
        deal = conn.execute(query).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if uid == str(deal["buyer_user_id"]):
            actor_type = "buyer"
        elif uid == str(deal["seller_user_id"]):
            actor_type = "seller"
        else:
            raise PermissionError("Only the buyer or seller can dispute this deal")
        previous = conn.execute(select(marketplace_deal_events.c.event_id).where(
            marketplace_deal_events.c.deal_id == deal_id,
            marketplace_deal_events.c.event_type.in_(
                ("dispute_opened", "dispute_requires_reconciliation")
            ),
        ).limit(1)).first()
        if previous:
            return {"opened": False, "duplicate": True, "status": deal["status"]}
        if deal["status"] == "payment_confirmed":
            next_status = "disputed"
            event_type = "dispute_opened"
        elif deal["status"] in {"nft_transfer_pending", "payout_pending"}:
            next_status = "reconciliation_required"
            event_type = "dispute_requires_reconciliation"
        else:
            raise PermissionError("This deal is not in a disputable state")
        changed = conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == deal["status"],
        ).values(status=next_status, updated_at=current))
        if changed.rowcount != 1:
            raise RuntimeError("Deal changed while opening dispute; retry")
        _event(
            conn, deal_id=deal_id, event_type=event_type,
            actor_type=actor_type, actor_id=uid,
            data={"reason": clean, "previous_status": deal["status"],
                  "next_status": next_status}, now=current,
        )
        return {"opened": True, "duplicate": False, "status": next_status}



def resolve_deal_dispute(*, deal_id: str, decision: str, reason: str,
                         admin_user_id: str, engine: Engine | None = None,
                         now: int | None = None) -> dict[str, Any]:
    """Record an admin dispute decision; never resolve an in-flight chain transfer automatically."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    admin = str(admin_user_id)
    clean = " ".join(str(reason or "").split())
    if not admin.isdigit() or int(admin) <= 0 or not 8 <= len(clean) <= 500:
        raise ValueError("A valid admin and resolution reason of 8-500 characters are required")
    if decision not in {"refund", "dismiss"}:
        raise ValueError("Unsupported dispute decision")
    with db.connect() as conn:
        network_currency = conn.execute(select(
            marketplace_deals.c.network, marketplace_deals.c.currency
        ).where(marketplace_deals.c.deal_id == deal_id)).first()
    if network_currency and network_currency.network == "ton-testnet" and network_currency.currency == "tUSDT":
        raise PermissionError("Testnet tUSDT disputes must be resolved by the on-chain 2-of-3 escrow resolver")
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    if decision == "refund":
        with db.connect() as conn:
            query = select(marketplace_deals.c.status).where(
                marketplace_deals.c.deal_id == deal_id
            )
            if lock:
                query = query.with_for_update()
            status = conn.execute(query).scalar_one_or_none()
        if status != "disputed":
            raise PermissionError("Only a pre-transfer disputed deal can be refunded by this workflow")
        result = refund_unfulfilled_deal(
            deal_id=deal_id, reason=clean, admin_user_id=admin,
            engine=db, now=current, require_disputed=True,
        )
        if result.get("telegram_refund_required"):
            return {**result, "decision": "refund", "manual_telegram_refund": True}
        return {**result, "decision": "refund"}
    with db.begin() as conn:
        query = select(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        )
        if lock:
            query = query.with_for_update()
        deal = conn.execute(query).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        prior = conn.execute(select(marketplace_deal_events.c.event_id).where(
            marketplace_deal_events.c.deal_id == deal_id,
            marketplace_deal_events.c.event_type == "dispute_resolved",
        ).limit(1)).first()
        if prior:
            return {"resolved": False, "duplicate": True, "status": deal["status"]}
        if deal["status"] != "disputed":
            raise PermissionError("Only a pre-transfer disputed deal can be dismissed")
        changed = conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status == "disputed",
        ).values(status="payment_confirmed", updated_at=current))
        if changed.rowcount != 1:
            raise RuntimeError("Deal changed while resolving dispute; retry")
        _event(
            conn, deal_id=deal_id, event_type="dispute_resolved",
            actor_type="admin", actor_id=admin,
            data={"decision": "dismiss", "reason": clean,
                  "previous_status": "disputed", "next_status": "payment_confirmed"},
            now=current,
        )
        return {"resolved": True, "duplicate": False, "status": "payment_confirmed",
                "decision": "dismiss"}


def refund_unfulfilled_deal(*, deal_id: str, reason: str, admin_user_id: str,
                            engine: Engine | None = None,
                            now: int | None = None,
                            require_disputed: bool = False) -> dict[str, Any]:
    """Refund an unfulfilled deal from its hold; never auto-refund after NFT broadcast."""
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    with db.begin() as conn:
        q = select(marketplace_deals).where(marketplace_deals.c.deal_id == deal_id)
        if lock:
            q = q.with_for_update()
        deal = conn.execute(q).mappings().first()
        if deal is None:
            raise ValueError("Unknown marketplace deal")
        if require_disputed and deal["status"] != "disputed":
            raise PermissionError("Dispute refund requires the deal to remain disputed")
        if deal["status"] == "refunded":
            return {"refunded": False, "duplicate": True}
        if deal["status"] == "reconciliation_required":
            raise PermissionError("Deal requires manual chain reconciliation; automatic refund is blocked")
        if deal["network"] == "ton-testnet" or deal["currency"] == "tUSDT":
            raise PermissionError("Testnet tUSDT refunds must be verified from the on-chain escrow contract")
        if deal["currency"] == "XTR":
            if deal["status"] not in {"payment_confirmed", "disputed"}:
                raise PermissionError("A Stars deal can only be refunded before NFT transfer starts")
            invoice = conn.execute(select(stars_invoices).where(
                stars_invoices.c.invoice_id == deal["payment_invoice_id"]
            )).mappings().first()
            if invoice is None or invoice["status"] != "paid":
                raise ValueError("Paid Telegram Stars invoice is required for this refund")
            source_hold = "liability:deal_hold:" + deal_id + ":XTR"
            original_hold = "liability:deal_hold:" + str(invoice["deal_id"]) + ":XTR"
            source_available = -_account_balance_in_connection(conn, source_hold, "XTR")
            original_available = -_account_balance_in_connection(conn, original_hold, "XTR")
            if source_available == 0 and original_available == int(deal["price_raw"]):
                return {"refunded": False, "duplicate": True, "telegram_refund_required": True}
            if source_available != int(deal["price_raw"]):
                raise PermissionError("Stars deal hold is not fully available for refund restoration")
            _post(conn,
                idempotency_key="marketplace-deal-stars-refund-restore:" + deal_id,
                source_type="marketplace_deal_stars_refund_restore", source_reference=deal_id,
                postings=[
                    {"account_id": source_hold, "account_type": "liability",
                     "currency": "XTR", "amount_raw": int(deal["price_raw"])},
                    {"account_id": original_hold, "account_type": "liability",
                     "currency": "XTR", "amount_raw": -int(deal["price_raw"])},
                ], now=current)
            _event(conn, deal_id=deal_id, event_type="stars_refund_hold_restored",
                   actor_type="admin", actor_id=admin_user_id,
                   data={"reason": " ".join(reason.split())[:500]}, now=current)
            return {"refunded": False, "duplicate": False, "telegram_refund_required": True}
        if deal["status"] not in {"payment_confirmed", "nft_transfer_pending", "disputed"}:
            raise ValueError("Deal is not eligible for refund")
        if deal["status"] == "nft_transfer_pending":
            raise PermissionError("NFT transfer may have been broadcast; manual chain reconciliation is required")
        amount = int(deal["price_raw"])
        journal = _post(conn,
            idempotency_key="marketplace-deal-refund:" + deal_id,
            source_type="marketplace_deal_refund", source_reference=deal_id,
            postings=[
                {"account_id": f"liability:deal_hold:{deal_id}:{deal['currency']}",
                 "account_type": "liability", "currency": deal["currency"], "amount_raw": amount},
                {"account_id": f"liability:user:{deal['buyer_user_id']}:{deal['currency']}",
                 "account_type": "liability", "currency": deal["currency"], "amount_raw": -amount},
            ], now=current)
        conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id
        ).values(status="refunded", updated_at=current))
        conn.execute(update(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == deal["reservation_id"]
        ).values(status="cancelled"))
        asset_query = select(marketplace_assets).where(
            marketplace_assets.c.asset_id == deal["asset_id"]
        )
        listing_query = select(marketplace_listings).where(
            marketplace_listings.c.listing_id == deal["listing_id"]
        )
        if lock:
            asset_query = asset_query.with_for_update()
            listing_query = listing_query.with_for_update()
        asset = conn.execute(asset_query).mappings().first()
        listing = conn.execute(listing_query).mappings().first()
        reusable = bool(
            asset and asset["custody_state"] == "escrowed"
            and int(asset["ownership_expires_at"] or 0) > current
        )
        next_listing_status = "active" if reusable else "pending_verification"
        if listing and listing["status"] == "reserved":
            conn.execute(update(marketplace_listings).where(
                marketplace_listings.c.listing_id == deal["listing_id"],
                marketplace_listings.c.status == "reserved",
            ).values(status=next_listing_status, updated_at=current))
            conn.execute(insert(marketplace_listing_events).values(
                event_id=uuid.uuid4().hex, listing_id=deal["listing_id"],
                actor_user_id=str(admin_user_id), event_type="deal_refunded",
                event_data=json.dumps({"deal_id": deal_id, "listing_status": next_listing_status}),
                created_at=current,
            ))
        _event(conn, deal_id=deal_id, event_type="deal_refunded", actor_type="admin",
               actor_id=admin_user_id, data={"reason": " ".join(reason.split())[:500],
                                             "journal_id": journal["entry_id"],
                                             "listing_status": next_listing_status}, now=current)
        return {"refunded": True, "duplicate": False, "journal_id": journal["entry_id"]}



def list_reconciliation_required_deals(*, engine: Engine | None = None,
                                           limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Read-only admin queue for deals that must be reconciled by a human."""
    if not 1 <= int(limit) <= 100 or int(offset) < 0:
        raise ValueError("Invalid reconciliation queue pagination")
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    query = select(
        marketplace_deals.c.deal_id, marketplace_deals.c.listing_id,
        marketplace_deals.c.reservation_id, marketplace_deals.c.asset_id,
        marketplace_deals.c.buyer_user_id, marketplace_deals.c.seller_user_id,
        marketplace_deals.c.buyer_wallet_address, marketplace_deals.c.currency,
        marketplace_deals.c.price_raw, marketplace_deals.c.nft_transfer_tx_hash,
        marketplace_deals.c.created_at, marketplace_deals.c.updated_at,
        marketplace_listings.c.status.label("listing_status"),
        marketplace_assets.c.canonical_asset_id,
        marketplace_assets.c.collection_address,
        marketplace_assets.c.token_id,
        marketplace_assets.c.owner_wallet_address,
        marketplace_assets.c.deposit_tx_hash,
        marketplace_assets.c.deposit_event_index,
        marketplace_assets.c.custody_state,
        marketplace_assets.c.ownership_state,
        marketplace_assets.c.ownership_expires_at,
        marketplace_reservations.c.expires_at.label("reservation_expires_at"),
    ).select_from(
        marketplace_deals.join(
            marketplace_listings,
            marketplace_listings.c.listing_id == marketplace_deals.c.listing_id,
        ).join(
            marketplace_assets,
            marketplace_assets.c.asset_id == marketplace_deals.c.asset_id,
        ).join(
            marketplace_reservations,
            marketplace_reservations.c.reservation_id == marketplace_deals.c.reservation_id,
        )
    ).where(
        marketplace_deals.c.status == "reconciliation_required"
    ).order_by(
        marketplace_deals.c.updated_at.asc(), marketplace_deals.c.deal_id.asc()
    ).limit(int(limit)).offset(int(offset))
    with db.connect() as conn:
        total = int(conn.execute(select(func.count()).select_from(
            marketplace_deals
        ).where(
            marketplace_deals.c.status == "reconciliation_required"
        )).scalar_one())
        rows = [dict(row) for row in conn.execute(query).mappings().all()]
        if not rows:
            return {"items": [], "total": total, "limit": int(limit), "offset": int(offset)}
        ids = [row["deal_id"] for row in rows]
        asset_ids = list({row["asset_id"] for row in rows})
        proof_ranked = select(
            marketplace_ownership_proofs.c.asset_id,
            marketplace_ownership_proofs.c.owner_user_id,
            marketplace_ownership_proofs.c.proof_source,
            marketplace_ownership_proofs.c.evidence_reference,
            marketplace_ownership_proofs.c.evidence_hash,
            marketplace_ownership_proofs.c.verification_status,
            marketplace_ownership_proofs.c.verified_at,
            marketplace_ownership_proofs.c.expires_at,
            marketplace_ownership_proofs.c.recorded_at,
            func.row_number().over(
                partition_by=(
                    marketplace_ownership_proofs.c.asset_id,
                    marketplace_ownership_proofs.c.proof_source,
                ),
                order_by=(
                    marketplace_ownership_proofs.c.recorded_at.desc(),
                    marketplace_ownership_proofs.c.proof_id.desc(),
                ),
            ).label("proof_rank"),
        ).where(
            marketplace_ownership_proofs.c.asset_id.in_(asset_ids)
        ).subquery()
        proofs = conn.execute(select(
            proof_ranked.c.asset_id,
            proof_ranked.c.owner_user_id,
            proof_ranked.c.proof_source,
            proof_ranked.c.evidence_reference,
            proof_ranked.c.evidence_hash,
            proof_ranked.c.verification_status,
            proof_ranked.c.verified_at,
            proof_ranked.c.expires_at,
            proof_ranked.c.recorded_at,
        ).where(
            proof_ranked.c.proof_rank == 1
        )).mappings().all()
        proofs_by_asset: dict[str, list[dict[str, Any]]] = {asset_id: [] for asset_id in asset_ids}
        for proof in proofs:
            proofs_by_asset[proof["asset_id"]].append({
                "owner_user_id": proof["owner_user_id"],
                "proof_source": proof["proof_source"],
                "evidence_reference": proof["evidence_reference"],
                "evidence_hash": proof["evidence_hash"],
                "verification_status": proof["verification_status"],
                "verified_at": proof["verified_at"],
                "expires_at": proof["expires_at"],
                "recorded_at": proof["recorded_at"],
            })
        event_ranked = select(
            marketplace_deal_events.c.deal_id,
            marketplace_deal_events.c.actor_type,
            marketplace_deal_events.c.event_type,
            marketplace_deal_events.c.event_data,
            marketplace_deal_events.c.created_at,
            func.row_number().over(
                partition_by=marketplace_deal_events.c.deal_id,
                order_by=(
                    marketplace_deal_events.c.created_at.desc(),
                    marketplace_deal_events.c.event_id.desc(),
                ),
            ).label("event_rank"),
        ).where(
            marketplace_deal_events.c.deal_id.in_(ids)
        ).subquery()
        events = conn.execute(select(
            event_ranked.c.deal_id,
            event_ranked.c.actor_type,
            event_ranked.c.event_type,
            event_ranked.c.event_data,
            event_ranked.c.created_at,
        ).where(
            event_ranked.c.event_rank <= 25
        ).order_by(
            event_ranked.c.deal_id.asc(),
            event_ranked.c.event_rank.desc(),
        )).mappings().all()
    by_deal: dict[str, list[dict[str, Any]]] = {deal_id: [] for deal_id in ids}
    for event in events:
        try:
            data = json.loads(event["event_data"] or "{}")
            if not isinstance(data, dict):
                data = {}
        except (TypeError, ValueError):
            data = {"unavailable": True}
        by_deal[event["deal_id"]].append({
            "actor_type": event["actor_type"],
            "event_type": event["event_type"],
            "data": data,
            "created_at": int(event["created_at"]),
        })
    for row in rows:
        asset_proofs = proofs_by_asset.get(row["asset_id"], [])
        row["ownership_proofs"] = asset_proofs
        row["latest_ownership_proof"] = max(
            asset_proofs, key=lambda proof: int(proof["recorded_at"]), default=None
        )
        row["events"] = by_deal[row["deal_id"]][-25:]
    return {"items": rows, "total": total, "limit": int(limit), "offset": int(offset)}


def add_deal_reconciliation_note(*, deal_id: str, admin_user_id: str,
                                 note: str, idempotency_key: str,
                                 engine: Engine | None = None,
                                 now: int | None = None) -> dict[str, Any]:
    """Append an idempotent admin note without changing deal or money state."""
    admin = str(admin_user_id or "")
    clean_note = " ".join(str(note or "").split())
    key = str(idempotency_key or "").strip()
    if not admin.isdigit() or int(admin) <= 0:
        raise ValueError("A valid admin identity is required")
    if not 8 <= len(clean_note) <= 1000:
        raise ValueError("Reconciliation note must contain 8-1000 characters")
    if not 16 <= len(key) <= 128:
        raise ValueError("A 16-128 character idempotency key is required")
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    event_id = hashlib.sha256(
        ("reconciliation-note:" + deal_id + ":" + key).encode("utf-8")
    ).hexdigest()
    with db.begin() as conn:
        query = select(marketplace_deals.c.status).where(
            marketplace_deals.c.deal_id == deal_id
        )
        if lock:
            query = query.with_for_update()
        status = conn.execute(query).scalar_one_or_none()
        if status is None:
            raise ValueError("Unknown marketplace deal")
        prior = conn.execute(select(
            marketplace_deal_events.c.actor_id,
            marketplace_deal_events.c.event_data,
        ).where(
            marketplace_deal_events.c.event_id == event_id
        )).mappings().first()
        if prior:
            try:
                prior_data = json.loads(prior["event_data"] or "{}")
            except (TypeError, ValueError):
                prior_data = {}
            if prior["actor_id"] != admin or prior_data.get("note") != clean_note:
                raise ValueError("Reconciliation idempotency key was reused with different details")
            return {"recorded": False, "duplicate": True, "status": status}
        if status != "reconciliation_required":
            raise PermissionError("Admin notes are restricted to deals awaiting reconciliation")
        conn.execute(insert(marketplace_deal_events).values(
            event_id=event_id, deal_id=deal_id, actor_type="admin",
            actor_id=admin, event_type="reconciliation_note_added",
            event_data=json.dumps({
                "note": clean_note, "idempotency_key": key,
                "changes_financial_state": False,
            }, separators=(",", ":"), sort_keys=True),
            created_at=current,
        ))
    return {"recorded": True, "duplicate": False,
            "status": "reconciliation_required"}


def expire_unpaid_marketplace_deals(*, engine: Engine | None = None,
                                    now: int | None = None,
                                    limit: int = 100) -> dict[str, int]:
    """Expire unpaid deals at reservation deadline and safely release their listing."""
    if not 1 <= int(limit) <= 500:
        raise ValueError("Invalid expiry batch size")
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    lock = db.dialect.name == "postgresql"
    query = select(
        marketplace_deals.c.deal_id, marketplace_deals.c.listing_id,
        marketplace_deals.c.reservation_id, marketplace_deals.c.asset_id,
    ).select_from(marketplace_deals.join(
        marketplace_reservations,
        marketplace_reservations.c.reservation_id == marketplace_deals.c.reservation_id,
    )).where(
        marketplace_deals.c.status == "reserved",
        marketplace_reservations.c.status == "active",
        marketplace_reservations.c.expires_at <= current,
    ).order_by(marketplace_reservations.c.expires_at, marketplace_deals.c.deal_id).limit(limit)
    if lock:
        query = query.with_for_update()
    expired = 0
    relisted = 0
    reverify = 0
    with db.begin() as conn:
        candidates = conn.execute(query).mappings().all()
        for deal in candidates:
            reservation_query = select(marketplace_reservations).where(
                marketplace_reservations.c.reservation_id == deal["reservation_id"])
            asset_query = select(marketplace_assets).where(
                marketplace_assets.c.asset_id == deal["asset_id"])
            listing_query = select(marketplace_listings).where(
                marketplace_listings.c.listing_id == deal["listing_id"])
            if lock:
                reservation_query = reservation_query.with_for_update()
                asset_query = asset_query.with_for_update()
                listing_query = listing_query.with_for_update()
            reservation = conn.execute(reservation_query).mappings().first()
            asset = conn.execute(asset_query).mappings().first()
            listing = conn.execute(listing_query).mappings().first()
            if (not reservation or reservation["status"] != "active"
                    or int(reservation["expires_at"]) > current or not listing
                    or listing["status"] != "reserved"):
                continue
            changed = conn.execute(update(marketplace_deals).where(
                marketplace_deals.c.deal_id == deal["deal_id"],
                marketplace_deals.c.status == "reserved",
            ).values(status="expired", updated_at=current))
            if changed.rowcount != 1:
                continue
            conn.execute(update(marketplace_reservations).where(
                marketplace_reservations.c.reservation_id == deal["reservation_id"],
                marketplace_reservations.c.status == "active",
            ).values(status="expired"))
            reusable = bool(asset and asset["custody_state"] == "escrowed"
                            and int(asset["ownership_expires_at"] or 0) > current)
            next_status = "active" if reusable else "pending_verification"
            conn.execute(update(marketplace_listings).where(
                marketplace_listings.c.listing_id == deal["listing_id"],
                marketplace_listings.c.status == "reserved",
            ).values(status=next_status, updated_at=current))
            _event(conn, deal_id=deal["deal_id"], event_type="payment_window_expired",
                   data={"listing_status": next_status}, now=current)
            conn.execute(insert(marketplace_listing_events).values(
                event_id=uuid.uuid4().hex, listing_id=deal["listing_id"],
                actor_user_id="system", event_type="reservation_expired",
                event_data=json.dumps({"deal_id": deal["deal_id"], "listing_status": next_status}),
                created_at=current,
            ))
            expired += 1
            relisted += int(next_status == "active")
            reverify += int(next_status == "pending_verification")
    return {"expired": expired, "relisted": relisted, "reverification_required": reverify}

def flag_deal_for_reconciliation(*, deal_id: str, reason: str,
                                 engine: Engine | None = None,
                                 now: int | None = None) -> None:
    db = engine or get_engine()
    init_marketplace_settlement_schema(db)
    current = int(now or time.time())
    clean = " ".join(str(reason or "").split())[:500] or "Deal requires manual reconciliation"
    with db.begin() as conn:
        changed = conn.execute(update(marketplace_deals).where(
            marketplace_deals.c.deal_id == deal_id,
            marketplace_deals.c.status.in_(("payment_confirmed", "nft_transfer_pending", "disputed")),
        ).values(status="reconciliation_required", updated_at=current))
        if changed.rowcount != 1:
            raise ValueError("Deal cannot enter reconciliation from its current state")
        _event(conn, deal_id=deal_id, event_type="reconciliation_required",
               data={"reason": clean}, now=current)
