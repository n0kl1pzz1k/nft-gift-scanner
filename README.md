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

## Cloudflare Worker build

The `nft-gift-scanner` Worker is built from the `main` branch. The frontend build runs `npm --prefix frontend install && npm --prefix frontend run build`, followed by `npx wrangler deploy`.
