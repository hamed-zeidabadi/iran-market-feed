const test = require('node:test');
const assert = require('node:assert/strict');
const {extract, handleRequest} = require('../scheduler/arvan/market-api.js');

const now = '2026-11-17T06:10:00.000Z';
const row = {contract_type:'spot', deprecated:false, bid:270600, ask:270790, updated_at:1794895700,
  source:{slug:'nobitex-spot', deprecated:false, exchange:{slug:'nobitex', deprecated:false}},
  base:{slug:'tether', symbol:'USDT'}, quote:{slug:'ir-toman', symbol:'IRT'}};

test('extracts only undiscounted Parasteh and keeps timestamp separate', () => {
  const values = extract('parasteh', {symbol:'XAU', gold:100, gold_original:200,
    updated_at:'2026-11-17T06:00:00Z'}, now);
  assert.equal(values[0].price, 200);
  assert.equal(values[0].time_basis, 'provider_snapshot');
  assert.equal(values[0].comparison_only, true);
});
test('extracts valid Ramzinex book and converts rial to toman', () => {
  const payload = {data:{buys:[[2698555,1,0,0,0,0,1794895700000]],
    sells:[[2700000,1,0,0,0,0,1794895700000]]}};
  assert.equal(extract('ramzinex', payload, now)[0].price, 269927.75);
  payload.data.sells[0][0] = 1;
  assert.deepEqual(extract('ramzinex', payload, now), []);
});
test('rejects crossed or duplicate Arzdigital books', () => {
  assert.equal(extract('arzdigital', {status:'success',data:[row]}, now)[0].price, 270695);
  assert.deepEqual(extract('arzdigital', {status:'success',data:[row,row]}, now), []);
  assert.deepEqual(extract('arzdigital', {status:'success',data:[{...row,bid:280000}]}, now), []);
});
test('TGJU maps coins and gold with truthful timestamps and units', () => {
  const payload = {current:{geram18:{p:'246,309,000',ts:'2026-11-17 09:30:00',t_en:'09:30:00'},
    sekeb:{p:'2,430,000,000',ts:'2026-11-17 09:30:00',t_en:'09:30:00'},
    ons:{p:'3,871.25',ts:'2026-11-17 09:30:00',t_en:'09:30:00'}}};
  const quotes = extract('tgju', payload, now);
  assert.equal(quotes.find(q=>q.asset==='gold_18k').price, 24630900);
  assert.equal(quotes.find(q=>q.asset==='coin_bahar').price, 243000000);
  assert.equal(quotes.find(q=>q.asset==='gold_ounce').currency, 'USD');
  assert.equal(quotes[0].source_time, '2026-11-17T06:00:00.000Z');
  payload.current.geram18.t_en = 'wrong';
  assert.equal(extract('tgju', payload, now)[0].time_basis, 'unknown');
});
test('page and JSON routes remain separate', async () => {
  const page = await handleRequest(new Request('https://edge.test/'), () => assert.fail());
  assert.match(page.headers.get('content-type'), /text\/html/);
  const html = await page.text();
  assert.match(html, /تابلوی بازار/);
  assert.match(html, /fa-IR-u-ca-persian/);
  assert.match(html, /Asia\/Tehran/);
  new (require('node:vm').Script)(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
  const data = await handleRequest(new Request('https://edge.test/api/prices?source=digikala'),
    async () => new Response(JSON.stringify({gold18:{price:247015},silver999:{price:4278}})));
  const quotes = (await data.json()).sources.digikala.quotes;
  assert.deepEqual(quotes.map(q=>q.price), [24701500, 427800]);
  assert.ok(quotes.every(q=>q.time_basis==='observed_only' && q.source_time===null));
});
test('endpoint only accepts listed sources and records upstream failure', async () => {
  const forbidden = await handleRequest(new Request('https://edge.test/?source=https://example.com'), () => assert.fail());
  assert.equal(forbidden.status, 400);
  const res = await handleRequest(new Request('https://edge.test/api/prices?source=ramzinex'),
    async () => new Response('', {status:403}));
  const data = await res.json();
  assert.equal(data.sources.ramzinex.status, 'error');
  assert.equal(data.sources.ramzinex.http_status, 403);
});
