/* Local harness: exercise worker.js fetch() with a stubbed upstream. */
const BROWSER_HEADERS = {
  "content-type": "application/json",
  accept: "*/*",
  origin: "https://www.playhq.com",
  referer: "https://www.playhq.com/",
  "apollo-require-preflight": "true",
  tenant: "afl",
  "user-agent": "Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36",
  "sec-ch-ua": '"Google Chrome";v="131"',
  "sec-ch-ua-mobile": "?0",
  "sec-ch-ua-platform": '"Windows"',
  "sec-fetch-dest": "empty",
  "sec-fetch-mode": "cors",
  "sec-fetch-site": "same-site",
  priority: "u=1, i",
};

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`PASS ${name}`);
  else {
    failures++;
    console.log(`FAIL ${name}${extra ? " — " + extra : ""}`);
  }
}

async function run() {
  const worker = (await import("./src/worker.js")).default;
  let captured = null;
  global.fetch = async (url, init) => {
    captured = { url, init };
    return new Response('{"data":{"ok":true}}', {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const env = { PROXY_TOKEN: "tok-123" };
  const mk = (headers, method = "POST", body = '{"query":"q"}') =>
    new Request("https://playhq-proxy.example.workers.dev/graphql", {
      method,
      headers,
      body: method === "POST" ? body : undefined,
    });

  // 1. happy path
  let r = await worker.fetch(mk({ ...BROWSER_HEADERS, "x-proxy-token": "tok-123" }), env);
  check("happy: 200 passthrough", r.status === 200);
  check("happy: body passthrough", (await r.text()).includes('"ok":true'));
  check("happy: upstream url", captured.url === "https://api.playhq.com/graphql");
  check("happy: ua forwarded", captured.init.headers.get("user-agent").includes("Chrome/131"));
  check("happy: tenant forwarded", captured.init.headers.get("tenant") === "afl");
  check("happy: proxy token NOT forwarded", captured.init.headers.get("x-proxy-token") === null);

  // 2. auth
  r = await worker.fetch(mk({ ...BROWSER_HEADERS, "x-proxy-token": "wrong" }), env);
  check("auth: wrong token 403", r.status === 403);
  r = await worker.fetch(mk(BROWSER_HEADERS), env);
  check("auth: missing token 403", r.status === 403);
  r = await worker.fetch(mk({ ...BROWSER_HEADERS, "x-proxy-token": "tok-123" }), { PROXY_TOKEN: "" });
  check("auth: unset secret 500", r.status === 500);

  // 3. method + body
  r = await worker.fetch(mk({}, "GET"), env);
  check("method: GET 405", r.status === 405);
  r = await worker.fetch(mk({ ...BROWSER_HEADERS, "x-proxy-token": "tok-123" }, "POST", "not-json"), env);
  check("body: non-json 400", r.status === 400);
  check("body: no subrequest spent", captured.init.body === '{"query":"q"}');

  // 4. upstream status passthrough
  global.fetch = async () => new Response("<HTML>ERROR</HTML>", { status: 403 });
  r = await worker.fetch(mk({ ...BROWSER_HEADERS, "x-proxy-token": "tok-123" }), env);
  check("upstream: 403 passthrough", r.status === 403);
  check("upstream: status header", r.headers.get("x-playhq-proxy-status") === "403");

  process.exit(failures ? 1 : 0);
}

run();
