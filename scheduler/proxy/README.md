# Private read-only market proxy

Only `/ramzinex_usdt` and `/nobitex_usdt` are supported. Requests require
`X-Market-Secret`. The caller cannot choose a host/path/query or forward headers.
Redirects, non-GET methods, oversized bodies and invalid JSON are rejected.
Credentials and cookies are never forwarded; responses use `no-store`.

This is separate from the existing scheduler: deploying it does not alter the
scheduler or its secrets. No deployment or successful Cloudflare egress test has
been performed in the current environment (Wrangler is not authenticated).

1. Authenticate your own Cloudflare account with `npx wrangler login`.
2. In this directory run `npx wrangler secret put PROXY_SECRET`, then
   `npx wrangler deploy`. Enter a random secret through Wrangler, never in code.
3. Set `MARKET_PROXY_URL` (HTTPS origin only) and `MARKET_PROXY_SECRET` in your
   local environment and run `.venv/Scripts/python.exe -m scripts.probe_proxy`
   from the repository root. On Linux use `.venv/bin/python`.
4. Only after a successful fresh, correctly parsed response, configure the same
   two values as GitHub Actions secrets and verify a workflow run. The collector
   falls back to this proxy only after an upstream 403 and only for the two fixed
   source IDs. Nobitex remains disabled until validated on Actions.

The probe prints status/quote validity without secrets. A 200 alone is not
enough; check the returned publication timestamp and `reason: null` too.
Local security tests: `node --test tests/proxy.test.mjs`.
