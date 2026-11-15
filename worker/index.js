export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/tonconnect-manifest.json") {
      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response("Method Not Allowed", {
          status: 405,
          headers: { "Allow": "GET, HEAD, OPTIONS" },
        });
      }

      const origin = url.origin;
      const manifest = {
        url: origin,
        name: "GIFT RADAR",
        iconUrl: origin + "/favicon.svg",
      };
      const body = JSON.stringify(manifest);
      return new Response(request.method === "HEAD" ? null : body, {
        status: 200,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "public, max-age=300",
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        },
      });
    }

    return env.ASSETS.fetch(request);
  },
};
