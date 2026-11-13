# GIFT RADAR — Telegram NFT Gift Marketplace

Telegram bot and bilingual Mini App for browsing public NFT gift marketplace listings, collections, gift metadata and market references.

## Current verified scope

- **Portals**: optional adapter for documented GET collection-preview, collection-floor and listed-gift search routes. The app exposes only GET requests and displays listing prices, collection floors and gift attributes; it does not implement purchases, sales, transfers or offers.
- **Tonnel**: preferred read-only listings adapter through the Swift Gifts Partner API when `SWIFTGIFTS_API_KEY` is configured. Without that key, the app falls back to the experimental public `POST /api/pageGifts` endpoint with empty `user_auth`; this fallback uses ordinary HTTP only and does not use account sessions, TLS impersonation, proxies or anti-bot workarounds.
- **MRKT**: read-only listings adapter through the Swift Gifts Partner API. It does not use MRKT account sessions, Telegram-linked credentials or user authorization tokens.
- **Swift Gifts**: third-party partner aggregator, not an official MRKT or Tonnel API. It can return listings from both providers via `POST /api/aggregator`. The backend sends `receiver: 0`, discards all returned `options` fields (including any transaction payload), caches results and throttles requests. Partial provider coverage is surfaced rather than represented as a complete feed.
- **Gift Changes API**: public beta feed of gift/model/symbol/backdrop metadata changes. It is displayed separately from marketplace listings and does not imply that a gift is listed.
- **TG Gifts daily dataset**: public daily collection-floor history, cached for one hour and displayed with the required CC BY 4.0 attribution. It is historical reference data, not a live marketplace feed.
- Cross-market matching and transaction actions remain disabled.

## Safety and credentials

The Portals Partner API credential may have broader permissions than read-only. Never place credentials in frontend code, GitHub, chat messages, or logs. The application allowlists only specific GET routes, but that does not reduce the credential's own permissions. Do not configure a credential unless an authorized adult/account owner has reviewed its permissions.

## Environment variables

- `BOT_TOKEN` — Telegram bot token, stored only in Railway Variables.
- `BOT_WEBHOOK_SECRET` — recommended explicit Telegram webhook secret (1–256 characters from Telegram's allowed set), stored only in Railway Variables. If omitted, a fresh random secret is generated at startup and registered with Telegram; setting a stable secret is preferable for controlled rotation.
- `PUBLIC_APP_URL` — public HTTPS Mini App URL.
- `ADMIN_TELEGRAM_ID` or `ADMIN_TELEGRAM_IDS` — numeric Telegram user ID(s) allowed to use bot admin commands and the Mini App catalogue-visibility controls. Configure only in the backend environment; if unset, admin access is denied.
- `API_BASE_URL` — optional internal backend URL for admin health checks; defaults to `http://127.0.0.1:8080`.
- `PORTALS_API_KEY` — optional server-side Portals credential; see the warning above.
- `SWIFTGIFTS_API_KEY` — optional server-side Swift Gifts Partner API key for read-only MRKT and Tonnel listings. Obtain and configure it only with the marketplace/provider account owner’s approval; never put it in frontend code, GitHub, chat messages or logs.

## Server data pipeline

The Mini App does not contain marketplace data or fabricate listings. It requests the backend API, and the backend retrieves public data from the configured upstream marketplaces. The backend warms the default Portals collections/listings and Tonnel public-listings caches in a background task; identical catalogue requests reuse server-side cached responses for up to 60 seconds. The cache is in process memory and resets on a Railway restart or deploy. Portals catalogue metadata is also indexed in SQLite: the latest item record is upserted by upstream gift ID, and up to 50 distinct metadata snapshots are retained per gift. Snapshots are recorded when `/api/gifts` successfully refreshes data; this is an observation history, not a complete exchange price chart or proof of ownership. The database path is `GIFT_RADAR_DB_PATH` (fallback: `DATABASE_PATH`, default: `./data/gift-radar.sqlite3`). For persistence across Railway redeploys, attach a Railway volume and set `GIFT_RADAR_DB_PATH` to a file inside that volume's mount path. Without a persistent volume, SQLite data may be lost when the service is replaced.

- `DATA_REFRESH_SECONDS` — optional background refresh interval (60–900 seconds; default 60).
- `/api/status` reports the delivery path, cache type and refresh interval.

## API

- `GET /health` — backend health.
- `GET /api/status` — source coverage and verified data capabilities.
- `GET /api/collections` — Portals collection-level data or an honest empty result.
- `GET /api/gifts` — Portals listed-gift catalogue with `search`, `collection_id`, `min_price`, `max_price`, `external_collection_number`, `filter_by_backdrops`, optional `status` (`listed` / `unlisted`), and documented `sort_by` filters (including model-rarity sorting); pagination is limited to 40 results per request.
- `GET /api/catalog/items` — read-only search over persisted Portals metadata with bounded `search`, `collection_id`, `limit` (1–50) and `offset` filters.
- `GET /api/catalog/items/{gift_id}` — exact lookup of the latest indexed metadata snapshot.
- `GET /api/catalog/items/{gift_id}/history` — up to 50 distinct metadata snapshots, newest first.
- The Mini App opens a dedicated collection view with all NFT statuses by default, plus optional listed/unlisted, price, gift-number, backdrop and sort controls. Backdrop choices are discovered from attributes returned by loaded listings; the API does not currently expose a separate complete backdrop-facets endpoint.
- `GET /api/tonnel/gifts` — Swift partner-backed public Tonnel listings when configured; otherwise the experimental unauthenticated fallback. Supports `search`, `page` and `limit`.
- `GET /api/mrkt/gifts` — public MRKT listings through the Swift partner aggregator when `SWIFTGIFTS_API_KEY` is configured. Supports `search`, `page` and `limit`.
- `GET /api/reference/gift-changes` — beta public metadata-change feed; supports `limit` and optional `since`.
- `GET /api/reference/daily-floors` — public TG Gifts daily historical floor dataset with attribution and licence metadata.

The Mini App includes marketplace tabs for Portals, Tonnel and MRKT, plus a separate public gift-metadata updates feed. Daily historical floor data is available through the reference API and is not presented as real-time listings. Portals displays listing data when configured. Tonnel attempts to load public listings without account authentication; if the unofficial endpoint refuses the request, the tab shows an unavailable state. MRKT remains unconnected because the currently suggested client requires Telegram-linked authorization. Selecting a Portals listing opens an informational detail panel with available public attributes, price, floor and listing timestamp. No transaction controls are present.

The Telegram bot provides `/myid`, `/admin` and `/admin_status`. The Mini App also has a server-authorized admin panel that can only enable or disable Portals catalogue visibility. Admin identity is verified from signed Telegram initData against `ADMIN_TELEGRAM_ID` / `ADMIN_TELEGRAM_IDS`; visibility is stored in SQLite. Attach a persistent Railway volume and set `GIFT_RADAR_DB_PATH` inside its mount path to retain this setting across redeploys. No wallet, payment, purchase or transfer actions are available.

Catalogue and provider integrations are read-only. The separate order-book interface and its API are demonstration-only: records and fills are simulated, have no settlement, and do not move assets or money. No real buying, selling, transfers, offers, payments or wallet actions are implemented.


## Security baseline (November 2026)

- Telegram Mini App `initData` is verified server-side with Telegram's HMAC scheme, bounded freshness, duplicate-field rejection and one-time replay protection. It is exchanged for an opaque 12-hour session; only a SHA-256 digest is persisted.
- API credentials are server-issued sessions, not raw Telegram `initData`. Sessions can be listed, revoked individually, or all revoked for an account through `GET /api/auth/sessions`, `POST /api/auth/logout` and `POST /api/auth/logout-all`. Session responses expose timestamps and status only, never bearer tokens or hashes.
- Telegram webhook requests require Telegram's configured secret-token header. The endpoint enforces a 1 MiB request limit and stores update IDs in a durable SQLite inbox. Duplicate processed updates are acknowledged without redispatch; failed processing releases the lease so Telegram retries. Reuse of an update ID with a different payload is rejected.
- The webhook inbox is transport-level idempotency, not a payment ledger. Telegram Stars invoices, pre-checkout approval, successful-payment fulfillment, refunds and real settlement remain **disabled**. Never mark an order paid from a client callback, screenshot, browser request or unverified webhook.
- If Telegram Stars is approved for a future release, the server must create invoices from server-owned order/price data in `XTR`, validate `pre_checkout_query` (user, payload, amount, currency, expiry and order state), and treat only Telegram's authenticated `successful_payment` update as payment evidence. Persist charge IDs and a unique transaction record inside one database transaction; make duplicate updates and charge IDs harmless. Do not enable fulfilment until that separate implementation has passed review and tests.
- GIFT RADAR does not request, store or operate users' seed phrases, private keys, wallet passwords, exchange API keys or account API tokens. It cannot debit, credit or otherwise mutate user balances. Current orders and deal states remain simulation-only.


## Financial infrastructure and wallet connection (in progress)

The financial ledger and marketplace stores are separate logical subsystems. A guarded PostgreSQL adapter and a one-way migration path now cover the marketplace, offers, notifications and Portals catalogue tables; SQLite remains the default until a controlled data cutover is completed.

- financial_ledger.py implements balanced signed integer-unit postings, immutable journal rows, idempotency keys, account balance projections, server-owned payment invoices and strict normalized TON Jetton trace checks.
- migrations/ and alembic.ini provide an Alembic migration for PostgreSQL and SQLite CI. The PostgreSQL application stores require Alembic revision 0004_telegram_stars_terms (which includes the financial ledger base revision).
- payment_events has unique chain/transaction/event/asset identity and unique invoice binding. Ledger entries, lines and payment events are append-only at the database layer.
- TON Connect requests a TON Proof challenge. The private verifier checks the proof against the authenticated Telegram user, exact dApp domain, wallet StateInit/address and supported wallet public key before persisting the public wallet binding. The client never receives or stores seed phrases/private keys; no transaction signing, transfers or crypto checkout are enabled.
- /api/wallet/policy advertises the current disabled capabilities.
- security_controls.py validates browser Origin on mutations and rate-limits API mutations. Redis is required for distributed production enforcement; production mutations fail closed without REDIS_URL. Local process limiter is for development only.
- access_control.py centralizes buyer/seller/admin role and ownership helpers. Existing admin identity remains allowlisted by server-side Telegram IDs.

### Required deployment configuration (not applied automatically)

1. Provision PostgreSQL and set DATABASE_URL as a Railway secret variable.
2. Run alembic upgrade head as a controlled release migration before enabling financial services.
3. Provision Redis and set REDIS_URL as a Railway secret variable. Production mutation endpoints fail closed without it.
4. Set CORS_ALLOWED_ORIGINS to the exact HTTPS origins used by the Mini App.
5. Serve tonconnect-manifest.json from the exact public Mini App origin and verify the icon, terms and privacy URLs exist.
6. Complete TON proof verification before persisting a wallet address. The current UI connection is deliberately non-persistent.
7. Implement and independently verify a production TON indexer adapter that resolves the canonical Jetton wallet via the master contract and produces the normalized finalized trace required by verify_ton_payment_trace.
8. Obtain written platform/legal approval for the exact digital-goods payment model. Crypto payment policy remains disabled; GRAM is not allowlisted; TRON remains excluded from Mini App.
9. Run migration, backup/restore, load, concurrency, and security testing against staging before any production activation.

The SQLite-to-PostgreSQL cutover is prepared but has not been executed. Telegram Stars invoice, pre-checkout and successful-payment handlers plus an authenticated invoice-link endpoint are implemented behind fail-closed gates. They cannot charge the current demo deals: all existing deals are `is_real=0`, no real-deal creation/fulfilment path exists, and the live backend still uses SQLite. The invoice-link request omits `provider_token` as required for XTR. `postgres_compat.py` provides the deliberately narrow SQL compatibility layer, and `migrations/versions/0004_telegram_stars_terms.py` creates the PostgreSQL marketplace, offer/deal, notification and catalogue schema. Catalogue JSON search uses PostgreSQL JSONB casts while preserving the stored JSON text.

#### Controlled SQLite-to-PostgreSQL cutover

1. Take a verified backup of the SQLite database and record its file size and checksum. Keep the backup immutable.
2. Provision PostgreSQL and apply `alembic upgrade head`. Confirm the Alembic revision is exactly `0004_telegram_stars_terms`.
3. Copy the SQLite source to a controlled migration runner or securely mount its Railway volume. Do not print or paste database credentials.
4. Run a dry-run first: `python scripts/migrate_sqlite_to_postgres.py --source /path/to/gift-radar.sqlite3`. It checks target emptiness, required source tables, row counts and deterministic SHA-256 checksums.
5. Schedule a maintenance window, stop every SQLite writer, and take a final consistent backup. Only then run `python scripts/migrate_sqlite_to_postgres.py --source /path/to/gift-radar.sqlite3 --apply --source-quiesced`.
6. Verify every imported table's row count and content checksum. Keep SQLite read-only as a rollback source.
7. In a separate reviewed release, set `GIFT_RADAR_STORAGE_BACKEND=postgres` and the Railway `DATABASE_URL` reference together. Run smoke, concurrency, backup/restore and API ownership tests before reopening writes.
8. Do not remove the SQLite backup or switch off rollback capability until production reconciliation is signed off.

The migration tool refuses to write unless `--apply` and `--source-quiesced` are both present, refuses to import into non-empty target tables, and refuses cutover if any Stars invoice/payment/refund records exist in SQLite because those require separate reconciliation. It runs all imports and checksum checks in one PostgreSQL transaction. The current live Railway app has not been switched to PostgreSQL, no SQLite records have been copied, and no production cutover has been performed. CI now includes a dedicated PostgreSQL 16 service job that applies all Alembic revisions and verifies the real database schema, adapter and append-only ledger trigger.