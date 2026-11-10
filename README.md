# NFT Gift Scanner — starter

This is a simple starter for a Telegram bot and bilingual Mini App.

## Deploy
- Railway: deploy this repository as a service; start command `uvicorn app:app --host 0.0.0.0 --port $PORT`; add `BOT_TOKEN` and `PUBLIC_APP_URL` as Railway Variables.
- Run the bot as a separate Railway worker using `python bot.py`.
- Netlify: deploy this repository with publish directory `.` (or connect GitHub and set publish directory to `.`).

Update `config.js` with the public HTTPS Railway backend URL after deployment.

## Security and scope
Never commit `.env`, Telegram bot tokens, or marketplace keys. Telegram Bot API cannot observe every user's private gift-upgrade event. Marketplace adapters are not enabled until official read-only schemas and authorized credentials are confirmed. Getgems private Gift API access must not be assumed. No buying, selling, transfers, betting, or gambling endpoints are used.
