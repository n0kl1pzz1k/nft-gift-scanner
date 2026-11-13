"""Create PostgreSQL marketplace, offers, notification and catalogue tables."""
from alembic import op
import sqlalchemy as sa

revision = "0002_marketplace_core"
down_revision = "0001_financial_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("telegram_users",
        sa.Column("telegram_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, server_default=""),
        sa.Column("first_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("photo_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("language_code", sa.String(16), nullable=False, server_default=""),
        sa.Column("is_premium", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False))
    op.create_index("idx_telegram_users_updated", "telegram_users", ["updated_at"])

    op.create_table("user_profiles",
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("telegram_id", sa.String(64), sa.ForeignKey("telegram_users.telegram_id"), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False))
    op.create_index("idx_user_profiles_updated", "user_profiles", ["updated_at"])

    op.create_table("telegram_sessions",
        sa.Column("token_hash", sa.String(128), primary_key=True),
        sa.Column("telegram_id", sa.String(64), sa.ForeignKey("telegram_users.telegram_id"), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger()))
    op.create_index("idx_telegram_sessions_user", "telegram_sessions", ["telegram_id", "expires_at"])

    op.create_table("telegram_initdata_uses",
        sa.Column("initdata_hash", sa.String(64), primary_key=True),
        sa.Column("telegram_id", sa.String(64), nullable=False),
        sa.Column("consumed_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False))
    op.create_index("idx_telegram_initdata_expiry", "telegram_initdata_uses", ["expires_at"])

    op.create_table("telegram_update_inbox",
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("received_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.BigInteger(), nullable=False),
        sa.Column("processed_at", sa.BigInteger()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("status IN ('processing', 'processed')", name="ck_telegram_update_inbox_status"))
    op.create_index("idx_telegram_update_inbox_status", "telegram_update_inbox", ["status", "lease_until"])

    op.create_table("user_favorites",
        sa.Column("telegram_id", sa.String(64), nullable=False),
        sa.Column("gift_id", sa.String(160), nullable=False),
        sa.Column("gift_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("telegram_id", "gift_id"))
    op.create_index("idx_user_favorites_updated", "user_favorites", ["telegram_id", "updated_at"])

    op.create_table("demo_orders",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("owner_name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("price", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint("kind IN ('listing', 'buy-order')", name="ck_demo_orders_kind"),
        sa.CheckConstraint("currency IN ('TON', 'XTR')", name="ck_demo_orders_currency"),
        sa.CheckConstraint("status IN ('active', 'matched', 'cancelled')", name="ck_demo_orders_status"))
    op.create_index("idx_demo_orders_status_created", "demo_orders", ["status", "created_at"])
    op.create_index("idx_demo_orders_user_created", "demo_orders", ["user_id", "created_at"])

    op.create_table("demo_order_events",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("event_type IN ('created', 'simulated_fill', 'cancelled')", name="ck_demo_order_events_type"))
    op.create_index("idx_demo_order_events_order", "demo_order_events", ["order_id", "created_at"])

    op.create_table("demo_order_revisions",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("changed_fields", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.BigInteger(), nullable=False))
    op.create_index("idx_demo_order_revisions_order", "demo_order_revisions", ["order_id", "created_at"])

    op.create_table("demo_order_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("reporter_user_id", sa.String(64), nullable=False),
        sa.Column("reporter_name", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("details", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at", sa.BigInteger()),
        sa.Column("resolved_by", sa.String(64)),
        sa.UniqueConstraint("order_id", "reporter_user_id", name="uq_demo_order_reporter"),
        sa.CheckConstraint("reason IN ('scam', 'misleading', 'inappropriate', 'spam', 'other')", name="ck_demo_order_reports_reason"),
        sa.CheckConstraint("status IN ('open', 'dismissed', 'removed')", name="ck_demo_order_reports_status"))
    op.create_index("idx_demo_order_reports_status_created", "demo_order_reports", ["status", "created_at"])

    op.create_table("api_rate_limits",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "action"))

    op.create_table("app_settings",
        sa.Column("setting_key", sa.String(100), primary_key=True),
        sa.Column("setting_value", sa.String(500), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False))

    op.create_table("demo_offers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("sender_user_id", sa.String(64), nullable=False),
        sa.Column("sender_name", sa.String(128), nullable=False),
        sa.Column("amount", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("currency IN ('TON', 'XTR')", name="ck_demo_offers_currency"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'declined', 'withdrawn')", name="ck_demo_offers_status"))
    op.create_index("idx_demo_offers_order", "demo_offers", ["order_id", "created_at"])
    op.create_index("idx_demo_offers_sender", "demo_offers", ["sender_user_id", "created_at"])
    op.create_index("idx_demo_offers_status", "demo_offers", ["status", "created_at"])

    op.create_table("demo_deals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("offer_id", sa.String(64), sa.ForeignKey("demo_offers.id"), nullable=False, unique=True),
        sa.Column("listing_owner_id", sa.String(64), nullable=False),
        sa.Column("offer_sender_id", sa.String(64), nullable=False),
        sa.Column("agreed_amount", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("is_real", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="payment_not_enabled"),
        sa.CheckConstraint("currency IN ('TON', 'XTR')", name="ck_demo_deals_currency"),
        sa.CheckConstraint("is_real IN (0,1)", name="ck_demo_deals_is_real"),
        sa.CheckConstraint("status IN ('payment_not_enabled', 'awaiting_payment', 'payment_pending', 'paid', 'delivery_pending', 'completed', 'disputed', 'cancelled', 'expired')", name="ck_demo_deals_status"))
    op.create_index("idx_demo_deals_one_open_reservation", "demo_deals", ["order_id"], unique=True,
                    postgresql_where=sa.text("status NOT IN ('cancelled', 'expired')"),
                    sqlite_where=sa.text("status NOT IN ('cancelled', 'expired')"))

    bind = op.get_bind()
    deal_event_sequence = (sa.Column("sequence", sa.Integer(), sa.Identity(), nullable=False, unique=True)
                           if bind.dialect.name == "postgresql" else
                           sa.Column("sequence", sa.Integer(), nullable=True, unique=True))

    op.create_table("demo_deal_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("deal_id", sa.String(64), sa.ForeignKey("demo_deals.id"), nullable=False),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        deal_event_sequence,
        sa.CheckConstraint("event_type IN ('deal_created', 'listing_reserved', 'payment_not_enabled')", name="ck_demo_deal_events_type"))

    op.create_table("demo_deal_lifecycle_events",
        sa.Column("sequence", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.String(64), nullable=False, unique=True),
        sa.Column("deal_id", sa.String(64), sa.ForeignKey("demo_deals.id"), nullable=False),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("event_type IN ('deal_cancelled')", name="ck_demo_deal_lifecycle_events_type"))
    op.create_index("idx_demo_deal_lifecycle_events_deal", "demo_deal_lifecycle_events", ["deal_id", "sequence"])

    op.create_table("demo_notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("offer_id", sa.String(64), sa.ForeignKey("demo_offers.id"), nullable=False),
        sa.Column("order_id", sa.String(64), sa.ForeignKey("demo_orders.id"), nullable=False),
        sa.Column("order_title", sa.String(256), nullable=False, server_default=""),
        sa.Column("actor_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("read_at", sa.BigInteger()),
        sa.CheckConstraint("event_type IN ('offer_received', 'offer_accepted', 'offer_declined', 'offer_withdrawn')", name="ck_demo_notifications_type"))
    op.create_index("idx_demo_notifications_user_created", "demo_notifications", ["user_id", "created_at"])
    op.create_index("idx_demo_notifications_user_unread", "demo_notifications", ["user_id", "read_at", "created_at"])

    op.create_table("catalogue_gifts",
        sa.Column("gift_id", sa.String(160), primary_key=True),
        sa.Column("gift_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="Portals"),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False))
    op.create_index("idx_catalogue_gifts_last_seen", "catalogue_gifts", ["last_seen_at"])

    op.create_table("catalogue_gift_snapshots",
        sa.Column("gift_id", sa.String(160), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("gift_json", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("gift_id", "fingerprint"))
    op.create_index("idx_catalogue_snapshots_gift_time", "catalogue_gift_snapshots", ["gift_id", "observed_at"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION gift_radar_reject_marketplace_event_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'marketplace event records are immutable'; END;
        $$ LANGUAGE plpgsql;
        """)
        for table in ("demo_deal_events", "demo_deal_lifecycle_events"):
            op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION gift_radar_reject_marketplace_event_mutation()")

        op.execute("""
        CREATE OR REPLACE FUNCTION gift_radar_offer_notifications() RETURNS trigger AS $$
        BEGIN
          IF current_setting('gift_radar.migrating', true) = 'on' THEN RETURN NEW; END IF;
          IF TG_OP = 'INSERT' THEN
            INSERT INTO demo_notifications(id,user_id,event_type,offer_id,order_id,order_title,actor_name,created_at)
            SELECT 'nt-' || substr(md5(random()::text || clock_timestamp()::text),1,16),
                   o.user_id,'offer_received',NEW.id,NEW.order_id,o.title,NEW.sender_name,NEW.created_at
            FROM demo_orders o WHERE o.id=NEW.order_id AND o.user_id<>NEW.sender_user_id;
          ELSIF TG_OP = 'UPDATE' AND OLD.status='pending' AND NEW.status<>OLD.status
                AND NEW.status IN ('accepted','declined','withdrawn') THEN
            INSERT INTO demo_notifications(id,user_id,event_type,offer_id,order_id,order_title,actor_name,created_at)
            SELECT 'nt-' || substr(md5(random()::text || clock_timestamp()::text),1,16),
                   CASE WHEN NEW.status='withdrawn' THEN o.user_id ELSE NEW.sender_user_id END,
                   CASE NEW.status WHEN 'accepted' THEN 'offer_accepted'
                     WHEN 'declined' THEN 'offer_declined' WHEN 'withdrawn' THEN 'offer_withdrawn' END,
                   NEW.id,NEW.order_id,o.title,
                   CASE WHEN NEW.status='withdrawn' THEN NEW.sender_name ELSE o.owner_name END,
                   NEW.updated_at
            FROM demo_orders o
            WHERE o.id=NEW.order_id AND (NEW.status<>'withdrawn' OR o.user_id<>NEW.sender_user_id);
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER demo_offers_notifications_insert AFTER INSERT ON demo_offers
          FOR EACH ROW EXECUTE FUNCTION gift_radar_offer_notifications();
        CREATE TRIGGER demo_offers_notifications_update AFTER UPDATE OF status ON demo_offers
          FOR EACH ROW EXECUTE FUNCTION gift_radar_offer_notifications();

        CREATE OR REPLACE FUNCTION gift_radar_cancel_order_offers() RETURNS trigger AS $$
        BEGIN
          IF OLD.status='active' AND NEW.status='cancelled' THEN
            UPDATE demo_offers SET status='declined', updated_at=NEW.updated_at
            WHERE order_id=NEW.id AND status='pending';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER demo_orders_cancel_offers AFTER UPDATE OF status ON demo_orders
          FOR EACH ROW EXECUTE FUNCTION gift_radar_cancel_order_offers();
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS demo_orders_cancel_offers ON demo_orders")
        op.execute("DROP FUNCTION IF EXISTS gift_radar_cancel_order_offers()")
        op.execute("DROP TRIGGER IF EXISTS demo_offers_notifications_update ON demo_offers")
        op.execute("DROP TRIGGER IF EXISTS demo_offers_notifications_insert ON demo_offers")
        op.execute("DROP FUNCTION IF EXISTS gift_radar_offer_notifications()")
        for table in ("demo_deal_events", "demo_deal_lifecycle_events"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS gift_radar_reject_marketplace_event_mutation()")
    for table in ("catalogue_gift_snapshots","catalogue_gifts","demo_notifications","demo_deal_lifecycle_events",
                  "demo_deal_events","demo_deals","demo_offers","app_settings","api_rate_limits",
                  "demo_order_reports","demo_order_revisions","demo_order_events","demo_orders",
                  "user_favorites","telegram_update_inbox","telegram_initdata_uses","telegram_sessions",
                  "user_profiles","telegram_users"):
        op.drop_table(table)
