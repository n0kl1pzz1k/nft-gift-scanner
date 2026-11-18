"""TON Connect proof verification and Telegram-account wallet binding.

Only reviewed standard wallet StateInit layouts are accepted. Unknown wallet
contracts fail closed; the wallet-reported public key is never trusted alone.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
import sqlite3
import time
from typing import Any

from marketplace import _db
from database import begin_write, select_for_update

# Official TON standard wallet code hashes. Layouts are version-specific.
_WALLET_LAYOUTS = {
    "a0cfc2c48aee16a271f2cfc0b7382d81756cecb1017d077faaab3bb602f6868c": 1,  # V1R1
    "d4902fcc9fad74698fa8e353220a68da0dcf72e32bcb2eb9ee04217c17d3062c": 1,  # V1R2
    "587cc789eff1c84f46ec3797e45fc809a14ff5ae24f1e0c7a6a99cc9dc9061ff": 1,  # V1R3
    "5c9a5e68c108e18721a07c42f9956bfb39ad77ec6d624b60c576ec88eee65329": 1,  # V2R1
    "fe9530d3243853083ef2ef0b4c2908c0abf6fa1c31ea243aacaa5bf8c7d753f1": 1,  # V2R2
    "b61041a58a7980b946e8fb9e198e3c904d24799ffa36574ea4251c41a566f581": 2,  # V3R1
    "84dafa449f98a6987789ba232358072bc0f76dc4524002a5d0918b9a75d2d599": 2,  # V3R2
    "64dd54805522c5be8a9db59cea0105ccf0d08786ca79beb8cb79e880a8d7322d": 2,  # V4R1
    "feb5ff6820e2ff0d9483e7e0d62c817d846789fb4ae580c878866d959dabd5c0": 2,  # V4R2
    "20834b7b72b112147e1b2fb457b84e74d1a30f04f737d4f62a668e9552d2b72f": 3,  # V5R1
}
PROOF_MAX_AGE_SECONDS = 600
CHALLENGE_TTL_SECONDS = 300


def ensure_ton_connect_schema(connection: object) -> None:
    """Create TON Connect tables using portable, single-statement DDL.

    SQLite and the staged PostgreSQL adapter both support this schema. Runtime
    PostgreSQL selection remains disabled until the wider marketplace schema
    and query paths have been ported and reviewed.
    """
    if not isinstance(connection, sqlite3.Connection) and not bool(
        getattr(connection, "supports_for_update", False)
    ):
        raise RuntimeError("Unsupported database connection for TON Connect schema")

    statements = (
        """CREATE TABLE IF NOT EXISTS ton_proof_challenges (
            payload_hash TEXT PRIMARY KEY,
            telegram_id TEXT NOT NULL,
            session_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            consumed_at INTEGER
        )""",
        """CREATE INDEX IF NOT EXISTS idx_ton_proof_challenges_expiry
            ON ton_proof_challenges(expires_at)""",
        """CREATE TABLE IF NOT EXISTS verified_wallets (
            network TEXT NOT NULL,
            address TEXT NOT NULL,
            telegram_id TEXT NOT NULL,
            public_key TEXT NOT NULL,
            state_init_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_verified_at INTEGER NOT NULL,
            revoked_at INTEGER,
            PRIMARY KEY(network, address)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_verified_wallets_owner
            ON verified_wallets(telegram_id, revoked_at, last_verified_at DESC)""",
    )
    for statement in statements:
        connection.execute(statement)


def init_ton_connect_db() -> None:
    with _db() as conn:
        ensure_ton_connect_schema(conn)


def create_proof_challenge(telegram_id: str, session_token: str, now: int | None = None) -> str:
    user_id = str(telegram_id).strip()
    if not user_id.isdigit() or not session_token.startswith("grs_"):
        raise ValueError("A verified Telegram session is required")
    issued_at = int(time.time()) if now is None else int(now)
    payload = secrets.token_urlsafe(32)
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    with _db() as conn:
        begin_write(conn, f"ton-session:{user_id}:{session_hash}")
        conn.execute("DELETE FROM ton_proof_challenges WHERE expires_at <= ? OR consumed_at IS NOT NULL", (issued_at,))
        conn.execute(
            "DELETE FROM ton_proof_challenges WHERE telegram_id = ? AND session_hash = ? AND consumed_at IS NULL",
            (user_id, session_hash),
        )
        conn.execute(
            """INSERT INTO ton_proof_challenges
               (payload_hash, telegram_id, session_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (payload_hash, user_id, session_hash, issued_at, issued_at + CHALLENGE_TTL_SECONDS),
        )
        conn.commit()
    return payload


def _parse_state_init(state_init_b64: str) -> tuple[bytes, bytes, str]:
    if not isinstance(state_init_b64, str) or len(state_init_b64) > 16_384:
        raise ValueError("Invalid wallet StateInit")
    try:
        from tonsdk.boc import Cell
        raw = base64.b64decode(state_init_b64, validate=True)
        root = Cell.one_from_boc(raw)
        state_hash = root.bytes_hash()
        sl = root.begin_parse()
        if sl.read_bit():
            sl.read_uint(5)  # split_depth
        if sl.read_bit():
            sl.read_uint(2)  # special TickTock
        code = sl.read_ref() if sl.read_bit() else None
        data = sl.read_ref() if sl.read_bit() else None
        if sl.read_bit():
            sl.read_ref()  # library
        if code is None or data is None:
            raise ValueError("Wallet StateInit must contain code and data")
        code_hash = code.bytes_hash().hex()
        layout = _WALLET_LAYOUTS.get(code_hash)
        if layout is None:
            raise ValueError("Unsupported wallet contract version")
        data_slice = data.begin_parse()
        if layout == 3:
            data_slice.read_bit()  # V5 signature-authentication flag
        data_slice.read_uint(32)  # seqno
        if layout in {2, 3}:
            data_slice.read_uint(32)  # subwallet_id / wallet_id
        public_key = data_slice.read_bytes(32)
        if len(public_key) != 32:
            raise ValueError("Invalid wallet public key")
        return state_hash, public_key, code_hash
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Could not parse wallet StateInit") from exc


def _parse_address(address_text: str) -> tuple[int, bytes, str]:
    from tonsdk.utils import Address
    address = Address(address_text)
    workchain = int(address.wc)
    account_hash = bytes(address.hash_part)
    if len(account_hash) != 32:
        raise ValueError("Invalid TON address")
    raw = f"{workchain}:{account_hash.hex()}"
    return workchain, account_hash, raw


def verify_ton_proof(
    address: str,
    network: str,
    public_key_hex: str | None,
    proof: dict[str, Any],
    state_init_b64: str,
    expected_domain: str,
    now: int | None = None,
) -> dict[str, str]:
    """Verify TON Connect ton_proof against a supported, address-bound StateInit."""
    if network not in {"-239", "-3"}:
        raise ValueError("Unsupported TON network")
    if network not in {part.strip() for part in os.getenv("TON_CONNECT_NETWORKS", "-239").split(",")}:
        raise ValueError("This TON network is not enabled")
    if not expected_domain or "." not in expected_domain or len(expected_domain.encode("utf-8")) > 128:
        raise ValueError("TON Connect domain is not configured correctly")
    if not isinstance(proof, dict):
        raise ValueError("Missing TON proof")
    timestamp = proof.get("timestamp")
    if isinstance(timestamp, bool):
        raise ValueError("Invalid proof timestamp")
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Invalid proof timestamp")
    current = int(time.time()) if now is None else int(now)
    if timestamp <= 0 or abs(current - timestamp) > PROOF_MAX_AGE_SECONDS:
        raise ValueError("TON proof has expired")
    domain = proof.get("domain")
    if not isinstance(domain, dict) or domain.get("value") != expected_domain:
        raise ValueError("TON proof domain mismatch")
    domain_bytes = expected_domain.encode("utf-8")
    if domain.get("lengthBytes") != len(domain_bytes):
        raise ValueError("TON proof domain length mismatch")
    payload = proof.get("payload")
    if not isinstance(payload, str) or not payload or len(payload.encode("utf-8")) > 128:
        raise ValueError("Invalid TON proof payload")
    signature_text = proof.get("signature")
    if not isinstance(signature_text, str) or len(signature_text) > 256:
        raise ValueError("Invalid TON proof signature")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid TON proof signature") from exc
    if len(signature) != 64:
        raise ValueError("Invalid TON proof signature")

    workchain, account_hash, raw_address = _parse_address(address)
    state_hash, public_key, code_hash = _parse_state_init(state_init_b64)
    if state_hash != account_hash:
        raise ValueError("Wallet StateInit does not derive the connected address")
    if public_key_hex is not None:
        if not isinstance(public_key_hex, str) or len(public_key_hex) != 64:
            raise ValueError("Invalid wallet public key")
        try:
            if bytes.fromhex(public_key_hex) != public_key:
                raise ValueError("Wallet public key does not match StateInit")
        except ValueError as exc:
            raise ValueError("Wallet public key does not match StateInit") from exc

    message = (
        b"ton-proof-item-v2/"
        + workchain.to_bytes(4, "big", signed=True)
        + account_hash
        + len(domain_bytes).to_bytes(4, "little")
        + domain_bytes
        + timestamp.to_bytes(8, "little", signed=False)
        + payload.encode("utf-8")
    )
    digest = hashlib.sha256(b"\xff\xffton-connect" + hashlib.sha256(message).digest()).digest()
    try:
        from nacl.signing import VerifyKey
        VerifyKey(public_key).verify(digest, signature)
    except Exception as exc:
        raise ValueError("TON proof signature verification failed") from exc

    return {
        "address": raw_address,
        "network": network,
        "public_key": public_key.hex(),
        "state_init_hash": state_hash.hex(),
        "payload": payload,
    }


def consume_challenge_and_link_wallet(
    verified: dict[str, str],
    telegram_id: str,
    session_token: str,
    now: int | None = None,
) -> bool:
    current = int(time.time()) if now is None else int(now)
    payload_hash = hashlib.sha256(verified["payload"].encode("utf-8")).hexdigest()
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    with _db() as conn:
        initial = conn.execute(
            "SELECT telegram_id, session_hash FROM ton_proof_challenges WHERE payload_hash = ?",
            (payload_hash,),
        ).fetchone()
        if initial is None:
            return False
        begin_write(
            conn,
            f"ton-session:{initial['telegram_id']}:{initial['session_hash']}",
            f"ton-challenge:{payload_hash}",
        )
        challenge = conn.execute(
            select_for_update(conn, """SELECT telegram_id, session_hash FROM ton_proof_challenges
               WHERE payload_hash = ? AND expires_at > ? AND consumed_at IS NULL"""),
            (payload_hash, current),
        ).fetchone()
        if challenge is None or challenge["telegram_id"] != str(telegram_id) or challenge["session_hash"] != session_hash:
            conn.rollback()
            return False
        updated = conn.execute(
            """UPDATE ton_proof_challenges SET consumed_at = ?
               WHERE payload_hash = ? AND consumed_at IS NULL AND expires_at > ?""",
            (current, payload_hash, current),
        )
        if updated.rowcount != 1:
            conn.rollback()
            return False
        conn.execute(
            """INSERT INTO verified_wallets
               (network, address, telegram_id, public_key, state_init_hash, created_at, last_verified_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(network, address) DO UPDATE SET
                 telegram_id=excluded.telegram_id,
                 public_key=excluded.public_key,
                 state_init_hash=excluded.state_init_hash,
                 last_verified_at=excluded.last_verified_at,
                 revoked_at=NULL""",
            (verified["network"], verified["address"], str(telegram_id), verified["public_key"],
             verified["state_init_hash"], current, current),
        )
        conn.commit()
    return True


def list_verified_wallets(telegram_id: str) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            """SELECT network, address, public_key, created_at, last_verified_at
               FROM verified_wallets WHERE telegram_id = ? AND revoked_at IS NULL
               ORDER BY last_verified_at DESC""",
            (str(telegram_id),),
        ).fetchall()
    return [dict(row) for row in rows]
