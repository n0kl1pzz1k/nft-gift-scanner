"""Add an explicit reconciliation note for ambiguous Stars refunds."""
from alembic import op
import sqlalchemy as sa

revision = "0007_stars_refund_reconciliation"
down_revision = "0006_stars_refunds"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "telegram_stars_refund_requests",
        sa.Column("reconciliation_note", sa.String(500), nullable=True),
    )


def downgrade():
    op.drop_column("telegram_stars_refund_requests", "reconciliation_note")
