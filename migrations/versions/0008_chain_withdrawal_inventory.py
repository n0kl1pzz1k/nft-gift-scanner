"""Add native TON asset identity, chain observations, withdrawals and collectible NFT types."""
from alembic import op
import sqlalchemy as sa

revision = "0008_chain_withdrawal_inventory"
down_revision = "0007_stars_refund_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_invoices", sa.Column(
        "asset_kind", sa.String(16), nullable=False, server_default="jetton"
    ))
    with op.batch_alter_table("payment_invoices", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_payment_invoices_asset_kind", "asset_kind IN ('native', 'jetton')"
        )

    op.create_table(
        "ton_chain_observations",
        sa.Column("observation_id", sa.String(36), primary_key=True),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("tx_hash", sa.String(128), nullable=False),
        sa.Column("event_index", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("finalized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("observed_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "network", "tx_hash", "event_index", "asset_id", "provider_id",
            name="uq_ton_observation_provider_event"
        ),
        sa.CheckConstraint("network='ton-mainnet'", name="ck_ton_observation_network"),
        sa.CheckConstraint("length(evidence_hash)=64", name="ck_ton_observation_hash"),
    )
    op.create_index(
        "idx_ton_observations_event", "ton_chain_observations",
        ["network", "tx_hash", "event_index", "asset_id"]
    )

    op.create_table(
        "withdrawal_requests",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.String(32), nullable=False),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("destination_address", sa.String(128), nullable=False),
        sa.Column("amount_raw", sa.String(80), nullable=False),
        sa.Column("fee_raw", sa.String(80), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("tx_hash", sa.String(128), unique=True),
        sa.Column("failure_reason", sa.String(500)),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("network='ton-mainnet'", name="ck_withdrawal_network"),
        sa.CheckConstraint("CAST(amount_raw AS BIGINT) > 0", name="ck_withdrawal_positive_amount"),
        sa.CheckConstraint("CAST(fee_raw AS BIGINT) >= 0", name="ck_withdrawal_nonnegative_fee"),
        sa.CheckConstraint(
            "status IN ('requested','processing','submitted','confirmed','failed','reconciliation_required','cancelled')",
            name="ck_withdrawal_status"
        ),
    )
    op.create_index(
        "idx_withdrawal_user_status", "withdrawal_requests",
        ["user_id", "status", "created_at"]
    )

    with op.batch_alter_table("marketplace_assets", recreate="always") as batch:
        batch.drop_constraint("ck_marketplace_assets_type", type_="check")
        batch.drop_constraint("ck_marketplace_assets_identity", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_assets_type",
            "asset_type IN ('ton_nft','telegram_gift','collectible_username','collectible_phone_number')"
        )
        batch.create_check_constraint(
            "ck_marketplace_assets_identity",
            "(asset_type IN ('ton_nft','collectible_username','collectible_phone_number') "
            "AND network='ton-mainnet' AND collection_address <> '' AND token_id <> '') OR "
            "(asset_type='telegram_gift' AND network='telegram' AND telegram_gift_id <> '')"
        )

    with op.batch_alter_table("marketplace_listings", recreate="always") as batch:
        batch.drop_constraint("ck_marketplace_listings_currency_xtr", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_listings_currency", "currency IN ('XTR','GRAM','USDT')"
        )

    with op.batch_alter_table("marketplace_assets", recreate="always") as batch:
        batch.add_column(sa.Column(
            "custody_state", sa.String(24), nullable=False, server_default="wallet_owned"
        ))
        batch.add_column(sa.Column("deposit_tx_hash", sa.String(128)))
        batch.add_column(sa.Column("deposit_event_index", sa.BigInteger()))
        batch.add_column(sa.Column("custody_verified_at", sa.BigInteger()))
        batch.create_check_constraint(
            "ck_marketplace_assets_custody_state",
            "custody_state IN ('wallet_owned','escrow_pending','escrowed','transferred','telegram_profile_only','reconciliation_required')"
        )


def downgrade():
    with op.batch_alter_table("marketplace_assets", recreate="always") as batch:
        batch.drop_constraint("ck_marketplace_assets_custody_state", type_="check")
        batch.drop_column("custody_verified_at")
        batch.drop_column("deposit_event_index")
        batch.drop_column("deposit_tx_hash")
        batch.drop_column("custody_state")

    with op.batch_alter_table("marketplace_listings", recreate="always") as batch:
        batch.drop_constraint("ck_marketplace_listings_currency", type_="check")
        batch.create_check_constraint("ck_marketplace_listings_currency_xtr", "currency='XTR'")

    with op.batch_alter_table("marketplace_assets", recreate="always") as batch:
        batch.drop_constraint("ck_marketplace_assets_identity", type_="check")
        batch.drop_constraint("ck_marketplace_assets_type", type_="check")
        batch.create_check_constraint("ck_marketplace_assets_type", "asset_type IN ('ton_nft','telegram_gift')")
        batch.create_check_constraint(
            "ck_marketplace_assets_identity",
            "(asset_type <> 'ton_nft' OR (network='ton-mainnet' AND collection_address <> '' AND token_id <> '')) AND "
            "(asset_type <> 'telegram_gift' OR (network='telegram' AND telegram_gift_id <> ''))"
        )
    op.drop_index("idx_withdrawal_user_status", table_name="withdrawal_requests")
    op.drop_table("withdrawal_requests")
    op.drop_index("idx_ton_observations_event", table_name="ton_chain_observations")
    op.drop_table("ton_chain_observations")
    with op.batch_alter_table("payment_invoices", recreate="always") as batch:
        batch.drop_constraint("ck_payment_invoices_asset_kind", type_="check")
        batch.drop_column("asset_kind")
