"""Add auditable Telegram Stars refund requests with one-time execution state."""

from alembic import op
import sqlalchemy as sa

revision = "0006_stars_refunds"
down_revision = "0005_marketplace_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_stars_refund_requests",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(64), sa.ForeignKey("telegram_stars_invoices.invoice_id"), nullable=False, unique=True),
        sa.Column("buyer_user_id", sa.String(64), nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(256), nullable=False, unique=True),
        sa.Column("amount_stars", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="requested"),
        sa.Column("requested_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("processed_by", sa.String(64)),
        sa.Column("telegram_response_ref", sa.String(256)),
        sa.CheckConstraint("amount_stars > 0", name="ck_telegram_stars_refund_request_amount"),
        sa.CheckConstraint(
            "status IN ('requested', 'processing', 'succeeded', 'failed', 'reconciliation_required')",
            name="ck_telegram_stars_refund_request_status",
        ),
    )
    op.create_index(
        "idx_telegram_stars_refund_requests_status",
        "telegram_stars_refund_requests",
        ["status", "requested_at"],
    )


def downgrade():
    op.drop_index("idx_telegram_stars_refund_requests_status", table_name="telegram_stars_refund_requests")
    op.drop_table("telegram_stars_refund_requests")
