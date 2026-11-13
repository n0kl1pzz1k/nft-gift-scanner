"""Add an atomic native marketplace deal and fulfillment state machine."""
from alembic import op
import sqlalchemy as sa

revision = "0010_marketplace_deal_settlement"
down_revision = "0009_seller_wallet_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "marketplace_deals",
        sa.Column("deal_id", sa.String(64), primary_key=True),
        sa.Column("listing_id", sa.String(64), sa.ForeignKey("marketplace_listings.listing_id"), nullable=False),
        sa.Column("reservation_id", sa.String(64), sa.ForeignKey("marketplace_reservations.reservation_id"), nullable=False),
        sa.Column("asset_id", sa.String(64), sa.ForeignKey("marketplace_assets.asset_id"), nullable=False),
        sa.Column("buyer_user_id", sa.String(64), nullable=False),
        sa.Column("seller_user_id", sa.String(64), nullable=False),
        sa.Column("buyer_wallet_address", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("price_raw", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payment_invoice_id", sa.String(64), unique=True),
        sa.Column("payment_event_id", sa.String(64), unique=True),
        sa.Column("nft_transfer_tx_hash", sa.String(128), unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("reservation_id", name="uq_marketplace_deal_reservation"),
        sa.CheckConstraint("price_raw > 0", name="ck_marketplace_deals_positive_price"),
        sa.CheckConstraint("currency IN ('GRAM','USDT','XTR')", name="ck_marketplace_deals_currency"),
        sa.CheckConstraint(
            "status IN ('reserved','payment_confirmed','nft_transfer_pending','settled','disputed','refunded','reconciliation_required')",
            name="ck_marketplace_deals_status",
        ),
    )
    op.create_index("idx_marketplace_deals_status_created", "marketplace_deals", ["status", "created_at"])
    op.create_table(
        "marketplace_deal_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("deal_id", sa.String(64), sa.ForeignKey("marketplace_deals.deal_id"), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("event_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("actor_type IN ('buyer','seller','system','admin')", name="ck_marketplace_deal_event_actor"),
    )
    op.create_index("idx_marketplace_deal_events_deal", "marketplace_deal_events", ["deal_id", "created_at"])


def downgrade():
    op.drop_index("idx_marketplace_deal_events_deal", table_name="marketplace_deal_events")
    op.drop_table("marketplace_deal_events")
    op.drop_index("idx_marketplace_deals_status_created", table_name="marketplace_deals")
    op.drop_table("marketplace_deals")
