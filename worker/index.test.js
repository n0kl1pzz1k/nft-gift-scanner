import test from "node:test";
import assert from "node:assert/strict";
import worker from "./index.js";

test("TON Connect manifest reflects the active deployment origin", async () => {
  const response = await worker.fetch(
    new Request("https://preview-gift-radar.example/tonconnect-manifest.json"),
    { ASSETS: { fetch: async () => new Response("unexpected asset fallback") } },
  );
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /application\/json/);
  const manifest = await response.json();
  assert.deepEqual(manifest, {
    url: "https://preview-gift-radar.example",
    name: "GIFT RADAR",
    iconUrl: "https://preview-gift-radar.example/favicon.svg",
  });
});

test("manifest preflight and unsupported methods are handled safely", async () => {
  const preflight = await worker.fetch(
    new Request("https://gift-radar.example/tonconnect-manifest.json", { method: "OPTIONS" }),
    { ASSETS: { fetch: async () => new Response("unexpected asset fallback") } },
  );
  assert.equal(preflight.status, 204);
  const post = await worker.fetch(
    new Request("https://gift-radar.example/tonconnect-manifest.json", { method: "POST" }),
    { ASSETS: { fetch: async () => new Response("unexpected asset fallback") } },
  );
  assert.equal(post.status, 405);
});

test("all other requests pass through to the existing static asset binding", async () => {
  const assetResponse = new Response("built app");
  const request = new Request("https://gift-radar.example/assets/app.js");
  const response = await worker.fetch(request, {
    ASSETS: { fetch: async (received) => {
      assert.equal(received.url, request.url);
      return assetResponse;
    } },
  });
  assert.equal(response, assetResponse);
});
