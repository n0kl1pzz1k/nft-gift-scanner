"""Add Telegram Stars invoice, payment and refund-event storage."""
from alembic import op
import sqlalchemy as sa

revision = "0003_telegram_stars_payments"
down_revision = "0002_marketplace_core"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_stars_invoices",
        sa.Column("invoice_id", sa.String(64), primary_key=True),
        sa.Column("payload", sa.String(128), nullable=False, unique=True),
        sa.Column("deal_id", sa.String(64), nullable=False),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("buyer_user_id", sa.String(64), nullable=False),
        sa.Column("seller_user_id", sa.String(64), nullable=False),
        sa.Column("amount_stars", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("pre_checkout_query_id", sa.String(128), unique=True),
        sa.Column("pre_checkout_approved_at", sa.BigInteger()),
        sa.Column("paid_at", sa.BigInteger()),
        sa.Column("telegram_payment_charge_id", sa.String(256), unique=True),
        sa.Column("provider_payment_charge_id", sa.String(256)),
        sa.Column("refunded_at", sa.BigInteger()),
        sa.CheckConstraint("amount_stars > 0", name="ck_telegram_stars_invoice_amount"),
        sa.CheckConstraint("currency = 'XTR'", name="ck_telegram_stars_invoice_currency"),
        sa.CheckConstraint(
            "status IN ('pending', 'pre_checkout_approved', 'paid', 'expired', 'refunded')",
            name="ck_telegram_stars_invoice_status",
        ),
    )
    op.create_index(
        "uq_telegram_stars_open_invoice_per_deal",
        "telegram_stars_invoices",
        ["deal_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'pre_checkout_approved')"),
        sqlite_where=sa.text("status IN ('pending', 'pre_checkout_approved')"),
    )
    op.create_index("idx_telegram_stars_invoice_buyer", "telegram_stars_invoices", ["buyer_user_id", "created_at"])

    op.create_table(
        "telegram_stars_payments",
        sa.Column("payment_event_id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(64), sa.ForeignKey("telegram_stars_invoices.invoice_id"), nullable=False, unique=True),
        sa.Column("telegram_payment_charge_id", sa.String(256), nullable=False, unique=True),
        sa.Column("buyer_user_id", sa.String(64), nullable=False),
        sa.Column("amount_stars", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("amount_stars > 0", name="ck_telegram_stars_payment_amount"),
        sa.CheckConstraint("currency = 'XTR'", name="ck_telegram_stars_payment_currency"),
    )

    op.create_table(
        "telegram_stars_refund_events",
        sa.Column("refund_event_id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(64), sa.ForeignKey("telegram_stars_invoices.invoice_id"), nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(256), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('refund_requested', 'refund_succeeded', 'refund_failed')",
            name="ck_telegram_stars_refund_event_type",
        ),
    )
    op.create_index("idx_telegram_stars_refunds_invoice", "telegram_stars_refund_events", ["invoice_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION gift_radar_reject_stars_event_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Telegram Stars payment events are immutable'; END;
        $$ LANGUAGE plpgsql;
        """)
        for table in ("telegram_stars_payments", "telegram_stars_refund_events"):
            op.execute(
                f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION gift_radar_reject_stars_event_mutation()"
            )
    elif bind.dialect.name == "sqlite":
        for table in ("telegram_stars_payments", "telegram_stars_refund_events"):
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'append-only Telegram Stars event'); END"
                )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("telegram_stars_payments", "telegram_stars_refund_events"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS gift_radar_reject_stars_event_mutation()")
    elif bind.dialect.name == "sqlite":
        for table in ("telegram_stars_payments", "telegram_stars_refund_events"):
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}")
    op.drop_table("telegram_stars_refund_events")
    op.drop_table("telegram_stars_payments")
    op.drop_index("idx_telegram_stars_invoice_buyer", table_name="telegram_stars_invoices")
    op.drop_index("uq_telegram_stars_open_invoice_per_deal", table_name="telegram_stars_invoices")
    op.drop_table("telegram_stars_invoices")
