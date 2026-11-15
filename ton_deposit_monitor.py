"""Independent-provider TON deposit observation and idempotent settlement gate.

This module consumes normalized evidence from independently operated TON providers.
Provider adapters must parse the complete transaction/message trace; this module never
trusts browser-submitted transaction hashes or wallet screenshots.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Protocol

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from financial_ledger import (
    _hash,
    get_engine,
    init_financial_schema,
    invoices,
    settle_verified_ton_payment,
    ton_chain_observations,
    verify_ton_payment_trace,
)


class TonTraceProvider(Protocol):
    provider_id: str

    async def find_invoice_transfer(self, invoice: dict[str, Any]) -> dict[str, Any] | None:
        """Return a normalized transfer trace or None when not observed."""


def _trusted_provider_ids() -> set[str]:
    raw = os.getenv("TON_DEPOSIT_PROVIDER_IDS", "")
    providers = {item.strip() for item in raw.split(",") if item.strip()}
    if providers != {"tonapi", "toncenter"}:
        raise RuntimeError("TON_DEPOSIT_PROVIDER_IDS must explicitly be 'tonapi,toncenter'")
    return providers


def _normalized_evidence(trace: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "chain", "asset_kind", "master_address", "source_jetton_wallet",
        "expected_source_jetton_wallet", "destination_jetton_wallet",
        "expected_destination_jetton_wallet", "sender_address", "recipient_address",
        "amount_raw", "payload", "opcode", "successful", "bounced", "finalized",
        "tx_hash", "event_index",
    )
    return {key: trace.get(key) for key in keys if key in trace}


def record_ton_provider_observation(
    *,
    invoice_id: str,
    provider_id: str,
    trace: dict[str, Any],
    engine: Engine | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Persist one provider's evidence and settle only after matching independent finality."""
    trusted = _trusted_provider_ids()
    provider = str(provider_id or "").strip()
    if provider not in trusted:
        raise PermissionError("TON observation came from an untrusted provider")
    if not isinstance(trace, dict) or trace.get("chain") != "ton-mainnet":
        raise ValueError("Only normalized TON mainnet traces are accepted")
    tx_hash = str(trace.get("tx_hash") or "").strip()
    asset_id = ""
    try:
        event_index = int(trace.get("event_index"))
    except (TypeError, ValueError):
        raise ValueError("Invalid TON event identity") from None
    if not tx_hash or len(tx_hash) > 128 or event_index < 0:
        raise ValueError("Invalid TON event identity")

    db = engine or get_engine()
    init_financial_schema(db)
    with db.connect() as conn:
        invoice_row = conn.execute(select(invoices).where(
            invoices.c.invoice_id == invoice_id
        )).mappings().first()
    if invoice_row is None:
        raise ValueError("Unknown payment invoice")
    invoice = dict(invoice_row)
    asset_id = invoice["asset_id"]
    if trace.get("finalized") is True:
        verify_ton_payment_trace(trace, invoice)

    normalized = _normalized_evidence(trace)
    digest = _hash(normalized)
    current = int(now or time.time())
    query = select(ton_chain_observations).where(
        ton_chain_observations.c.network == "ton-mainnet",
        ton_chain_observations.c.tx_hash == tx_hash,
        ton_chain_observations.c.event_index == event_index,
        ton_chain_observations.c.asset_id == asset_id,
        ton_chain_observations.c.provider_id == provider,
    )
    with db.begin() as conn:
        existing = conn.execute(query).mappings().first()
        if existing:
            if existing["evidence_hash"] != digest:
                raise ValueError("Provider changed evidence for an already observed chain event")
            duplicate = True
        else:
            try:
                conn.execute(insert(ton_chain_observations).values(
                    observation_id=str(uuid.uuid4()), network="ton-mainnet",
                    tx_hash=tx_hash, event_index=event_index, asset_id=asset_id,
                    provider_id=provider, evidence_hash=digest,
                    finalized=1 if trace.get("finalized") is True else 0,
                    observed_at=current,
                ))
                duplicate = False
            except IntegrityError:
                raise ValueError("Duplicate TON provider observation") from None

        rows = conn.execute(select(
            ton_chain_observations.c.provider_id,
            ton_chain_observations.c.evidence_hash,
            ton_chain_observations.c.finalized,
        ).where(
            ton_chain_observations.c.network == "ton-mainnet",
            ton_chain_observations.c.tx_hash == tx_hash,
            ton_chain_observations.c.event_index == event_index,
            ton_chain_observations.c.asset_id == asset_id,
        )).mappings().all()

    finalized = [row for row in rows if bool(row["finalized"])]
    hashes = {row["evidence_hash"] for row in finalized}
    if len(hashes) > 1:
        return {"settled": False, "review_required": True, "duplicate": duplicate,
                "provider_count": len(finalized)}
    matching_providers = {row["provider_id"] for row in finalized if row["evidence_hash"] == digest}
    if len(matching_providers) < 2:
        return {"settled": False, "review_required": False, "duplicate": duplicate,
                "provider_count": len(matching_providers)}
    result = settle_verified_ton_payment(invoice_id=invoice_id, trace=trace, engine=db, now=current)
    return {"settled": bool(result.get("credited") or result.get("duplicate")),
            "review_required": bool(result.get("review_required")), "duplicate": duplicate,
            "provider_count": len(matching_providers), **result}


async def scan_ton_invoice(
    *,
    invoice_id: str,
    providers: list[TonTraceProvider],
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Poll configured providers for one invoice; absence or disagreement never credits."""
    trusted = _trusted_provider_ids()
    by_id = {provider.provider_id: provider for provider in providers}
    if len(by_id) < 2 or not set(by_id).issubset(trusted):
        raise PermissionError("Two configured independent TON providers are required")
    db = engine or get_engine()
    init_financial_schema(db)
    with db.connect() as conn:
        row = conn.execute(select(invoices).where(
            invoices.c.invoice_id == invoice_id
        )).mappings().first()
    if row is None:
        raise ValueError("Unknown payment invoice")
    invoice = dict(row)
    results = []
    provider_errors: list[dict[str, str]] = []
    # An outage at one indexer must not prevent collecting evidence from the
    # other. A single provider can never satisfy the settlement quorum.
    for provider_id in sorted(by_id):
        try:
            trace = await by_id[provider_id].find_invoice_transfer(invoice)
            if trace is None:
                continue
            results.append(record_ton_provider_observation(
                invoice_id=invoice_id, provider_id=provider_id, trace=trace, engine=db
            ))
        except Exception as exc:
            provider_errors.append({
                "provider_id": provider_id,
                "error_type": type(exc).__name__,
            })
    settled = next((item for item in results if item.get("settled")), None)
    if settled:
        return {**settled, "provider_errors": provider_errors,
                "provider_error_count": len(provider_errors)}
    return {
        "settled": False,
        "review_required": any(item.get("review_required") for item in results),
        "provider_count": max((item.get("provider_count", 0) for item in results), default=0),
        "observations": len(results),
        "provider_errors": provider_errors,
        "provider_error_count": len(provider_errors),
    }



async def scan_pending_ton_invoices(
    *,
    providers: list[TonTraceProvider] | None = None,
    engine: Engine | None = None,
    batch_size: int = 100,
) -> dict[str, int]:
    """Continuously scan the oldest pending TON invoices with the configured provider quorum."""
    from ton_payment_providers import configured_ton_payment_providers

    db = engine or get_engine()
    init_financial_schema(db)
    selected = providers or configured_ton_payment_providers()
    current = int(time.time())
    grace_cutoff = current - 86400
    with db.begin() as conn:
        # Expired invoices remain observable for a 24-hour late-payment review window.
        # After that window they stop consuming provider polling capacity.
        conn.execute(invoices.update().where(
            invoices.c.status == "pending",
            invoices.c.chain == "ton-mainnet",
            invoices.c.expires_at <= grace_cutoff,
        ).values(status="expired"))
        invoice_ids = conn.execute(select(invoices.c.invoice_id).where(
            invoices.c.status == "pending",
            invoices.c.chain == "ton-mainnet",
            invoices.c.expires_at > grace_cutoff,
        ).order_by(invoices.c.created_at.asc()).limit(max(1, min(int(batch_size), 500)))).scalars().all()
    stats = {"checked": 0, "settled": 0, "observations": 0,
             "duplicates": 0, "review_required": 0, "provider_errors": 0, "errors": 0}
    for invoice_id in invoice_ids:
        stats["checked"] += 1
        try:
            result = await scan_ton_invoice(invoice_id=invoice_id, providers=selected, engine=db)
            stats["observations"] += int(result.get("observations", 0))
            stats["settled"] += int(bool(result.get("settled")))
            stats["duplicates"] += int(bool(result.get("duplicate")))
            stats["review_required"] += int(bool(result.get("review_required")))
            stats["provider_errors"] += int(result.get("provider_error_count", 0))
        except Exception as exc:
            # Never log transaction payloads, wallet addresses, invoice data or API credentials.
            import logging
            logging.warning("TON invoice scan failed (%s)", type(exc).__name__)
            stats["errors"] += 1
    return stats


async def run_ton_deposit_monitor(*, poll_seconds: int = 8) -> None:
    """Worker stays inert unless the full crypto-deposit and production gates are enabled."""
    import asyncio
    import logging

    enabled = os.getenv("TON_DEPOSIT_MONITOR_ENABLED", "false").strip().lower() == "true"
    crypto_enabled = os.getenv("TON_CRYPTO_DEPOSITS_ENABLED", "false").strip().lower() == "true"
    real_deals = os.getenv("MARKETPLACE_REAL_DEALS_ENABLED", "false").strip().lower() == "true"
    postgres = os.getenv("GIFT_RADAR_STORAGE_BACKEND", "").strip().lower() == "postgres"
    database_url = os.getenv("DATABASE_URL", "").strip().startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))
    legal_approved = os.getenv("KZ_LEGAL_REVIEW_APPROVED", "false").strip().lower() == "true"
    residency_verified = os.getenv("KZ_DATA_RESIDENCY_VERIFIED", "false").strip().lower() == "true"
    tax_approved = os.getenv("KZ_TAX_REVIEW_APPROVED", "false").strip().lower() == "true"
    security_approved = os.getenv("KZ_SECURITY_REVIEW_APPROVED", "false").strip().lower() == "true"
    if not all((enabled, crypto_enabled, real_deals, postgres, database_url,
                legal_approved, residency_verified, tax_approved, security_approved)):
        return
    delay = max(3, min(int(poll_seconds), 60))
    while True:
        try:
            await scan_pending_ton_invoices()
            delay = max(3, min(int(poll_seconds), 60))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("TON deposit monitor cycle failed (%s)", type(exc).__name__)
            delay = min(300, max(delay * 2, 5))
        await asyncio.sleep(delay)
