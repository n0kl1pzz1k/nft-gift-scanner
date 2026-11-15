# HASSLE NFT Market

**Маркетплейс NFT-подарков Telegram от n0kl1pzz1k.**

HASSLE NFT is an independent Telegram Mini App marketplace focused on listings, buyer requests, seller profiles, offers, favorites and account activity.

## Product identity
- Product name: **HASSLE NFT**
- Product descriptor: **HASSLE NFT Market**
- Russian description: **Маркетплейс NFT-подарков Telegram от n0kl1pzz1k.**
- Primary assets: Telegram NFT gifts
- Listing currencies: TON and Telegram Stars (XTR)

## Architecture
- `frontend/` - Svelte 5 Telegram Mini App.
- `bot.py` - Telegram bot entry point and Mini App launcher.
- `app.py`, `marketplace.py` - backend API and marketplace operations.
- `demo_offers.py` - offer endpoints. Acceptance stays disabled until real escrow and NFT transfer are implemented. No simulated settlement.
- `wrangler.toml` - Cloudflare Worker frontend deployment.
- Railway hosts the backend API.

## Runtime configuration
Secrets belong only in Railway/Cloudflare environment variables. Never commit bot tokens, API keys, wallet seeds, private keys or session credentials.
- `BOT_TOKEN` - Telegram bot token.
- `BOT_WEBHOOK_SECRET` - Telegram webhook secret.
- `PUBLIC_APP_URL` - public Mini App URL.
- `API_BASE_URL` - backend API URL.
- `ADMIN_TELEGRAM_ID` / `ADMIN_TELEGRAM_IDS` - administrator Telegram IDs.
- `HASSLE_NFT_DB_PATH` - optional database path; the legacy `GIFT_RADAR_DB_PATH` is still read as a fallback to preserve existing production data.
- GitHub Actions secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` - optional, free Cloudflare Worker deployment credentials. Without them, the deploy step safely skips.

## Deployment
- Frontend Worker name: `hassle-nft-market`.
- Frontend URL: `https://hassle-nft-market.fj7kmzkhn9.workers.dev`.
- Backend URL: `https://hassle-nft-market.up.railway.app`.
- Frontend build: `npm --prefix frontend install && npm --prefix frontend run check && npm --prefix frontend run build`.
- Worker publish: `npx wrangler deploy`.

## Production integrity
The interface must never display fake balances, fake completed trades, fabricated inventory or simulated payments. TON/Stars settlement and on-chain NFT escrow/transfer must be enabled only after complete production implementation and verification. Until then, unsupported settlement actions stay disabled.