# GIFT RADAR frontend

Modern frontend migration built alongside the existing static app.

## Stack
- Svelte 5 + TypeScript
- Vite
- Tailwind CSS 4
- Motion for lightweight UI transitions
- Existing Python FastAPI backend remains the data source

## Local development
```bash
cd frontend
npm install
npm run check
npm run build
npm run dev
```

Set `VITE_API_BASE_URL` to the Railway API URL when running locally or when frontend/backend are hosted separately.

## Cloudflare Pages
Deploy this directory as a Pages project root:
- Root directory: `frontend`
- Build command: `npm run build`
- Build output directory: `dist`
- Environment variable: `VITE_API_BASE_URL=https://nft-gift-scanner-production.up.railway.app`

The existing root-level static frontend is intentionally left untouched until this migration is built and verified. Switch the Cloudflare Pages root only after the preview deployment passes UI and API smoke tests.
