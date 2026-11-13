"""Create immutable ledger, payment invoices and unique chain events."""
from alembic import op
import sqlalchemy as sa
revision = "0001_financial_ledger"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("ledger_accounts",
        sa.Column("account_id",sa.String(160),primary_key=True),
        sa.Column("account_type",sa.String(32),nullable=False),
        sa.Column("currency",sa.String(16),nullable=False),
        sa.Column("created_at",sa.BigInteger(),nullable=False))
    op.create_table("ledger_entries",
        sa.Column("entry_id",sa.String(36),primary_key=True),
        sa.Column("idempotency_key",sa.String(200),nullable=False,unique=True),
        sa.Column("source_type",sa.String(40),nullable=False),
        sa.Column("source_reference",sa.String(200),nullable=False),
        sa.Column("payload_hash",sa.String(64),nullable=False),
        sa.Column("created_at",sa.BigInteger(),nullable=False))
    op.create_table("ledger_lines",
        sa.Column("line_id",sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column("entry_id",sa.String(36),sa.ForeignKey("ledger_entries.entry_id"),nullable=False),
        sa.Column("account_id",sa.String(160),sa.ForeignKey("ledger_accounts.account_id"),nullable=False),
        sa.Column("currency",sa.String(16),nullable=False),
        sa.Column("amount_raw",sa.String(80),nullable=False),
        sa.CheckConstraint("amount_raw != '0'",name="ck_ledger_line_nonzero"))
    op.create_table("payment_invoices",
        sa.Column("invoice_id",sa.String(64),primary_key=True),
        sa.Column("user_id",sa.String(64),nullable=False),
        sa.Column("asset_id",sa.String(32),nullable=False),
        sa.Column("chain",sa.String(32),nullable=False),
        sa.Column("master_address",sa.String(128),nullable=False),
        sa.Column("recipient_address",sa.String(128),nullable=False),
        sa.Column("amount_raw",sa.String(80),nullable=False),
        sa.Column("payload",sa.String(256),nullable=False,unique=True),
        sa.Column("expires_at",sa.BigInteger(),nullable=False),
        sa.Column("status",sa.String(24),nullable=False),
        sa.Column("created_at",sa.BigInteger(),nullable=False))
    op.create_table("payment_events",
        sa.Column("payment_event_id",sa.String(36),primary_key=True),
        sa.Column("invoice_id",sa.String(64),sa.ForeignKey("payment_invoices.invoice_id"),nullable=False,unique=True),
        sa.Column("chain",sa.String(32),nullable=False),
        sa.Column("tx_hash",sa.String(128),nullable=False),
        sa.Column("event_index",sa.BigInteger(),nullable=False),
        sa.Column("asset_id",sa.String(32),nullable=False),
        sa.Column("amount_raw",sa.String(80),nullable=False),
        sa.Column("recipient_address",sa.String(128),nullable=False),
        sa.Column("created_at",sa.BigInteger(),nullable=False),
        sa.UniqueConstraint("chain","tx_hash","event_index","asset_id",name="uq_payment_chain_event"))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION gift_radar_reject_financial_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'financial records are append-only'; END; $$ LANGUAGE plpgsql;""")
        for table in ("ledger_entries","ledger_lines","payment_events"):
            op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION gift_radar_reject_financial_mutation()")
    elif bind.dialect.name == "sqlite":
        for table in ("ledger_entries","ledger_lines","payment_events"):
            op.execute(f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'financial records are append-only'); END")
            op.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'financial records are append-only'); END")

def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("ledger_entries","ledger_lines","payment_events"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS gift_radar_reject_financial_mutation()")
    for table in ("payment_events","payment_invoices","ledger_lines","ledger_entries","ledger_accounts"):
        op.drop_table(table)
