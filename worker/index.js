export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/tonconnect-manifest.json") {
      const corsHeaders = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Max-Age": "300",
      };
      if (request.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: corsHeaders });
      }
      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response("Method Not Allowed", {
          status: 405,
          headers: { ...corsHeaders, "Allow": "GET, HEAD, OPTIONS" },
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
          ...corsHeaders,
        },
      });
    }

    return env.ASSETS.fetch(request);
  },
};
