import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from websocket import WebSocketTimeoutException

from marketfeed.adapters import extract
from marketfeed.__main__ import collect, load_config, read_source
from marketfeed.core import iso
from marketfeed.new_sources import depth_midpoint, milliseconds, TABDEAL_STREAM, TABDEAL_TRADES_URL
from marketfeed.report import build_report
from test_report import config, quote, NOW

STREAM = {'id': 'tabdeal', 'group': 'tabdeal', 'url': 'wss://api1.tabdeal.org/stream/',
          'adapter': 'tabdeal_depth', 'kind': 'midpoint', 'assets': ['usdt']}
CHANNEL = {'id': 'a', 'group': 'dollargold24', 'url': 'https://t.me/s/DollarGold_24',
           'adapter': 'dollargold', 'assets': ['gold_18k', 'silver_999', 'coin_bahar', 'coin_emami', 'coin_half', 'coin_quarter', 'gold_ounce', 'usdt']}


def frame(**changes):
    data = {'e': 'depthUpdate', 'E': int(NOW.timestamp() * 1000), 's': 'USDTIRT',
            'b': [['261000', '2']], 'a': [['261100', '3']]}
    data.update(changes)
    return json.dumps({'stream': TABDEAL_STREAM, 'data': data})


def post(extra='', clock='۱۵:۲۹', date='۱۴۰۵/۰۸/۱۹', forwarded=False):
    text = f'''💵دلار: 263000 تومان
__________________
🟡طلای ۱۸ عیار: 25000000 تومان
🔻حباب طلا ۱۸ عیار: -1.34%
🟡طلای دست دوم: 24000000 تومان
🟡تتر: 262000 تومان
🟡هر گرم نقره: 460000 تومان
🟡سکه بهار آزادی: 248000000 تومان
🟡سکه امامی: 255000000 تومان
🟡نیم سکه: 134000000 تومان
🟡ربع سکه: 72000000 تومان
🔻اونس طلا: 4074 دلار
ساعت: {clock}
تاریخ: {date}
{extra}'''
    forward = '<span class="tgme_widget_message_forwarded_from">upstream</span>' if forwarded else ''
    return f'<div class="tgme_widget_message">{forward}<div class="tgme_widget_message_text">{text}</div><time datetime="2026-11-10T12:00:00Z"></time></div>'


class TabdealTests(unittest.TestCase):
    def test_toman_midpoint_with_source_event_time(self):
        quotes = extract(frame(), STREAM, NOW.isoformat())
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual((q.price, q.currency, q.unit, q.kind), (261050, 'IRT', 'USDT', 'midpoint'))
        self.assertEqual(iso(q.published_at), NOW)
        self.assertEqual(q.time_basis, 'source')

    def test_wrong_symbol_dollar_market_or_futures_rejected(self):
        for symbol in ('USDTUSDT', 'BTCIRT', 'BTCUSDT', 'USDT_IRT'):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                extract(frame(s=symbol), STREAM, NOW.isoformat())
        payload = json.loads(frame()); payload['stream'] = 'special_margin-USDTIRT-depth-2000ms'
        with self.assertRaises(ValueError): extract(json.dumps(payload), STREAM, NOW.isoformat())

    def test_no_server_or_observed_time_substitution(self):
        for stamp in (None, 0, True, int(NOW.timestamp()), str(int(NOW.timestamp()*1000))):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError): extract(frame(E=stamp), STREAM, NOW.isoformat())
        payload = json.loads(frame()); del payload['data']['E']; payload['serverTime'] = int(NOW.timestamp()*1000)
        with self.assertRaises(ValueError): extract(json.dumps(payload), STREAM, NOW.isoformat())

    def test_zero_crossed_nonfinite_and_malformed_levels_rejected(self):
        for bids, asks in (([], [['2','1']]), ([['0','1']], [['2','1']]),
                           ([['3','1']], [['2','1']]), ([['NaN','1']], [['2','1']]),
                           ([['1','-1']], [['2','1']]), ([['1','Infinity']], [['2','1']]),
                           ([['1','0']], [['2','1']]), ([['1']], [['2','1']])):
            with self.subTest(bids=bids), self.assertRaises(ValueError): depth_midpoint(bids, asks)

    def test_best_levels_not_dom_order_and_zero_quantity_ignored(self):
        self.assertEqual(depth_midpoint([['10','2'],['12','1'],['100','0']], [['16','2'],['14','1']]), 13)

    def test_nobitex_rial_and_tabdeal_toman_agree(self):
        nobitex = {'id':'nobitex','group':'nobitex','url':'https://apiv2.nobitex.ir/v3/orderbook/USDTIRT','adapter':'nobitex','kind':'midpoint'}
        body = json.dumps({'status':'ok','bids':[['2610000','2']],'asks':[['2611000','3']],'lastUpdate':int(NOW.timestamp()*1000)})
        self.assertEqual(extract(body,nobitex,NOW.isoformat())[0].price, extract(frame(),STREAM,NOW.isoformat())[0].price)

    def test_bounded_stream_skips_ack_and_closes(self):
        connection = Mock(); connection.recv.side_effect = ['{"result":null,"id":1}', frame()]
        with patch('marketfeed.__main__.create_connection', return_value=connection):
            body,status = read_source(STREAM, 5)
        self.assertEqual(json.loads(body)['data']['s'], 'USDTIRT')
        self.assertIsNone(status)
        connection.close.assert_called_once()
        self.assertEqual(json.loads(connection.send.call_args.args[0])['params'], [TABDEAL_STREAM])

    def test_stream_errors_and_ack_flood_always_close(self):
        for frames in ([json.dumps({'code':1,'msg':'denied'})], ['{"result":null,"id":1}']*16):
            connection=Mock();connection.recv.side_effect=frames
            with patch('marketfeed.__main__.create_connection',return_value=connection), self.assertRaises(ValueError):
                read_source(STREAM,5)
            connection.close.assert_called_once()

    def test_bad_snapshot_is_not_a_successful_collection(self):
        with patch('marketfeed.__main__.read_source',return_value=(frame(E=0).encode(),None)):
            quotes,state=collect(STREAM,5,1)
        self.assertEqual(quotes,[])
        self.assertEqual(state['status'],'error')

    def test_stream_timeout_falls_back_to_documented_trades(self):
        raw=json.dumps([{'price':'261000','qty':'2','time':int(NOW.timestamp()*1000)}]).encode()
        with patch('marketfeed.__main__.create_connection',side_effect=WebSocketTimeoutException('timeout')), \
             patch('marketfeed.__main__.read_https',return_value=(raw,200)) as http:
            quotes,state=collect(STREAM,5,1)
        http.assert_called_once_with(TABDEAL_TRADES_URL,5)
        self.assertEqual(state['status'],'parsed')
        self.assertEqual(state['http_status'],200)
        self.assertEqual(quotes[0].kind,'last_trade')
        self.assertEqual(iso(quotes[0].published_at),NOW)

    def test_rest_trade_time_not_response_or_server_time(self):
        old=NOW-timedelta(hours=12)
        raw=json.dumps([{'price':'261000','qty':'2','time':int(old.timestamp()*1000)}])
        q=extract(raw,STREAM,NOW.isoformat())[0]
        self.assertEqual(iso(q.published_at),old)
        self.assertEqual(q.kind,'last_trade')
        cfg=config();cfg['primary_assets']=['usdt'];cfg['tolerances']={'usdt':1000};cfg['sources']=[STREAM]
        report=build_report(cfg,[q],[],NOW)
        self.assertIsNone(report['prices']['usdt']['price'])
        self.assertEqual(report['sources']['tabdeal']['prices']['usdt']['reason'],'stale')

    def test_rest_response_latest_valid_trade_not_list_order(self):
        raw=json.dumps([{'price':'261000','qty':'2','time':int(NOW.timestamp()*1000)},
                        {'price':'260000','qty':'1','time':int((NOW-timedelta(minutes=5)).timestamp()*1000)}])
        self.assertEqual(extract(raw,STREAM,NOW.isoformat())[0].price,261000)

    def test_rest_unknown_time_zero_and_missing_trade_rejected(self):
        for rows in ([],[{'price':'261000','qty':'2'}],
                     [{'price':'0','qty':'2','time':int(NOW.timestamp()*1000)}]):
            with self.assertRaises(ValueError):extract(json.dumps(rows),STREAM,NOW.isoformat())


class DollarGoldTests(unittest.TestCase):
    def test_rate_bulletin_only_identifiable_assets(self):
        quotes=extract(post(),CHANNEL,NOW.isoformat())
        by_asset={q.asset:q for q in quotes}
        self.assertEqual(set(by_asset),set(CHANNEL['assets'])-{'silver_999'})
        self.assertEqual(by_asset['gold_18k'].price,25000000)
        self.assertEqual(by_asset['usdt'].price,262000)
        self.assertEqual(by_asset['usdt'].kind,'indicative')
        self.assertEqual(by_asset['gold_ounce'].currency,'USD')
        self.assertEqual(by_asset['gold_18k'].published_at,'2026-11-10T15:29:00+03:30')

    def test_tether_rial_conversion_not_cash_dollar(self):
        body=post().replace('تتر: 262000 تومان','تتر: 2620000 ریال')
        quotes=extract(body,CHANNEL,NOW.isoformat())
        self.assertEqual(next(q.price for q in quotes if q.asset=='usdt'),262000)
        self.assertNotIn('usd_cash',{q.asset for q in quotes})

    def test_ads_news_forwarded_and_missing_time_not_rates(self):
        for body in (post(extra='تبلیغات'),post(extra='خبر فوری'),post(extra='پیش‌بینی'),
                     post(forwarded=True),post(clock=''),post(date=''),
                     '<div class="tgme_widget_message"><div class="tgme_widget_message_text">خبر: قیمت طلا امروز بالا رفت</div></div>'):
            with self.subTest(body=body[:60]):self.assertEqual(extract(body,CHANNEL,NOW.isoformat()),[])

    def test_invalid_purity_and_zero_tether_not_invented(self):
        body=post().replace('طلای ۱۸ عیار','طلای ۸ عیار').replace('تتر: 262000','تتر: 0')
        assets={q.asset for q in extract(body,CHANNEL,NOW.isoformat())}
        self.assertNotIn('gold_18k',assets)
        self.assertNotIn('usdt',assets)
        self.assertNotIn('silver_999',assets)

    def test_latest_real_source_time_not_dom_or_repost_time(self):
        body=post(clock='۱۵:۲۹').replace('تتر: 262000','تتر: 262100')+post(clock='۱۴:۰۰')
        self.assertEqual(next(q.price for q in extract(body,CHANNEL,NOW.isoformat()) if q.asset=='usdt'),262100)


class ReferenceTests(unittest.TestCase):
    def report(self, quotes, sources):
        cfg=config();cfg['primary_assets']=['usdt'];cfg['tolerances']={'usdt':1000};cfg['sources']=sources
        return build_report(cfg,quotes,[],NOW)

    def test_same_nobitex_publisher_only_one_vote(self):
        quotes=[replace(quote(s, p),asset='usdt',currency='IRT',unit='USDT',purity=None,kind='midpoint')
                for s,p in [('api',100),('page',102),('tabdeal',104)]]
        sources=[{'id':s,'group':'tabdeal' if s=='tabdeal' else 'nobitex','url':'https://example.org/'+s} for s in ('api','page','tabdeal')]
        r=self.report(quotes,sources)
        self.assertEqual(r['prices']['usdt']['source_count'],2)
        self.assertEqual(sum(r['sources'][s]['prices']['usdt']['used_in_reference'] for s in ('api','page')),1)

    def test_last_trade_bid_ask_and_reported_not_mixed_with_midpoint(self):
        for kind in ('last_trade','bid','ask','indicative'):
            q=replace(quote('a'),asset='usdt',currency='IRT',unit='USDT',purity=None,kind=kind)
            r=self.report([q],[{'id':'a','group':'a','url':'https://example.org'}])
            self.assertIsNone(r['prices']['usdt']['price'])
            self.assertFalse(r['sources']['a']['prices']['usdt']['used_in_reference'])

    def test_unknown_upstream_comparison_only_and_stale_rejected(self):
        q=replace(quote('a'),asset='usdt',currency='IRT',unit='USDT',purity=None,kind='midpoint')
        source={'id':'a','group':'a','url':'https://example.org','reference_eligible':False}
        self.assertIsNone(self.report([q],[source])['prices']['usdt']['price'])
        source['reference_eligible']=True
        for stamp in ((NOW-timedelta(hours=2)).isoformat(),(NOW+timedelta(hours=1)).isoformat()):
            r=self.report([replace(q,published_at=stamp)],[source])
            self.assertIsNone(r['prices']['usdt']['price'])

    def test_documented_wss_only_and_no_unverified_sources_active(self):
        cfg=load_config('sources.json')
        new={s['id']:s for s in cfg['sources']}
        self.assertEqual(new['tabdeal_usdt']['kind'],'midpoint')
        self.assertFalse(new['telegram_dollargold24']['reference_eligible'])
        self.assertEqual(len([s for s in cfg['sources'] if s['group']=='nobitex']),1)
        self.assertFalse(any('4334467640' in s['url'] for s in cfg['sources']))


if __name__=='__main__':
    unittest.main()
