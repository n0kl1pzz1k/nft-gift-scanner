"""Add canonical inventory, ownership evidence, listings and reservation primitives."""
from alembic import op
import sqlalchemy as sa

revision = "0005_marketplace_inventory"
down_revision = "0004_telegram_stars_terms"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "marketplace_assets",
        sa.Column("asset_id", sa.String(64), primary_key=True),
        sa.Column("asset_type", sa.String(24), nullable=False),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("canonical_asset_id", sa.String(256), nullable=False, unique=True),
        sa.Column("collection_address", sa.String(128), nullable=False, server_default=""),
        sa.Column("token_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("telegram_gift_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("owner_user_id", sa.String(64), nullable=False),
        sa.Column("owner_wallet_address", sa.String(128)),
        sa.Column("ownership_state", sa.String(16), nullable=False, server_default="unverified"),
        sa.Column("ownership_checked_at", sa.BigInteger()),
        sa.Column("ownership_expires_at", sa.BigInteger()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("asset_type IN ('ton_nft', 'telegram_gift')", name="ck_marketplace_assets_type"),
        sa.CheckConstraint("network IN ('ton-mainnet', 'telegram')", name="ck_marketplace_assets_network"),
        sa.CheckConstraint("ownership_state IN ('unverified', 'verified', 'stale', 'revoked')", name="ck_marketplace_assets_ownership"),
        sa.CheckConstraint(
            "(ownership_state <> 'verified' OR (ownership_checked_at IS NOT NULL AND ownership_expires_at IS NOT NULL AND ownership_expires_at > ownership_checked_at))",
            name="ck_marketplace_assets_verified_window",
        ),
        sa.CheckConstraint(
            "(asset_type <> 'ton_nft' OR (network='ton-mainnet' AND collection_address <> '' AND token_id <> '')) AND "
            "(asset_type <> 'telegram_gift' OR (network='telegram' AND telegram_gift_id <> ''))",
            name="ck_marketplace_assets_identity",
        ),
    )
    op.create_index("idx_marketplace_assets_owner", "marketplace_assets", ["owner_user_id", "ownership_state"])

    op.create_table(
        "marketplace_ownership_proofs",
        sa.Column("proof_id", sa.String(64), primary_key=True),
        sa.Column("asset_id", sa.String(64), sa.ForeignKey("marketplace_assets.asset_id"), nullable=False),
        sa.Column("owner_user_id", sa.String(64), nullable=False),
        sa.Column("proof_source", sa.String(48), nullable=False),
        sa.Column("evidence_reference", sa.String(256), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("verification_status", sa.String(16), nullable=False),
        sa.Column("verified_at", sa.BigInteger()),
        sa.Column("expires_at", sa.BigInteger()),
        sa.Column("recorded_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("verification_status IN ('pending', 'verified', 'rejected', 'expired')", name="ck_marketplace_ownership_proofs_status"),
        sa.CheckConstraint(
            "(verification_status <> 'verified' OR (verified_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at > verified_at))",
            name="ck_marketplace_ownership_proofs_verified_window",
        ),
        sa.CheckConstraint("length(evidence_hash)=64", name="ck_marketplace_ownership_proofs_hash"),
    )
    op.create_index("idx_marketplace_ownership_proofs_asset", "marketplace_ownership_proofs", ["asset_id", "recorded_at"])

    op.create_table(
        "marketplace_listings",
        sa.Column("listing_id", sa.String(64), primary_key=True),
        sa.Column("asset_id", sa.String(64), sa.ForeignKey("marketplace_assets.asset_id"), nullable=False),
        sa.Column("seller_user_id", sa.String(64), nullable=False),
        sa.Column("price_raw", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("price_raw > 0", name="ck_marketplace_listings_positive_price"),
        sa.CheckConstraint("currency='XTR'", name="ck_marketplace_listings_currency_xtr"),
        sa.CheckConstraint("status IN ('draft', 'pending_verification', 'active', 'reserved', 'sold', 'cancelled', 'expired', 'rejected')", name="ck_marketplace_listings_status"),
    )
    op.create_index("idx_marketplace_listings_status_created", "marketplace_listings", ["status", "created_at"])
    op.create_index("uq_marketplace_listings_one_open_asset", "marketplace_listings", ["asset_id"], unique=True, postgresql_where=sa.text("status IN ('draft','pending_verification','active','reserved')"), sqlite_where=sa.text("status IN ('draft','pending_verification','active','reserved')"))

    op.create_table(
        "marketplace_listing_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("listing_id", sa.String(64), sa.ForeignKey("marketplace_listings.listing_id"), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("event_type IN ('created', 'verification_requested', 'verified', 'rejected', 'reserved', 'reservation_expired', 'cancelled', 'sold')", name="ck_marketplace_listing_events_type"),
    )
    op.create_index("idx_marketplace_listing_events_listing", "marketplace_listing_events", ["listing_id", "created_at"])

    op.create_table(
        "marketplace_reservations",
        sa.Column("reservation_id", sa.String(64), primary_key=True),
        sa.Column("listing_id", sa.String(64), sa.ForeignKey("marketplace_listings.listing_id"), nullable=False),
        sa.Column("buyer_user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'consumed', 'expired', 'cancelled')", name="ck_marketplace_reservations_status"),
        sa.CheckConstraint("expires_at > created_at", name="ck_marketplace_reservations_expiry"),
    )
    op.create_index("uq_marketplace_reservations_one_active_listing", "marketplace_reservations", ["listing_id"], unique=True, postgresql_where=sa.text("status='active'"), sqlite_where=sa.text("status='active'"))
    op.create_index("idx_marketplace_reservations_buyer", "marketplace_reservations", ["buyer_user_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("marketplace_ownership_proofs", "marketplace_listing_events"):
            op.execute(
                f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION gift_radar_reject_financial_mutation()"
            )
    elif bind.dialect.name == "sqlite":
        for table in ("marketplace_ownership_proofs", "marketplace_listing_events"):
            op.execute(
                f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'marketplace evidence and events are append-only'); END"
            )
            op.execute(
                f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'marketplace evidence and events are append-only'); END"
            )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("marketplace_ownership_proofs", "marketplace_listing_events"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    for table in ("marketplace_reservations", "marketplace_listing_events", "marketplace_listings",
                  "marketplace_ownership_proofs", "marketplace_assets"):
        op.drop_table(table)
