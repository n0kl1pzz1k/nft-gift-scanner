"""Allow unpaid marketplace deals to expire without financial settlement."""
from alembic import op

revision = "0012_expire_unpaid_marketplace_deals"
down_revision = "0011_withdrawal_approvals"
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode keeps this migration compatible with SQLite CI and PostgreSQL.
    with op.batch_alter_table("marketplace_deals") as batch_op:
        batch_op.drop_constraint("ck_marketplace_deals_status", type_="check")
        batch_op.create_check_constraint(
            "ck_marketplace_deals_status",
            "status IN ('reserved','payment_confirmed','nft_transfer_pending','settled','disputed','refunded','reconciliation_required','expired')",
        )


def downgrade():
    op.execute(
        "UPDATE marketplace_deals SET status='reserved' WHERE status='expired'"
    )
    with op.batch_alter_table("marketplace_deals") as batch_op:
        batch_op.drop_constraint("ck_marketplace_deals_status", type_="check")
        batch_op.create_check_constraint(
            "ck_marketplace_deals_status",
            "status IN ('reserved','payment_confirmed','nft_transfer_pending','settled','disputed','refunded','reconciliation_required')",
        )
