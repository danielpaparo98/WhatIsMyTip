/**
 * playhq-proxy — token-gated Cloudflare Worker forwarding /graphql to
 * api.playhq.com.
 *
 * Why this exists (2026-10): PlayHQ's CloudFront edge denies
 * DigitalOcean's datacenter IP ranges outright (403 with the generic
 * CloudFront ERROR page, regardless of browser headers — verified live
 * against prod).  Cloudflare Worker egress is a different network that
 * PlayHQ's WAF does not blanket-block, so the WhatIsMyTip backend
 * points PLAYHQ_GRAPHQL_BASE at this worker instead.
 *
 * The browser identity headers the backend already sends are forwarded
 * verbatim; the worker adds nothing (a wrong browser header is a bot
 * tell).  A shared secret (X-Proxy-Token vs the PROXY_TOKEN secret)
 * keeps the endpoint from being an open PlayHQ relay.
 *
 * Deploy (see README.md): npx wrangler login && npx wrangler secret
 * put PROXY_TOKEN && npx wrangler deploy
 */

const UPSTREAM = "https://api.playhq.com/graphql";

// Browser/client-hint headers the backend sends — forward exactly
// these, nothing else (extra hop headers would be a bot tell).
const FORWARDED_HEADERS = [
  "content-type",
  "accept",
  "accept-language",
  "origin",
  "referer",
  "apollo-require-preflight",
  "tenant",
  "user-agent",
  "sec-ch-ua",
  "sec-ch-ua-mobile",
  "sec-ch-ua-platform",
  "sec-fetch-dest",
  "sec-fetch-mode",
  "sec-fetch-site",
  "priority",
  "x-proxy-token",
];

function jsonResponse(status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return jsonResponse(405, { error: "POST only" });
    }
    if (!env.PROXY_TOKEN) {
      return jsonResponse(500, {
        error: "worker misconfigured: PROXY_TOKEN secret not set",
      });
    }
    const token = request.headers.get("x-proxy-token");
    if (typeof token !== "string" || token !== env.PROXY_TOKEN) {
      return jsonResponse(403, { error: "forbidden" });
    }

    const upstreamHeaders = new Headers();
    for (const name of FORWARDED_HEADERS) {
      if (name === "x-proxy-token") continue;
      const value = request.headers.get(name);
      if (value) upstreamHeaders.set(name, value);
    }
    upstreamHeaders.set("x-forwarded-for", "playhq-proxy");

    let body;
    try {
      body = await request.text();
      JSON.parse(body); // reject non-JSON before spending a subrequest
    } catch {
      return jsonResponse(400, { error: "body must be JSON GraphQL" });
    }

    let upstream;
    try {
      upstream = await fetch(UPSTREAM, {
        method: "POST",
        headers: upstreamHeaders,
        body,
      });
    } catch (err) {
      return jsonResponse(502, {
        error: `upstream fetch failed: ${String(err)}`,
      });
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") || "application/json",
        "x-playhq-proxy-status": String(upstream.status),
      },
    });
  },
};
