// Private, fixed-target read-only proxy. No caller-supplied upstream URLs.
export const TARGETS = Object.freeze({
  nobitex_usdt: 'https://apiv2.nobitex.ir/v3/orderbook/USDTIRT',
  ramzinex_usdt: 'https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/orderbooks/11/buys_sells',
});

export async function handle(request, env, fetchImpl = fetch) {
  if (!env.PROXY_SECRET) return new Response('Unavailable', {status: 503});
  // Hash both values before comparing; do not log or forward credentials.
  const digest = async value => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
  const [expected, received] = await Promise.all([
    digest(env.PROXY_SECRET), digest(request.headers.get('X-Market-Secret') || ''),
  ]);
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) mismatch |= expected[i] ^ received[i];
  if (mismatch) return new Response('Not found', {status: 404});
  const url = new URL(request.url);
  const id = url.pathname.slice(1);
  if (request.method !== 'GET' || url.search || !Object.hasOwn(TARGETS, id))
    return new Response('Not found', {status: 404});
  try {
    const upstream = await fetchImpl(TARGETS[id], {
      method: 'GET', redirect: 'manual',
      headers: {'Accept': 'application/json', 'User-Agent': 'IranMarketFeed/0.1'},
      signal: AbortSignal.timeout(15000), cf: {cacheTtl: 0, cacheEverything: false},
    });
    // Never follow redirects, expose cookies, or reuse cached snapshots.
    if (!upstream.ok || upstream.status >= 300)
      return new Response('Upstream unavailable', {status: upstream.status >= 400 ? upstream.status : 502, headers: {'Cache-Control': 'no-store'}});
    const reader = upstream.body.getReader();
    const chunks = []; let length = 0;
    try {
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        length += value.length;
        if (length > 2_000_000) { await reader.cancel(); return new Response('Too large', {status: 502}); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const raw = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) {raw.set(chunk, offset); offset += chunk.length;}
    JSON.parse(new TextDecoder().decode(raw));
    return new Response(raw, {headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}});
  } catch {
    return new Response('Upstream unavailable', {status: 502});
  }
}
export default {
  fetch(request, env) {
    return handle(request, env);
  },
};
