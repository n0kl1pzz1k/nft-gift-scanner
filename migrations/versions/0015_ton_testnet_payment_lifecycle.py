"""Add TON testnet inventory and independent payout/refund lifecycle evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0015_ton_testnet_payment_lifecycle"
down_revision = "0014_ownership_proof_index"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("verified_wallets") as batch:
        batch.drop_constraint("ck_verified_wallet_network", type_="check")
        batch.create_check_constraint(
            "ck_verified_wallet_network",
            "network IN ('ton-mainnet','ton-testnet')",
        )

    with op.batch_alter_table("marketplace_assets") as batch:
        batch.add_column(sa.Column("escrow_contract_address", sa.String(128), nullable=True))
        batch.add_column(sa.Column("item_address", sa.String(128), nullable=True))
        batch.add_column(sa.Column("seller_wallet_address", sa.String(128), nullable=True))
        batch.drop_constraint("ck_marketplace_assets_type", type_="check")
        batch.drop_constraint("ck_marketplace_assets_network", type_="check")
        batch.drop_constraint("ck_marketplace_assets_identity", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_assets_type",
            "asset_type IN ('ton_nft','testnet_mock_nft','telegram_gift','collectible_username','collectible_phone_number')",
        )
        batch.create_check_constraint(
            "ck_marketplace_assets_network",
            "network IN ('ton-mainnet','ton-testnet','telegram')",
        )
        batch.create_check_constraint(
            "ck_marketplace_assets_identity",
            "(asset_type IN ('ton_nft','collectible_username','collectible_phone_number') AND network='ton-mainnet' AND collection_address<>'' AND token_id<>'') "
            "OR (asset_type='testnet_mock_nft' AND network='ton-testnet' AND collection_address<>'' AND token_id<>'') "
            "OR (asset_type='telegram_gift' AND network='telegram' AND telegram_gift_id<>'')",
        )

    with op.batch_alter_table("marketplace_listings") as batch:
        batch.drop_constraint("ck_marketplace_listings_currency", type_="check")
        batch.drop_constraint("ck_marketplace_listings_status", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_listings_currency",
            "currency IN ('XTR','GRAM','USDT','tUSDT')",
        )
        batch.create_check_constraint(
            "ck_marketplace_listings_status",
            "status IN ('draft','pending_verification','active','reserved','payout_pending','sold','cancelled','expired','rejected')",
        )

    with op.batch_alter_table("marketplace_deals") as batch:
        batch.add_column(sa.Column("seller_wallet_address", sa.String(128), nullable=True))
        batch.add_column(sa.Column("escrow_contract_address", sa.String(128), nullable=True))
        batch.add_column(sa.Column("network", sa.String(16), nullable=False, server_default="ton-mainnet"))
        batch.add_column(sa.Column("payment_query_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("payment_reference", sa.String(128), nullable=True))
        batch.add_column(sa.Column("payment_network", sa.String(16), nullable=True))
        batch.add_column(sa.Column("nft_return_tx_hash", sa.String(128), nullable=True))
        batch.add_column(sa.Column("payout_query_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("payout_tx_hash", sa.String(128), nullable=True))
        batch.add_column(sa.Column("payout_verified_at", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("refund_query_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("refund_tx_hash", sa.String(128), nullable=True))
        batch.add_column(sa.Column("refund_verified_at", sa.BigInteger(), nullable=True))
        batch.drop_constraint("ck_marketplace_deals_currency", type_="check")
        batch.drop_constraint("ck_marketplace_deals_status", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_deals_currency",
            "currency IN ('GRAM','USDT','tUSDT','XTR')",
        )
        batch.create_check_constraint(
            "ck_marketplace_deals_network",
            "network IN ('ton-mainnet','ton-testnet')",
        )
        batch.create_check_constraint(
            "ck_marketplace_deals_status",
            "status IN ('reserved','payment_confirmed','nft_transfer_pending','payout_pending','refund_pending','settled','disputed','refunded','reconciliation_required','expired')",
        )

    op.execute(
        "UPDATE marketplace_assets SET seller_wallet_address = owner_wallet_address "
        "WHERE seller_wallet_address IS NULL AND custody_state = 'wallet_owned'"
    )
    op.execute(
        "UPDATE marketplace_assets SET item_address = ("
        "SELECT evidence_reference FROM marketplace_ownership_proofs "
        "WHERE marketplace_ownership_proofs.asset_id = marketplace_assets.asset_id "
        "AND marketplace_ownership_proofs.verification_status = 'verified' "
        "ORDER BY recorded_at DESC LIMIT 1) "
        "WHERE item_address IS NULL"
    )
    op.create_index("uq_marketplace_deals_payment_reference", "marketplace_deals", ["payment_reference"], unique=True)
    op.create_index("uq_marketplace_deals_nft_return_tx_hash", "marketplace_deals", ["nft_return_tx_hash"], unique=True)
    op.create_index("uq_marketplace_deals_payout_tx_hash", "marketplace_deals", ["payout_tx_hash"], unique=True)
    op.create_index("uq_marketplace_deals_refund_tx_hash", "marketplace_deals", ["refund_tx_hash"], unique=True)
    op.create_index("idx_marketplace_deals_network_status", "marketplace_deals", ["network", "status"])


def downgrade():
    op.drop_index("idx_marketplace_deals_network_status", table_name="marketplace_deals")
    op.drop_index("uq_marketplace_deals_refund_tx_hash", table_name="marketplace_deals")
    op.drop_index("uq_marketplace_deals_payout_tx_hash", table_name="marketplace_deals")
    op.drop_index("uq_marketplace_deals_nft_return_tx_hash", table_name="marketplace_deals")
    op.drop_index("uq_marketplace_deals_payment_reference", table_name="marketplace_deals")

    with op.batch_alter_table("marketplace_deals") as batch:
        batch.drop_constraint("ck_marketplace_deals_network", type_="check")
        batch.drop_constraint("ck_marketplace_deals_currency", type_="check")
        batch.drop_constraint("ck_marketplace_deals_status", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_deals_currency",
            "currency IN ('GRAM','USDT','XTR')",
        )
        batch.create_check_constraint(
            "ck_marketplace_deals_status",
            "status IN ('reserved','payment_confirmed','nft_transfer_pending','settled','disputed','refunded','reconciliation_required','expired')",
        )
        for name in (
            "refund_verified_at", "refund_tx_hash", "refund_query_id",
            "payout_verified_at", "payout_tx_hash", "payout_query_id", "nft_return_tx_hash",
            "payment_network", "payment_reference", "payment_query_id",
            "network", "escrow_contract_address", "seller_wallet_address",
        ):
            batch.drop_column(name)

    with op.batch_alter_table("marketplace_listings") as batch:
        batch.drop_constraint("ck_marketplace_listings_currency", type_="check")
        batch.drop_constraint("ck_marketplace_listings_status", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_listings_currency",
            "currency IN ('XTR','GRAM','USDT')",
        )
        batch.create_check_constraint(
            "ck_marketplace_listings_status",
            "status IN ('draft','pending_verification','active','reserved','sold','cancelled','expired','rejected')",
        )

    with op.batch_alter_table("marketplace_assets") as batch:
        batch.drop_constraint("ck_marketplace_assets_type", type_="check")
        batch.drop_constraint("ck_marketplace_assets_network", type_="check")
        batch.drop_constraint("ck_marketplace_assets_identity", type_="check")
        batch.create_check_constraint(
            "ck_marketplace_assets_type",
            "asset_type IN ('ton_nft','telegram_gift','collectible_username','collectible_phone_number')",
        )
        batch.create_check_constraint(
            "ck_marketplace_assets_network",
            "network IN ('ton-mainnet','telegram')",
        )
        batch.create_check_constraint(
            "ck_marketplace_assets_identity",
            "(asset_type IN ('ton_nft','collectible_username','collectible_phone_number') AND network='ton-mainnet' AND collection_address<>'' AND token_id<>'') "
            "OR (asset_type='telegram_gift' AND network='telegram' AND telegram_gift_id<>'')",
        )
        batch.drop_column("seller_wallet_address")
        batch.drop_column("item_address")
        batch.drop_column("escrow_contract_address")

    with op.batch_alter_table("verified_wallets") as batch:
        batch.drop_constraint("ck_verified_wallet_network", type_="check")
        batch.create_check_constraint("ck_verified_wallet_network", "network='ton-mainnet'")
