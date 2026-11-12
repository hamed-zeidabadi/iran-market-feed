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

export async function dispatch(env, fetchImpl = fetch, now = Date.now()) {
  const repo = env.GITHUB_REPOSITORY;
  const workflow = env.WORKFLOW_FILE;
  const ref = env.WORKFLOW_REF;
  if (!env.GITHUB_TOKEN || !repo || !workflow || !ref)
    throw new Error("scheduler is not configured");
  const base = `${API}/repos/${repo}/actions/workflows/${workflow}`;
  const headers = HEADERS(env.GITHUB_TOKEN);

  // A fresh pending run avoids duplicates; an abandoned old queue entry must
  // never suppress all future ticks. Only inspect the configured branch.
  const res = await fetchImpl(`${base}/runs?status=queued&branch=${encodeURIComponent(ref)}&per_page=100`, {
    headers,
  });
  if (!res.ok)
    throw new Error(
      `run lookup failed: HTTP ${res.status} ${await detail(res)}`,
    );
  const queued = (await res.json()).workflow_runs;
  if (!Array.isArray(queued)) throw new Error('invalid workflow run list');
  const fresh = queued.filter(run => {
    const age = now - Date.parse(run.created_at);
    return run.head_branch === ref && Number.isFinite(age) && age >= -120000 && age <= 600000;
  });
  if (fresh.length) return { skipped: 'recent run already queued', queued_run_ids: fresh.map(r=>r.id).slice(0,10) };
  const stale = queued.filter(r=>r.head_branch===ref).map(r=>r.id).slice(0,10);

  let last;
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetchImpl(`${base}/dispatches`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ ref }),
    });
    if (res.status === 204) return { dispatched: true, attempt: attempt + 1, ignored_stale_queued_runs: stale };
    last = `HTTP ${res.status} ${await detail(res)}`;
    if (res.status < 500 && res.status !== 429) break;
  }
  throw new Error(`workflow dispatch failed: ${last}`);
}

// Optional private durable record; an archive outage must not stop dispatch.
async function audit(env, at, result, failed=false) {
  if (!env.AUDIT_URL || !env.AUDIT_SECRET) return;
  const url = new URL(env.AUDIT_URL);
  if (url.protocol!=='https:' || url.pathname!=='/' || url.search || url.username || url.password)
    throw new Error('invalid audit origin');
  const batch = {id:'scheduler:'+at,kind:'workflow',at,
    records:[{log_version:1,phase:'scheduler',at,
      at_tehran:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tehran',dateStyle:'short',timeStyle:'medium'}).format(new Date(at)),
      event:failed?'scheduler_dispatch_failed':result.skipped?'scheduler_dispatch_skipped':'scheduler_dispatched',
      severity:failed?'error':result.ignored_stale_queued_runs?.length?'warning':'info',...result}]};
  const body = new Blob([JSON.stringify(batch)]).stream().pipeThrough(new CompressionStream('gzip'));
  const response = await fetch(url.origin+'/ingest',{method:'POST',redirect:'manual',
    headers:{'X-Audit-Secret':env.AUDIT_SECRET,'User-Agent':'IranMarketFeed/0.1 scheduler','Content-Type':'application/gzip'},
    body,signal:AbortSignal.timeout(15000)});
  if (!response.ok)throw new Error('scheduler audit upload failed');
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
        async (r) => {
          console.log(JSON.stringify({ tick: at, ...r }));
          try { await audit(env,at,r); } catch { console.error('scheduler archive unavailable'); }
        },
        async (e) => {
          console.error(JSON.stringify({ tick: at, error: e.message }));
          const status=e.message.match(/HTTP (\d+)/);
          try { await audit(env,at,{error_type:e.name,http_status:status?Number(status[1]):null},true); } catch { console.error('scheduler archive unavailable'); }
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
