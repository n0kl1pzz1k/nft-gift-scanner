import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NFT Gift Scanner API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your Netlify domain before production.
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "nft-gift-scanner"}

@app.get("/api/status")
async def status():
    return {
        "sources": {
            "portals": "not_configured",
            "marketapp": "not_configured",
            "getgems": "documentation_only"
        },
        "mode": "read_only"
    }

@app.get("/api/collections")
async def collections():
    # Real marketplace data adapters are enabled only after their official
    # read-only schemas and credentials have been verified.
    return {"items": [], "note": "Marketplace adapters are not configured yet."}
