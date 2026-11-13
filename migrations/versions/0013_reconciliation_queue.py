"""Index the admin manual reconciliation queue."""
from alembic import op

revision = "0013_reconciliation_queue"
down_revision = "0012_expire_unpaid_deals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_marketplace_deals_status_updated",
        "marketplace_deals",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_marketplace_deal_events_deal_created",
        "marketplace_deal_events",
        ["deal_id", "created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "idx_marketplace_deal_events_deal_created",
        table_name="marketplace_deal_events",
    )
    op.drop_index(
        "idx_marketplace_deals_status_updated",
        table_name="marketplace_deals",
    )
