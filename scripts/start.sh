#!/usr/bin/env bash
set -Eeuo pipefail

API_PID=""
PROOF_PID=""

cleanup() {
  if [[ -n "$API_PID" ]]; then kill "$API_PID" 2>/dev/null || true; fi
  if [[ -n "$PROOF_PID" ]]; then kill "$PROOF_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

# Keep the TON Proof verifier private and colocated with the existing API.
# No new Railway service or public port is required.
if [[ -z "${TON_PROOF_SERVICE_URL:-}" ]]; then
  export TON_PROOF_PORT="${TON_PROOF_PORT:-8081}"
  export TON_PROOF_SERVICE_URL="http://127.0.0.1:${TON_PROOF_PORT}"
  export TON_PROOF_INTERNAL_TOKEN="${TON_PROOF_INTERNAL_TOKEN:-$(python -c 'import secrets; print(secrets.token_urlsafe(48))')}"
  export TON_PROOF_ALLOWED_DOMAINS="${TON_PROOF_ALLOWED_DOMAINS:-nft-gift-scanner.fj7kmzkhn9.workers.dev,nft-gift-scanner.pages.dev,feat-testnet-ton-usdt-escrow.nft-gift-scanner.pages.dev}"
  export TON_PROOF_ALLOWED_DOMAIN_SUFFIXES="${TON_PROOF_ALLOWED_DOMAIN_SUFFIXES:--nft-gift-scanner.fj7kmzkhn9.workers.dev}"

  npm --prefix services/ton-proof start &
  PROOF_PID=$!

  python - "${TON_PROOF_PORT}" <<'PY'
import sys
import time
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/health"
for _ in range(40):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit("TON Proof sidecar failed its private health check")
PY
fi

uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}" &
API_PID=$!

if [[ -n "$PROOF_PID" ]]; then
  wait -n "$API_PID" "$PROOF_PID"
  exit 1
fi
wait "$API_PID"
