"""Record explicit acceptance of the current Telegram Stars terms version."""
from alembic import op
import sqlalchemy as sa

revision = "0004_telegram_stars_terms"
down_revision = "0003_telegram_stars_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_stars_terms_acceptances",
        sa.Column("acceptance_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("terms_version", sa.String(64), nullable=False),
        sa.Column("terms_url", sa.String(2048), nullable=False),
        sa.Column("accepted_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "terms_version", name="uq_telegram_stars_terms_user_version"),
        sa.CheckConstraint("length(terms_version) BETWEEN 1 AND 64", name="ck_telegram_stars_terms_version"),
        sa.CheckConstraint("terms_url LIKE 'https://%'", name="ck_telegram_stars_terms_https"),
    )
    op.create_index(
        "idx_telegram_stars_terms_user_accepted",
        "telegram_stars_terms_acceptances",
        ["user_id", "accepted_at"],
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER telegram_stars_terms_append_only "
            "BEFORE UPDATE OR DELETE ON telegram_stars_terms_acceptances "
            "FOR EACH ROW EXECUTE FUNCTION gift_radar_reject_financial_mutation()"
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER telegram_stars_terms_no_update BEFORE UPDATE ON telegram_stars_terms_acceptances "
            "BEGIN SELECT RAISE(ABORT, 'terms acceptances are append-only'); END"
        )
        op.execute(
            "CREATE TRIGGER telegram_stars_terms_no_delete BEFORE DELETE ON telegram_stars_terms_acceptances "
            "BEGIN SELECT RAISE(ABORT, 'terms acceptances are append-only'); END"
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS telegram_stars_terms_append_only ON telegram_stars_terms_acceptances")
    op.drop_index("idx_telegram_stars_terms_user_accepted", table_name="telegram_stars_terms_acceptances")
    op.drop_table("telegram_stars_terms_acceptances")
