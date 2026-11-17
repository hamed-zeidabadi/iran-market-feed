// Public, fixed-target JSON API. This file can be pasted into Arvan Edge.
// Values are observations, not the Iran Market Feed reference calculation.
const ENDPOINTS = Object.freeze({
  ramzinex: 'https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/orderbooks/11/buys_sells',
  arzdigital: 'https://lake.arzdigital.com/web/api/v1/pub/exchanges/pairs/top-pairs?limit=50&offset=0&base=tether&deprecated=false&exchange_country=IR',
  ordibehesht: 'https://ordibehesht.gold/gold-rate/latest',
  parasteh: 'https://parasteh.com/wp-json/parasteh/v1/gold-rate',
  tgju: 'https://call2.tgju.org/ajax.json',
  tgju_widget: 'https://api.tgju.org/v1/widget/tmp?keys=137119,137120,137121,137123,137137,137138,137139,137140',
  digikala: 'https://api.digikala.com/wealth/v1/prices/',
});

function positive(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  return value;
}

function stamp(value) {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d\d:\d\d)$/.test(value)) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

function result(asset, price, source, basis, sourceTime, now, extra = {}) {
  const ounce = asset === 'gold_ounce' || asset === 'silver_ounce';
  return {asset, price, currency: ounce ? 'USD' : 'IRT',
    unit: ounce ? 'troy_ounce' : asset === 'usdt' ? 'USDT' : asset.startsWith('coin_') ? 'coin' : 'gram',
    source, time_basis: basis, source_time: sourceTime, observed_at: now, ...extra};
}

function extract(source, payload, now) {
  if (source === 'digikala') {
    return [['gold18','gold_18k'], ['silver999','silver_999']].flatMap(([key, asset]) => {
      const price = positive(payload?.[key]?.price);
      return price ? [result(asset, price * 100, source, 'observed_only', null, now,
        {note: 'rial per milligram inferred; rate publication time unknown'})] : [];
    });
  }
  if (source === 'tgju') {
    if (!payload?.current || typeof payload.current !== 'object') return [];
    const mapping = {geram18:'gold_18k', silver_999:'silver_999', ons:'gold_ounce',
      sekeb:'coin_bahar', sekee:'coin_emami', nim:'coin_half', rob:'coin_quarter'};
    return Object.entries(mapping).flatMap(([key, asset]) => {
      const row = payload.current[key];
      if (!row) return [];
      const price = positive(Number(String(row.p).replace(/,/g, '')));
      if (!price) return [];
      const t = String(row.ts || ''), clock = String(row.t_en || '');
      const valid = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(t) && t.slice(11) === clock;
      const time = valid ? stamp(t.replace(' ', 'T') + '+03:30') : null;
      return [result(asset, asset === 'gold_ounce' ? price : price / 10,
        source, time ? 'source' : 'unknown', time, now)];
    });
  }
  if (source === 'tgju_widget') {
    const mapping = {137119:['ons','gold_ounce'],137121:['geram18','gold_18k'],
      137123:['silver','silver_ounce'],137137:['sekeb','coin_bahar'],
      137138:['sekee','coin_emami'],137139:['nim','coin_half'],137140:['rob','coin_quarter']};
    if (!Array.isArray(payload?.response?.indicators)) return [];
    return payload.response.indicators.flatMap(row => {
      const match = mapping[row?.item_id];
      if (!match || row.name !== match[0] || !/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(row.updated_at || '')) return [];
      const price = positive(Number(row.p)), time = stamp(row.updated_at.replace(' ', 'T') + '+03:30');
      if (!price || !time) return [];
      return [result(match[1], match[1].endsWith('_ounce') ? price : price / 10,
        source, 'provider_snapshot', time, now)];
    });
  }
  if (source === 'ordibehesht') {
    const time = stamp(payload?.verifiedAtUtc), price = positive(payload?.gold18);
    return payload?.fresh === true && time && price
      ? [result('gold_18k', price, source, 'provider_snapshot', time, now)] : [];
  }
  if (source === 'parasteh') {
    const price = positive(payload?.gold_original), time = stamp(payload?.updated_at);
    return payload?.symbol === 'XAU' && price
      ? [result('gold_18k', price, source, time ? 'provider_snapshot' : 'unknown', time, now,
          {comparison_only: true, note: 'weight and purity unconfirmed'})] : [];
  }
  if (source === 'ramzinex') {
    const book = payload?.data;
    if (!Array.isArray(book?.buys) || !Array.isArray(book?.sells)) return [];
    function levels(rows) {
      return rows.filter(x => Array.isArray(x) && positive(x[0]) && positive(x[1]) &&
        Number.isSafeInteger(x[6]) && x[6] >= 1e12 && x[6] < 1e13);
    }
    const buys = levels(book.buys), sells = levels(book.sells);
    if (!buys.length || !sells.length) return [];
    const bid = Math.max(...buys.map(x => x[0])), ask = Math.min(...sells.map(x => x[0]));
    const mid = (bid + ask) / 2;
    if (bid > ask || (ask - bid) / mid > 0.05) return [];
    const millis = Math.max(...buys.concat(sells).map(x => x[6]));
    return [result('usdt', mid / 10, source, 'book_level_change', new Date(millis).toISOString(), now,
      {quote_type: 'midpoint', note: 'newest level time is not necessarily a timestamp for both sides'})];
  }
  if (source === 'arzdigital') {
    if (payload?.status !== 'success' || !Array.isArray(payload.data)) return [];
    const allowed = new Set(['nobitex', 'ramzinex', 'wallex']);
    const candidates = payload.data.filter(r => r?.contract_type === 'spot' && r.deprecated === false &&
      r.source?.deprecated === false && r.source?.exchange?.deprecated === false &&
      allowed.has(r.source?.exchange?.slug) && r.source?.slug === r.source.exchange.slug + '-spot' &&
      r.base?.slug === 'tether' && r.base?.symbol === 'USDT' &&
      r.quote?.slug === 'ir-toman' && r.quote?.symbol === 'IRT');
    const counts = {};
    for (const r of candidates) counts[r.source.exchange.slug] = (counts[r.source.exchange.slug] || 0) + 1;
    return candidates.flatMap(r => {
      const bid = positive(r.bid), ask = positive(r.ask), t = r.updated_at;
      if (counts[r.source.exchange.slug] !== 1 || !bid || !ask || bid > ask ||
          !Number.isSafeInteger(t) || t < 1e9 || t >= 1e10) return [];
      return [result('usdt', (bid + ask) / 2, source, 'provider_snapshot',
        new Date(t * 1000).toISOString(), now,
        {exchange: r.source.exchange.slug, quote_type: 'midpoint'})];
    });
  }
  return [];
}

async function getSource(source, fetcher, now) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetcher(ENDPOINTS[source], {
        redirect: 'manual', headers: {'Accept': 'application/json', 'User-Agent': 'IranMarketFeed/0.1'},
        signal: controller.signal,
      });
      if (!response.ok) return {status: 'error', http_status: response.status, quotes: []};
      const length = Number(response.headers.get('content-length'));
      if (length > 2_000_000) return {status: 'error', reason: 'response_too_large', quotes: []};
      const text = await response.text();
      if (text.length > 2_000_000) return {status: 'error', reason: 'response_too_large', quotes: []};
      const quotes = extract(source, JSON.parse(text), now);
      return {status: quotes.length ? 'parsed' : 'no_quotes', http_status: response.status, quotes};
    } finally { clearTimeout(timer); }
  } catch {
    return {status: 'error', reason: 'fetch_or_parse_failed', quotes: []};
  }
}

async function handleRequest(request, fetcher = fetch) {
  if (request.method !== 'GET') return json({error: 'method_not_allowed'}, 405);
  const url = new URL(request.url);
  if (url.pathname !== '/' && url.pathname !== '/api/prices') return json({error: 'not_found'}, 404);
  if (url.pathname === '/' && !url.search) return new Response(page(), {headers: {
    'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
  }});
  const source = url.searchParams.get('source');
  if ([...url.searchParams.keys()].some(k => k !== 'source') ||
      url.searchParams.getAll('source').length > 1 || (source && !Object.hasOwn(ENDPOINTS, source)))
    return json({error: 'invalid_source'}, 400);
  const now = new Date().toISOString();
  const names = source ? [source] : Object.keys(ENDPOINTS);
  const values = await Promise.all(names.map(name => getSource(name, fetcher, now)));
  return json({generated_at: now, note: 'source observations; not a reference price',
    sources: Object.fromEntries(names.map((name, i) => [name, values[i]]))});
}

function page() {
  return `<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>تابلوی بازار ایران</title><link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css"><style>
:root{color-scheme:dark;--bg:#101a24;--panel:#172533;--muted:#9babb7;--ink:#f3f7f7;--gold:#e7bd72;--line:#304251;--green:#72d3b1}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 83% 0,#273746 0,transparent 35%),var(--bg);color:var(--ink);font-family:Vazirmatn,Tahoma,sans-serif;line-height:1.7}
main{max-width:1160px;margin:auto;padding:36px 24px 60px}header{display:flex;align-items:center;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:25px}
.brand{display:flex;align-items:center;gap:12px;color:var(--gold);font-weight:bold}.logo{width:42px;height:42px;display:grid;place-items:center;border:1px solid #ae8b50;border-radius:14px;font-size:21px}.actions{display:flex;gap:12px;align-items:center}a{color:var(--gold);text-decoration:none}button{font:inherit;color:#11201d;background:var(--green);border:0;border-radius:12px;padding:9px 16px;cursor:pointer;font-weight:bold}
.intro{display:flex;justify-content:space-between;align-items:end;gap:20px;padding:40px 0 24px}.eyebrow{color:var(--gold);font-size:12px}.intro h1{font-size:clamp(26px,4vw,40px);margin:4px 0}.muted{color:var(--muted);font-size:13px}.live{color:var(--green);white-space:nowrap;font-size:13px}.live:before{content:'●';margin-left:7px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;min-height:150px}.card .label{color:#bac8cf;font-size:14px}.price{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;font-size:clamp(20px,2.1vw,27px);font-weight:bold;direction:rtl;margin:15px 0 4px}.amount{direction:ltr;unicode-bidi:isolate}.unit{font-size:12px;color:var(--gold);white-space:nowrap}.meta{font-size:12px;color:var(--muted)}.warn{color:#f2bf78}.bar{display:flex;justify-content:space-between;gap:16px;align-items:center;margin:38px 0 12px}.bar h2{margin:0;font-size:19px}.table{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:var(--panel)}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:14px 18px;text-align:right;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:normal}tr:last-child td{border:0}.badge{display:inline-block;color:var(--green)}.bad{color:#eaa8a1}.notice{margin-top:20px;color:var(--muted);font-size:12px}.empty{text-align:center;padding:25px;color:var(--muted)}@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:500px){main{padding:24px 16px 45px}header,.intro{align-items:flex-start}.intro{flex-direction:column}.card{padding:16px;min-height:135px}th,td{padding:10px 8px;font-size:11px}.actions a{display:none}}
</style></head><body><main><header><div class="brand"><span class="logo">◈</span><span>بازار ایران</span></div><div class="actions"><a href="/api/prices">دریافت JSON ↗</a><button id="reload">به‌روزرسانی ↻</button></div></header>
<section class="intro"><div><div class="eyebrow">نمایی از قیمت‌های دریافتی</div><h1>تابلوی بازار</h1><div class="muted">طلا، نقره، ارز و سکه در یک نگاه</div></div><div class="live" id="updated">در حال دریافت اطلاعات</div></section>
<section class="grid" id="cards"></section><p class="notice">این اعداد از منابع مختلف نمایش داده می‌شوند و قیمت مرجع تجمیعی نیستند. زمان دریافت با زمان اعلام قیمت یکسان نیست؛ پیش از استفاده به وضعیت هر نرخ توجه کنید.</p></main>
<script>
const items=[['gold_18k','طلای ۱۸ عیار','تومان / گرم'],['silver_999','نقره ۹۹۹','تومان / گرم'],['usdt','تتر','تومان'],['gold_ounce','اونس طلا','دلار'],['coin_emami','سکه امامی','تومان'],['coin_bahar','سکه بهار آزادی','تومان'],['coin_half','نیم سکه','تومان'],['coin_quarter','ربع سکه','تومان'],['silver_ounce','اونس نقره','دلار']];
const preferred={gold_18k:['tgju','ordibehesht','digikala','tgju_widget','parasteh'],silver_999:['tgju','digikala'],usdt:['ramzinex','arzdigital'],gold_ounce:['tgju','tgju_widget'],silver_ounce:['tgju_widget'],coin_emami:['tgju','tgju_widget'],coin_bahar:['tgju','tgju_widget'],coin_half:['tgju','tgju_widget'],coin_quarter:['tgju','tgju_widget']};
const cards=document.getElementById('cards'), updated=document.getElementById('updated');
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
function age(q,asset){if(!q.source_time)return null;const v=(Date.now()-Date.parse(q.source_time))/1000;if(!Number.isFinite(v)||v< -120)return null;return v<=(asset==='usdt'?300:5400)}
function formatQuoteTime(q,asset){
  if(!q.source_time || !Number.isFinite(Date.parse(q.source_time)))return 'زمان نرخ نامشخص';
  const when=new Intl.DateTimeFormat('fa-IR-u-ca-persian',{timeZone:'Asia/Tehran',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(q.source_time));
  const label=q.time_basis==='source'?'زمان نرخ':q.time_basis==='book_level_change'?'آخرین تغییر سفارش':'زمان داده';
  return label+': '+when+(age(q,asset)===false?' · قدیمی':'');
}
function render(data){cards.replaceChildren();const all=Object.values(data.sources||{}).flatMap(s=>s.quotes||[]);for(const [id,label,unit] of items){const opts=all.filter(q=>q.asset===id&&typeof q.price==='number'&&q.price>0);opts.sort((a,b)=>{const av=age(a,id)===true?0:age(a,id)===null?1:2,bv=age(b,id)===true?0:age(b,id)===null?1:2;return av-bv||(preferred[id].indexOf(a.source)<0?99:preferred[id].indexOf(a.source))-(preferred[id].indexOf(b.source)<0?99:preferred[id].indexOf(b.source))});const q=opts[0],card=el('article','card');card.append(el('div','label',label));const price=el('div','price');price.append(el('span','amount',q?new Intl.NumberFormat('fa-IR',{maximumFractionDigits:2}).format(q.price):'\u2014'),el('span','unit',unit));card.append(price);const state=q?formatQuoteTime(q,id):'';card.append(el('div','meta'+(q&&age(q,id)!==true?' warn':''),q?state:'داده‌ای دریافت نشد'));cards.append(card)}updated.textContent='آخرین بررسی: '+new Intl.DateTimeFormat('fa-IR',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(data.generated_at))}
async function refresh(){updated.textContent='در حال دریافت اطلاعات...';try{const r=await fetch('/api/prices',{cache:'no-store'});if(!r.ok)throw Error();render(await r.json())}catch{updated.textContent='دریافت اطلاعات ناموفق بود'}}document.getElementById('reload').addEventListener('click',refresh);refresh();setInterval(refresh,60000);
</script></body></html>`;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {status, headers: {
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
  }});
}

if (typeof addEventListener === 'function') {
  addEventListener('fetch', event => event.respondWith(handleRequest(event.request)));
}

// CommonJS export is ignored by the Arvan runtime and enables local tests.
if (typeof module !== 'undefined') module.exports = {extract, handleRequest};
