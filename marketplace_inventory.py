"""Seller-owned TON NFT inventory and escrow-gated listing lifecycle.

This module never trusts client ownership claims. The wallet address is taken from
server-verified TON Proof storage; two configured chain providers must agree on NFT
identity/owner. Listings stay pending until the NFT is independently observed in escrow.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, ForeignKey, Index,
    String, Table, Text, UniqueConstraint, insert, select, update, text,
)
from sqlalchemy.engine import Engine

from financial_ledger import _hash, get_engine, metadata

verified_wallets = Table(
    "verified_wallets", metadata,
    Column("wallet_id", String(36), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("network", String(32), nullable=False),
    Column("address", String(128), nullable=False),
    Column("verified_at", BigInteger, nullable=False),
    Column("revoked_at", BigInteger),
    UniqueConstraint("user_id", "network", "address", name="uq_verified_wallet_user_address"),
    CheckConstraint("network='ton-mainnet'", name="ck_verified_wallet_network"),
)
Index("idx_verified_wallet_active", verified_wallets.c.user_id, verified_wallets.c.revoked_at)

marketplace_assets = Table(
    "marketplace_assets", metadata,
    Column("asset_id", String(64), primary_key=True),
    Column("asset_type", String(24), nullable=False),
    Column("network", String(32), nullable=False),
    Column("canonical_asset_id", String(256), nullable=False, unique=True),
    Column("collection_address", String(128), nullable=False, default=""),
    Column("token_id", String(128), nullable=False, default=""),
    Column("telegram_gift_id", String(128), nullable=False, default=""),
    Column("owner_user_id", String(64), nullable=False),
    Column("owner_wallet_address", String(128)),
    Column("ownership_state", String(16), nullable=False, default="unverified"),
    Column("ownership_checked_at", BigInteger),
    Column("ownership_expires_at", BigInteger),
    Column("custody_state", String(24), nullable=False, default="wallet_owned"),
    Column("deposit_tx_hash", String(128)),
    Column("deposit_event_index", BigInteger),
    Column("custody_verified_at", BigInteger),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    CheckConstraint("asset_type IN ('ton_nft','telegram_gift','collectible_username','collectible_phone_number')", name="ck_marketplace_assets_type"),
    CheckConstraint("network IN ('ton-mainnet','telegram')", name="ck_marketplace_assets_network"),
    CheckConstraint("ownership_state IN ('unverified','verified','stale','revoked')", name="ck_marketplace_assets_ownership"),
    CheckConstraint("custody_state IN ('wallet_owned','escrow_pending','escrowed','transferred','telegram_profile_only','reconciliation_required')", name="ck_marketplace_assets_custody_state"),
    CheckConstraint("(asset_type IN ('ton_nft','collectible_username','collectible_phone_number') AND network='ton-mainnet' AND collection_address<>'' AND token_id<>'') OR (asset_type='telegram_gift' AND network='telegram' AND telegram_gift_id<>'')", name="ck_marketplace_assets_identity"),
)
Index("idx_marketplace_assets_owner", marketplace_assets.c.owner_user_id, marketplace_assets.c.ownership_state)

marketplace_ownership_proofs = Table(
    "marketplace_ownership_proofs", metadata,
    Column("proof_id", String(64), primary_key=True),
    Column("asset_id", String(64), ForeignKey("marketplace_assets.asset_id"), nullable=False),
    Column("owner_user_id", String(64), nullable=False),
    Column("proof_source", String(48), nullable=False),
    Column("evidence_reference", String(256), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    Column("verification_status", String(16), nullable=False),
    Column("verified_at", BigInteger),
    Column("expires_at", BigInteger),
    Column("recorded_at", BigInteger, nullable=False),
    CheckConstraint("verification_status IN ('pending','verified','rejected','expired')", name="ck_marketplace_ownership_proofs_status"),
)

marketplace_listings = Table(
    "marketplace_listings", metadata,
    Column("listing_id", String(64), primary_key=True),
    Column("asset_id", String(64), ForeignKey("marketplace_assets.asset_id"), nullable=False),
    Column("seller_user_id", String(64), nullable=False),
    Column("price_raw", BigInteger, nullable=False),
    Column("currency", String(8), nullable=False),
    Column("status", String(24), nullable=False),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    CheckConstraint("price_raw>0", name="ck_marketplace_listings_positive_price"),
    CheckConstraint("currency IN ('XTR','GRAM','USDT')", name="ck_marketplace_listings_currency"),
    CheckConstraint("status IN ('draft','pending_verification','active','reserved','sold','cancelled','expired','rejected')", name="ck_marketplace_listings_status"),
)
Index("idx_marketplace_listings_status_created", marketplace_listings.c.status, marketplace_listings.c.created_at)
Index("uq_marketplace_listings_one_open_asset", marketplace_listings.c.asset_id, unique=True,
      sqlite_where=marketplace_listings.c.status.in_(("draft","pending_verification","active","reserved")),
      postgresql_where=marketplace_listings.c.status.in_(("draft","pending_verification","active","reserved")))

marketplace_listing_events = Table(
    "marketplace_listing_events", metadata,
    Column("event_id", String(64), primary_key=True),
    Column("listing_id", String(64), ForeignKey("marketplace_listings.listing_id"), nullable=False),
    Column("actor_user_id", String(64), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("event_data", Text, nullable=False, default="{}"),
    Column("created_at", BigInteger, nullable=False),
)

marketplace_reservations = Table(
    "marketplace_reservations", metadata,
    Column("reservation_id", String(64), primary_key=True),
    Column("listing_id", String(64), ForeignKey("marketplace_listings.listing_id"), nullable=False),
    Column("buyer_user_id", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("status IN ('active','consumed','expired','cancelled')", name="ck_marketplace_reservations_status"),
    CheckConstraint("expires_at>created_at", name="ck_marketplace_reservations_expiry"),
)
Index("uq_marketplace_reservations_one_active_listing", marketplace_reservations.c.listing_id, unique=True,
      sqlite_where=marketplace_reservations.c.status == "active",
      postgresql_where=marketplace_reservations.c.status == "active")


def init_inventory_schema(engine: Engine | None = None) -> None:
    db = engine or get_engine()
    if db.dialect.name == "postgresql":
        with db.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if version != "0009_seller_wallet_inventory":
            raise RuntimeError("Seller inventory migration 0009 is required")
        return
    metadata.create_all(db)
    if db.dialect.name == "sqlite":
        with db.begin() as conn:
            for table in ("marketplace_ownership_proofs", "marketplace_listing_events"):
                conn.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'marketplace evidence and events are append-only'); END"
                ))
                conn.execute(__import__("sqlalchemy").text(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'marketplace evidence and events are append-only'); END"
                ))


def save_verified_wallet(*, user_id: str, address: str, network: str,
                         engine: Engine | None = None, now: int | None = None) -> None:
    if not str(user_id).isdigit() or network != "ton-mainnet" or not address or len(address) > 128:
        raise ValueError("Invalid verified wallet binding")
    db = engine or get_engine()
    init_inventory_schema(db)
    current = int(now or time.time())
    with db.begin() as conn:
        existing = conn.execute(select(verified_wallets).where(
            verified_wallets.c.user_id == str(user_id),
            verified_wallets.c.network == network,
            verified_wallets.c.address == address,
        )).mappings().first()
        if existing:
            conn.execute(update(verified_wallets).where(
                verified_wallets.c.wallet_id == existing["wallet_id"]
            ).values(verified_at=current, revoked_at=None))
        else:
            conn.execute(insert(verified_wallets).values(
                wallet_id=str(uuid.uuid4()), user_id=str(user_id), network=network,
                address=address, verified_at=current, revoked_at=None,
            ))


def get_active_verified_wallet(*, user_id: str, engine: Engine | None = None) -> str:
    db = engine or get_engine()
    init_inventory_schema(db)
    with db.connect() as conn:
        row = conn.execute(select(verified_wallets.c.address).where(
            verified_wallets.c.user_id == str(user_id),
            verified_wallets.c.network == "ton-mainnet",
            verified_wallets.c.revoked_at.is_(None),
        ).order_by(verified_wallets.c.verified_at.desc())).first()
    if not row:
        raise PermissionError("Connect and verify a TON mainnet wallet before importing an NFT")
    return str(row.address)


def record_wallet_owned_nft(*, user_id: str, wallet_address: str, asset_type: str,
                            collection_address: str, token_id: str, item_address: str,
                            evidence: dict[str, Any], proof_ttl_seconds: int = 300,
                            engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Persist a dual-provider-verified wallet-owned NFT; it is not yet a listing."""
    allowed = {"ton_nft", "collectible_username", "collectible_phone_number"}
    if asset_type not in allowed or not evidence.get("verified"):
        raise PermissionError("A successful dual-provider NFT ownership proof is required")
    if evidence.get("owner_wallet_address") != wallet_address:
        raise PermissionError("NFT owner does not match the user's verified wallet")
    if (evidence.get("collection_address") != collection_address or
            str(evidence.get("token_id")) != str(token_id) or
            evidence.get("item_address") != item_address):
        raise ValueError("NFT identity does not match verified chain evidence")
    db = engine or get_engine()
    init_inventory_schema(db)
    current = int(now or time.time())
    canonical = f"ton-mainnet:{collection_address}:{int(token_id)}"
    asset_id = "gra_" + uuid.uuid4().hex
    with db.begin() as conn:
        existing = conn.execute(select(marketplace_assets).where(
            marketplace_assets.c.canonical_asset_id == canonical
        )).mappings().first()
        if existing and existing["owner_user_id"] != str(user_id):
            raise PermissionError("NFT is already registered to another seller")
        if existing and existing["asset_type"] != asset_type:
            raise ValueError("NFT asset type cannot change for an existing canonical identity")
        if existing and existing["custody_state"] in ("escrow_pending", "escrowed", "transferred"):
            raise ValueError("NFT already has an active custody lifecycle")
        if existing:
            asset_id = existing["asset_id"]
            conn.execute(update(marketplace_assets).where(
                marketplace_assets.c.asset_id == asset_id
            ).values(owner_wallet_address=wallet_address, ownership_state="verified",
                     ownership_checked_at=current, ownership_expires_at=current + proof_ttl_seconds,
                     custody_state="wallet_owned", updated_at=current))
        else:
            conn.execute(insert(marketplace_assets).values(
                asset_id=asset_id, asset_type=asset_type, network="ton-mainnet",
                canonical_asset_id=canonical, collection_address=collection_address,
                token_id=str(int(token_id)), telegram_gift_id="", owner_user_id=str(user_id),
                owner_wallet_address=wallet_address, ownership_state="verified",
                ownership_checked_at=current, ownership_expires_at=current + proof_ttl_seconds,
                custody_state="wallet_owned", created_at=current, updated_at=current,
            ))
        proof_hash = str(evidence.get("evidence_hash") or "")
        if len(proof_hash) != 64:
            raise ValueError("Chain ownership evidence hash is invalid")
        conn.execute(insert(marketplace_ownership_proofs).values(
            proof_id=uuid.uuid4().hex, asset_id=asset_id, owner_user_id=str(user_id),
            proof_source="ton_dual_provider", evidence_reference=item_address,
            evidence_hash=proof_hash, verification_status="verified",
            verified_at=current, expires_at=current + proof_ttl_seconds, recorded_at=current,
        ))
    return {"asset_id": asset_id, "canonical_asset_id": canonical,
            "custody_state": "wallet_owned", "listing_eligible": False}


def create_escrow_pending_listing(*, user_id: str, asset_id: str, price_raw: int,
                                  currency: str, idempotency_key: str,
                                  engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Create a private pending listing. It cannot appear publicly before escrow."""
    db = engine or get_engine()
    init_inventory_schema(db)
    if int(price_raw) <= 0 or currency not in {"XTR", "GRAM", "USDT"}:
        raise ValueError("Invalid listing price or currency")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("A bounded listing idempotency key is required")
    current = int(now or time.time())
    listing_id = "grl_" + uuid.uuid4().hex
    with db.begin() as conn:
        existing = conn.execute(select(marketplace_listings).where(
            marketplace_listings.c.idempotency_key == idempotency_key
        )).mappings().first()
        if existing:
            if (existing["seller_user_id"] != str(user_id) or existing["asset_id"] != asset_id
                    or int(existing["price_raw"]) != int(price_raw) or existing["currency"] != currency):
                raise ValueError("Listing idempotency key was reused with different details")
            return {**dict(existing), "duplicate": True}
        statement = select(marketplace_assets).where(
            marketplace_assets.c.asset_id == asset_id
        )
        if db.dialect.name == "postgresql":
            statement = statement.with_for_update()
        asset = conn.execute(statement).mappings().first()
        if not asset or asset["owner_user_id"] != str(user_id):
            raise PermissionError("Seller does not own this registered NFT")
        if asset["ownership_state"] != "verified" or int(asset["ownership_expires_at"] or 0) <= current:
            raise PermissionError("NFT ownership proof is stale; re-verify before listing")
        if asset["custody_state"] != "wallet_owned":
            raise ValueError("NFT is not in the seller wallet state")
        conn.execute(insert(marketplace_listings).values(
            listing_id=listing_id, asset_id=asset_id, seller_user_id=str(user_id),
            price_raw=int(price_raw), currency=currency, status="pending_verification",
            idempotency_key=idempotency_key, created_at=current, updated_at=current,
        ))
        conn.execute(update(marketplace_assets).where(
            marketplace_assets.c.asset_id == asset_id
        ).values(custody_state="escrow_pending", updated_at=current))
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=listing_id, actor_user_id=str(user_id),
            event_type="created", event_data='{"status":"pending_verification"}', created_at=current,
        ))
        row = conn.execute(select(marketplace_listings).where(
            marketplace_listings.c.listing_id == listing_id
        )).mappings().one()
    return {**dict(row), "duplicate": False,
            "next_step": "Transfer the NFT from your wallet to the official GIFT RADAR escrow address"}


def confirm_nft_escrow_deposit(*, listing_id: str, tx_hash: str, event_index: int,
                               verified_escrow_owner: str, expected_escrow_owner: str,
                               evidence_hash: str, provider_ids: list[str],
                               engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    """Activate only after independent providers prove the NFT's current escrow owner."""
    if verified_escrow_owner != expected_escrow_owner or not tx_hash or event_index < 0:
        raise PermissionError("NFT escrow ownership proof failed")
    if set(provider_ids) != {"tonapi", "toncenter"} or len(evidence_hash) != 64:
        raise PermissionError("The named independent TON providers are required")
    db = engine or get_engine()
    init_inventory_schema(db)
    current = int(now or time.time())
    statement = select(marketplace_listings).where(
        marketplace_listings.c.listing_id == listing_id
    )
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        listing = conn.execute(statement).mappings().first()
        if not listing or listing["status"] != "pending_verification":
            raise ValueError("Listing is not awaiting escrow verification")
        asset_stmt = select(marketplace_assets).where(
            marketplace_assets.c.asset_id == listing["asset_id"]
        )
        if db.dialect.name == "postgresql":
            asset_stmt = asset_stmt.with_for_update()
        asset = conn.execute(asset_stmt).mappings().one()
        conn.execute(update(marketplace_assets).where(
            marketplace_assets.c.asset_id == asset["asset_id"]
        ).values(custody_state="escrowed", deposit_tx_hash=tx_hash,
                 deposit_event_index=event_index, custody_verified_at=current,
                 ownership_state="verified", ownership_checked_at=current,
                 ownership_expires_at=current + 86400, updated_at=current))
        conn.execute(update(marketplace_listings).where(
            marketplace_listings.c.listing_id == listing_id,
            marketplace_listings.c.status == "pending_verification"
        ).values(status="active", updated_at=current))
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=listing_id,
            actor_user_id="system", event_type="verified",
            event_data=__import__("json").dumps({
                "tx_hash": tx_hash, "event_index": event_index,
                "evidence_hash": evidence_hash, "provider_ids": sorted(set(provider_ids)),
            }, separators=(",", ":")), created_at=current,
        ))
    return {"listing_id": listing_id, "status": "active", "public": True}


async def verify_and_activate_escrow_listing(*, listing_id: str,
                                          engine: Engine | None = None,
                                          providers: list[Any] | None = None) -> dict[str, Any]:
    """Poll two independent provider histories and live NFT state before publication."""
    from ton_nft_providers import configured_nft_providers, canonical_ton_address

    db = engine or get_engine()
    init_inventory_schema(db)
    escrow_address = os.getenv("GIFT_RADAR_ESCROW_ADDRESS", "").strip()
    if not escrow_address:
        raise RuntimeError("GIFT_RADAR_ESCROW_ADDRESS is not configured")
    escrow_address = canonical_ton_address(escrow_address)
    providers = providers or configured_nft_providers()
    if {provider.provider_id for provider in providers} != {"tonapi", "toncenter"}:
        raise PermissionError("The named independent TON providers are required")
    with db.connect() as conn:
        listing = conn.execute(select(marketplace_listings).where(
            marketplace_listings.c.listing_id == listing_id
        )).mappings().first()
        if not listing or listing["status"] != "pending_verification":
            return {"activated": False, "reason": "not_pending"}
        asset = conn.execute(select(marketplace_assets).where(
            marketplace_assets.c.asset_id == listing["asset_id"]
        )).mappings().one()
        proof = conn.execute(select(marketplace_ownership_proofs).where(
            marketplace_ownership_proofs.c.asset_id == asset["asset_id"],
            marketplace_ownership_proofs.c.verification_status == "verified",
        ).order_by(marketplace_ownership_proofs.c.recorded_at.desc())).mappings().first()
    if not proof:
        raise PermissionError("Original seller NFT ownership proof is missing")
    item_address = proof["evidence_reference"]
    seller_wallet = canonical_ton_address(asset["owner_wallet_address"] or "")
    results = []
    for provider in providers:
        state = await provider.get_nft_state(item_address)
        transfer = await provider.get_nft_transfer(
            item_address=item_address, old_owner=seller_wallet, new_owner=escrow_address,
        )
        if state is None or transfer is None:
            return {"activated": False, "reason": "transfer_not_observed"}
        normalized_state = {
            "item_address": canonical_ton_address(state.get("item_address", "")),
            "collection_address": canonical_ton_address(state.get("collection_address", "")),
            "token_id": str(state.get("token_id", "")),
            "owner_address": canonical_ton_address(state.get("owner_address", "")),
            "initialized": state.get("initialized") is True,
        }
        if (
            normalized_state["item_address"] != canonical_ton_address(item_address)
            or normalized_state["collection_address"] != canonical_ton_address(asset["collection_address"])
            or normalized_state["token_id"] != str(asset["token_id"])
            or normalized_state["owner_address"] != escrow_address
            or normalized_state["initialized"] is not True
        ):
            raise PermissionError("Provider state does not prove this NFT is owned by GIFT RADAR escrow")
        results.append({
            "provider_id": provider.provider_id,
            "state": normalized_state,
            "transfer": {
                "tx_hash": str(transfer["tx_hash"]).removeprefix("0x").lower(),
                "event_index": int(transfer.get("event_index", 0)),
            },
        })
    if {item["provider_id"] for item in results} != {"tonapi", "toncenter"}:
        raise PermissionError("Two independent TON providers are required")
    transfer_hashes = {item["transfer"]["tx_hash"] for item in results}
    state_hashes = {_hash(item["state"]) for item in results}
    if len(transfer_hashes) != 1 or len(state_hashes) != 1:
        raise PermissionError("TON providers disagree about NFT escrow transfer or ownership")
    transfer_hash = next(iter(transfer_hashes))
    evidence_hash = _hash({"state": results[0]["state"], "transfer_hash": transfer_hash})
    result = confirm_nft_escrow_deposit(
        listing_id=listing_id, tx_hash=transfer_hash, event_index=0,
        verified_escrow_owner=results[0]["state"]["owner_address"],
        expected_escrow_owner=escrow_address, evidence_hash=evidence_hash,
        provider_ids=[item["provider_id"] for item in results], engine=db,
    )
    return {"activated": True, **result}


async def scan_pending_nft_listings(*, engine: Engine | None = None) -> dict[str, int]:
    db = engine or get_engine()
    init_inventory_schema(db)
    with db.connect() as conn:
        ids = conn.execute(select(marketplace_listings.c.listing_id).where(
            marketplace_listings.c.status == "pending_verification"
        ).limit(100)).scalars().all()
    activated = 0
    errors = 0
    for listing_id in ids:
        try:
            result = await verify_and_activate_escrow_listing(listing_id=listing_id, engine=db)
            activated += int(bool(result.get("activated")))
        except Exception:
            errors += 1
    return {"checked": len(ids), "activated": activated, "errors": errors}


def reserve_verified_listing(*, listing_id: str, buyer_user_id: str,
                             ttl_seconds: int = 300, engine: Engine | None = None,
                             now: int | None = None) -> dict[str, Any]:
    """Atomically reserve a publicly active escrow-backed NFT listing."""
    db = engine or get_engine()
    init_inventory_schema(db)
    if not str(buyer_user_id).isdigit() or int(buyer_user_id) <= 0 or not 30 <= ttl_seconds <= 900:
        raise ValueError("Invalid buyer or reservation lifetime")
    current = int(now or time.time())
    statement = select(marketplace_listings).where(
        marketplace_listings.c.listing_id == listing_id
    )
    if db.dialect.name == "postgresql":
        statement = statement.with_for_update()
    with db.begin() as conn:
        listing = conn.execute(statement).mappings().first()
        if not listing or listing["status"] != "active":
            raise ValueError("Only active escrow-backed listings can be reserved")
        if listing["seller_user_id"] == str(buyer_user_id):
            raise PermissionError("Seller cannot reserve their own listing")
        asset = conn.execute(select(marketplace_assets).where(
            marketplace_assets.c.asset_id == listing["asset_id"]
        )).mappings().one()
        if asset["custody_state"] != "escrowed":
            raise PermissionError("NFT is not confirmed in marketplace escrow")
        if int(asset["ownership_expires_at"] or 0) <= current:
            raise PermissionError("Escrow ownership proof is stale")
        conn.execute(update(marketplace_reservations).where(
            marketplace_reservations.c.listing_id == listing_id,
            marketplace_reservations.c.status == "active",
            marketplace_reservations.c.expires_at <= current,
        ).values(status="expired"))
        reservation_id = "grr_" + uuid.uuid4().hex
        conn.execute(insert(marketplace_reservations).values(
            reservation_id=reservation_id, listing_id=listing_id,
            buyer_user_id=str(buyer_user_id), status="active",
            expires_at=current + ttl_seconds, created_at=current,
        ))
        changed = conn.execute(update(marketplace_listings).where(
            marketplace_listings.c.listing_id == listing_id,
            marketplace_listings.c.status == "active"
        ).values(status="reserved", updated_at=current))
        if changed.rowcount != 1:
            raise ValueError("Listing was reserved concurrently")
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=listing_id,
            actor_user_id=str(buyer_user_id), event_type="reserved",
            event_data="{}", created_at=current,
        ))
    return {"reservation_id": reservation_id, "listing_id": listing_id,
            "status": "active", "expires_at": current + ttl_seconds}


def cancel_listing_reservation(*, reservation_id: str, actor_user_id: str,
                               engine: Engine | None = None, now: int | None = None) -> dict[str, Any]:
    db = engine or get_engine()
    init_inventory_schema(db)
    current = int(now or time.time())
    with db.begin() as conn:
        reservation = conn.execute(select(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == reservation_id
        )).mappings().first()
        if not reservation:
            raise ValueError("Unknown reservation")
        if reservation["buyer_user_id"] != str(actor_user_id):
            raise PermissionError("Only the buyer can cancel this reservation")
        if reservation["status"] != "active":
            return {"cancelled": False, "duplicate": True}
        conn.execute(update(marketplace_reservations).where(
            marketplace_reservations.c.reservation_id == reservation_id
        ).values(status="cancelled"))
        conn.execute(update(marketplace_listings).where(
            marketplace_listings.c.listing_id == reservation["listing_id"],
            marketplace_listings.c.status == "reserved"
        ).values(status="active", updated_at=current))
        conn.execute(insert(marketplace_listing_events).values(
            event_id=uuid.uuid4().hex, listing_id=reservation["listing_id"],
            actor_user_id=str(actor_user_id), event_type="reservation_expired",
            event_data='{"reason":"buyer_cancelled"}', created_at=current,
        ))
    return {"cancelled": True, "duplicate": False}
