"""Index ownership proof history for bounded reconciliation lookups."""
from alembic import op

revision = "0014_ownership_proof_index"
down_revision = "0013_reconciliation_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "idx_marketplace_ownership_proofs_asset_source_recorded",
        "marketplace_ownership_proofs",
        ["asset_id", "proof_source", "recorded_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "idx_marketplace_ownership_proofs_asset_source_recorded",
        table_name="marketplace_ownership_proofs",
    )
