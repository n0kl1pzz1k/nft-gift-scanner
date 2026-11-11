# NFT Gift Scanner

Telegram bot and bilingual Mini App for public, read-only Telegram gift marketplace information.

## Current verified scope

- **Portals**: optional adapter for documented GET collection-preview, collection-floor and listed-gift search routes. The app exposes only GET requests and displays listing prices, collection floors and gift attributes; it does not implement purchases, sales, transfers or offers.
- **Tonnel**: preferred read-only listings adapter through the Swift Gifts Partner API when `SWIFTGIFTS_API_KEY` is configured. Without that key, the app falls back to the experimental public `POST /api/pageGifts` endpoint with empty `user_auth`; this fallback uses ordinary HTTP only and does not use account sessions, TLS impersonation, proxies or anti-bot workarounds.
- **MRKT**: read-only listings adapter through the Swift Gifts Partner API. It does not use MRKT account sessions, Telegram-linked credentials or user authorization tokens.
- **Swift Gifts**: third-party partner aggregator, not an official MRKT or Tonnel API. It can return listings from both providers via `POST /api/aggregator`. The backend sends `receiver: 0`, discards all returned `options` fields (including any transaction payload), caches results and throttles requests. Partial provider coverage is surfaced rather than represented as a complete feed.
- Cross-market matching and transaction actions remain disabled.

## Safety and credentials

The Portals Partner API credential may have broader permissions than read-only. Never place credentials in frontend code, GitHub, chat messages, or logs. The application allowlists only specific GET routes, but that does not reduce the credential's own permissions. Do not configure a credential unless an authorized adult/account owner has reviewed its permissions.

## Environment variables

- `BOT_TOKEN` — Telegram bot token, stored only in Railway Variables.
- `PUBLIC_APP_URL` — public HTTPS Mini App URL.
- `ADMIN_TELEGRAM_ID` — numeric Telegram user ID allowed to use `/admin` and `/admin_status`; obtain your own ID with `/myid`, then set it in Railway Variables. If unset, admin commands are denied to everyone.
- `API_BASE_URL` — optional internal backend URL for admin health checks; defaults to `http://127.0.0.1:8080`.
- `PORTALS_API_KEY` — optional server-side Portals credential; see the warning above.
- `SWIFTGIFTS_API_KEY` — optional server-side Swift Gifts Partner API key for read-only MRKT and Tonnel listings. Obtain and configure it only with the marketplace/provider account owner’s approval; never put it in frontend code, GitHub, chat messages or logs.

## Server data pipeline

The Mini App does not contain marketplace data or fabricate listings. It requests the backend API, and the backend retrieves public data from the configured upstream marketplaces. The backend warms the default Portals collections/listings and Tonnel public-listings caches in a background task; identical catalogue requests reuse server-side cached responses for up to 60 seconds. The cache is in process memory and resets on a Railway restart or deploy. Persistent historical snapshots require a configured database or mounted persistent volume and are not claimed as available yet.

- `DATA_REFRESH_SECONDS` — optional background refresh interval (60–900 seconds; default 60).
- `/api/status` reports the delivery path, cache type and refresh interval.

## API

- `GET /health` — backend health.
- `GET /api/status` — source coverage and verified data capabilities.
- `GET /api/collections` — Portals collection-level data or an honest empty result.
- `GET /api/gifts` — Portals listed-gift catalogue with `search`, `collection_id`, `min_price`, `max_price`, `external_collection_number`, `filter_by_backdrops`, optional `status` (`listed` / `unlisted`), and documented `sort_by` filters (including model-rarity sorting); pagination is limited to 40 results per request.
- The Mini App opens a dedicated collection view with all NFT statuses by default, plus optional listed/unlisted, price, gift-number, backdrop and sort controls. Backdrop choices are discovered from attributes returned by loaded listings; the API does not currently expose a separate complete backdrop-facets endpoint.
- `GET /api/tonnel/gifts` — Swift partner-backed public Tonnel listings when configured; otherwise the experimental unauthenticated fallback. Supports `search`, `page` and `limit`.
- `GET /api/mrkt/gifts` — public MRKT listings through the Swift partner aggregator when `SWIFTGIFTS_API_KEY` is configured. Supports `search`, `page` and `limit`.

The Mini App includes marketplace tabs for Portals, Tonnel and MRKT. Portals displays listing data when configured. Tonnel attempts to load public listings without account authentication; if the unofficial endpoint refuses the request, the tab shows an unavailable state. MRKT remains unconnected because the currently suggested client requires Telegram-linked authorization. Selecting a Portals listing opens an informational detail panel with available public attributes, price, floor and listing timestamp. No transaction controls are present.

The Telegram bot provides `/myid` for a user to view their own numeric Telegram ID, plus `/admin` and `/admin_status` restricted to the single `ADMIN_TELEGRAM_ID`. The admin panel reports service/source status only; it does not expose trading or wallet actions. Configure the ID in Railway Variables, never in source code.

All application endpoints are read-only. No buying, selling, transfers, offers, or wallet actions are implemented.
