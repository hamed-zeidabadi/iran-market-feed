var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// worker.mjs
var API = "https://api.github.com";
var HEADERS = /* @__PURE__ */ __name((token) => ({
  Authorization: `Bearer ${token}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "iran-market-feed-scheduler"
}), "HEADERS");
async function dispatch(env, fetchImpl = fetch) {
  const repo = env.GITHUB_REPOSITORY;
  const workflow = env.WORKFLOW_FILE;
  const ref = env.WORKFLOW_REF;
  if (!env.GITHUB_TOKEN || !repo || !workflow || !ref)
    throw new Error("scheduler is not configured");
  const base = `${API}/repos/${repo}/actions/workflows/${workflow}`;
  const headers = HEADERS(env.GITHUB_TOKEN);
  const res = await fetchImpl(`${base}/runs?status=queued&per_page=1`, {
    headers
  });
  if (!res.ok) throw new Error(`run lookup failed: HTTP ${res.status}`);
  if ((await res.json()).total_count >= 1)
    return { skipped: "run already queued" };
  let last;
  for (let attempt = 0; attempt < 2; attempt++) {
    const res2 = await fetchImpl(`${base}/dispatches`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ ref })
    });
    if (res2.status === 204) return { dispatched: true, attempt: attempt + 1 };
    last = res2.status;
    if (res2.status < 500 && res2.status !== 429) break;
  }
  throw new Error(`workflow dispatch failed: HTTP ${last}`);
}
__name(dispatch, "dispatch");
var worker_default = {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(dispatch(env).then((r) => console.log(JSON.stringify(r))));
  },
  // No public trigger endpoint.
  async fetch() {
    return new Response("Not found", { status: 404 });
  }
};
export {
  worker_default as default,
  dispatch
};
//# sourceMappingURL=worker.js.map
