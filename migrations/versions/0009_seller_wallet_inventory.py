"""Persist server-verified TON wallet bindings for seller inventory."""
from alembic import op
import sqlalchemy as sa

revision = "0009_seller_wallet_inventory"
down_revision = "0008_chain_withdrawal_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "verified_wallets",
        sa.Column("wallet_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column("verified_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger()),
        sa.UniqueConstraint("user_id", "network", "address", name="uq_verified_wallet_user_address"),
        sa.CheckConstraint("network='ton-mainnet'", name="ck_verified_wallet_network"),
    )
    op.create_index(
        "idx_verified_wallet_active", "verified_wallets", ["user_id", "revoked_at"]
    )


def downgrade():
    op.drop_index("idx_verified_wallet_active", table_name="verified_wallets")
    op.drop_table("verified_wallets")
