"""Allow unpaid marketplace deals to expire without financial settlement."""
from alembic import op

revision = "0012_expire_unpaid_marketplace_deals"
down_revision = "0011_withdrawal_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "ck_marketplace_deals_status", "marketplace_deals", type_="check"
    )
    op.create_check_constraint(
        "ck_marketplace_deals_status",
        "marketplace_deals",
        "status IN ('reserved','payment_confirmed','nft_transfer_pending','settled','disputed','refunded','reconciliation_required','expired')",
    )


def downgrade():
    op.execute(
        "UPDATE marketplace_deals SET status='reserved' WHERE status='expired'"
    )
    op.drop_constraint(
        "ck_marketplace_deals_status", "marketplace_deals", type_="check"
    )
    op.create_check_constraint(
        "ck_marketplace_deals_status",
        "marketplace_deals",
        "status IN ('reserved','payment_confirmed','nft_transfer_pending','settled','disputed','refunded','reconciliation_required')",
    )
