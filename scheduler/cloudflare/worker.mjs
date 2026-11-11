// External trigger for .github/workflows/update.yml.
// It stores nothing and publishes nothing; it only asks GitHub to start the
// existing workflow. The token is a Worker secret (GITHUB_TOKEN), never source.

const API = "https://api.github.com";
const HEADERS = (token) => ({
  Authorization: `Bearer ${token}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "iran-market-feed-scheduler",
});

export async function dispatch(env, fetchImpl = fetch) {
  const repo = env.GITHUB_REPOSITORY;
  const workflow = env.WORKFLOW_FILE;
  const ref = env.WORKFLOW_REF;
  if (!env.GITHUB_TOKEN || !repo || !workflow || !ref)
    throw new Error("scheduler is not configured");
  const base = `${API}/repos/${repo}/actions/workflows/${workflow}`;
  const headers = HEADERS(env.GITHUB_TOKEN);

  // The workflow serialises runs (concurrency group). A run already waiting
  // will publish fresh data, so another dispatch would be redundant.
  const res = await fetchImpl(`${base}/runs?status=queued&per_page=1`, {
    headers,
  });
  if (!res.ok)
    throw new Error(
      `run lookup failed: HTTP ${res.status} ${await detail(res)}`,
    );
  if ((await res.json()).total_count >= 1)
    return { skipped: "run already queued" };

  let last;
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetchImpl(`${base}/dispatches`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ ref }),
    });
    if (res.status === 204) return { dispatched: true, attempt: attempt + 1 };
    last = `HTTP ${res.status} ${await detail(res)}`;
    if (res.status < 500 && res.status !== 429) break;
  }
  throw new Error(`workflow dispatch failed: ${last}`);
}

// GitHub error text only (never request headers or the token), for the log.
async function detail(res) {
  try {
    return String((await res.text()) ?? "").slice(0, 200);
  } catch {
    return "";
  }
}

export default {
  async scheduled(controller, env, ctx) {
    const at = new Date(controller.scheduledTime).toISOString();
    console.log(JSON.stringify({ tick: at, cron: controller.cron }));
    ctx.waitUntil(
      dispatch(env).then(
        (r) => console.log(JSON.stringify({ tick: at, ...r })),
        (e) => {
          console.error(JSON.stringify({ tick: at, error: e.message }));
          throw e;
        },
      ),
    );
  },
  // No public trigger endpoint.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
