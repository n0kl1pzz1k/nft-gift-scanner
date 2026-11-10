# NFT Gift Scanner

Telegram bot and bilingual Mini App for displaying public Telegram gift marketplace information.

## Current data sources

- **Portals**: the backend adapter uses only the documented GET routes for collection preview and collection floors. It does not expose purchase, sale, offer, wallet, transfer, withdrawal, or betting actions.
- **Tonnel**: not connected. No official public API contract has been verified for this project.
- **MRKT**: not connected. No official public API contract has been verified for this project.

The Portals Partner API credential is powerful: Portals documents that the same key can authorize both read and write operations. Keep it server-side and do not add it to the Mini App or commit it to GitHub. This project itself allowlists only two GET market-data routes, but that does not change the permissions of the credential.

## Environment variables

Set these in Railway Variables, never in source code:

- `BOT_TOKEN` — Telegram bot token.
- `PUBLIC_APP_URL` — public HTTPS Mini App URL.
- `PORTALS_API_KEY` — optional Portals Partner API credential. The application only uses it for the two allowlisted GET market-data routes above.

## API

- `GET /health` — backend health.
- `GET /api/status` — current source connection states.
- `GET /api/collections` — Portals collection preview and floor data, or an empty list with a clear status when no key is configured.

All application endpoints are read-only. No buying, selling, transfers, withdrawals, offers, betting, or gambling endpoints are implemented.
