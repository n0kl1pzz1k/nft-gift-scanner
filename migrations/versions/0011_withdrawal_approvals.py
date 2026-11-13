"""Add multi-person approval and audit trail for withdrawal execution."""
from alembic import op
import sqlalchemy as sa

revision = "0011_withdrawal_approvals"
down_revision = "0010_marketplace_deal_settlement"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "withdrawal_approvals",
        sa.Column("approval_id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("withdrawal_requests.request_id"), nullable=False),
        sa.Column("admin_user_id", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("request_id", "admin_user_id", name="uq_withdrawal_approval_admin"),
    )
    op.create_index("idx_withdrawal_approvals_request", "withdrawal_approvals", ["request_id", "created_at"])
    op.create_table(
        "withdrawal_execution_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("withdrawal_requests.request_id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("event_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("idx_withdrawal_execution_events_request", "withdrawal_execution_events", ["request_id", "created_at"])


def downgrade():
    op.drop_index("idx_withdrawal_execution_events_request", table_name="withdrawal_execution_events")
    op.drop_table("withdrawal_execution_events")
    op.drop_index("idx_withdrawal_approvals_request", table_name="withdrawal_approvals")
    op.drop_table("withdrawal_approvals")
