# NFT Gift Scanner

Telegram bot and bilingual Mini App for public, read-only Telegram gift marketplace information.

## Current verified scope

- **Portals**: optional adapter for documented GET collection-preview, collection-floor and listed-gift search routes. The app exposes only GET requests and displays listing prices, collection floors and gift attributes; it does not implement purchases, sales, transfers or offers.
- **Tonnel**: not connected. An official public read-only API contract has not been verified.
- **MRKT**: not connected. An official public read-only API contract has not been verified.
- Cross-market matching is disabled until all compared sources expose compatible, official public data.

## Safety and credentials

The Portals Partner API credential may have broader permissions than read-only. Never place credentials in frontend code, GitHub, chat messages, or logs. The application allowlists only specific GET routes, but that does not reduce the credential's own permissions. Do not configure a credential unless an authorized adult/account owner has reviewed its permissions.

## Environment variables

- `BOT_TOKEN` — Telegram bot token, stored only in Railway Variables.
- `PUBLIC_APP_URL` — public HTTPS Mini App URL.
- `ADMIN_TELEGRAM_ID` — numeric Telegram user ID allowed to use `/admin` and `/admin_status`; obtain your own ID with `/myid`, then set it in Railway Variables. If unset, admin commands are denied to everyone.
- `API_BASE_URL` — optional internal backend URL for admin health checks; defaults to `http://127.0.0.1:8080`.
- `PORTALS_API_KEY` — optional server-side Portals credential; see the warning above.

## API

- `GET /health` — backend health.
- `GET /api/status` — source coverage and verified data capabilities.
- `GET /api/collections` — Portals collection-level data or an honest empty result.
- `GET /api/gifts` — public listed-gift catalogue with optional `search` and `collection_id` filters; pagination is limited to 40 results per request.

The Telegram bot provides `/myid` for a user to view their own numeric Telegram ID, plus `/admin` and `/admin_status` restricted to the single `ADMIN_TELEGRAM_ID`. The admin panel reports service/source status only; it does not expose trading or wallet actions. Configure the ID in Railway Variables, never in source code.

All application endpoints are read-only. No buying, selling, transfers, offers, or wallet actions are implemented.
