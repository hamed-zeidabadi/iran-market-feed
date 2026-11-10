import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from marketfeed.__main__ import main, collect, load_config
from marketfeed.adapters import extract
from marketfeed.parsers import parse_lines, message_time
from marketfeed.report import build_report
from marketfeed.times import output_clock
from test_report import config, quote, NOW


class RegressionTests(unittest.TestCase):
    def test_exact_purity(self):
        source = {'id': 'a', 'group': 'a', 'url': 'https://example.org'}
        for purity in ('1', '8', '81', '118', '۱۸۱', '۱۸۸', '24', '۱۸ عیار / 740'):
            with self.subTest(purity=purity):
                self.assertEqual(parse_lines(f'طلای {purity} عیار: 100 تومان', source, NOW.isoformat()), [])
        for purity in ('18', '۱۸'):
            self.assertEqual(len(parse_lines(f'طلای {purity} عیار: 100 تومان', source, NOW.isoformat())), 1)

    def test_channel_offset_order(self):
        source = {'id': 'a', 'group': 'a', 'url': 'https://t.me/s/a', 'adapter': 'channel'}
        body = ''.join(f'<div class="tgme_widget_message"><time datetime="{stamp}"></time>'
                       f'<div class="tgme_widget_message_text">طلای ۱۸ عیار: {value} تومان</div></div>'
                       for stamp, value in [('2026-11-10T14:00:00+03:30', 100), ('2026-11-10T11:00:00Z', 101)])
        quotes = extract(body, source, NOW.isoformat())
        result = build_report(config(), quotes, [], NOW)
        self.assertEqual(result['prices']['gold_18k']['price'], 101)

    def tgju(self, body, profile=False):
        source = {'id': 'a', 'group': 'a', 'url': 'https://www.tgju.org/' + ('profile/geram18' if profile else ''),
                  'adapter': 'tgju', 'allow_observed_time': True}
        return build_report(config(), extract(body, source, NOW.isoformat()), [], NOW)

    def test_tgju_clock_only_not_today(self):
        body = '<span data-col="info.last_trade.PDrCotVal">1000</span><div>زمان ثبت آخرین نرخ ۱۴:۵۵:۰۰</div><time datetime="2026-11-10T11:55:00Z">خبر</time>'
        result = self.tgju(body, True)
        self.assertIsNone(result['prices']['gold_18k']['price'])
        self.assertIsNone(result['sources']['a']['prices']['gold_18k']['published_at'])

    def test_tgju_stale_today_clock(self):
        body = '<table><tr data-market-row="geram18" data-price="1000"><td><time datetime="2026-11-10T09:00:00+03:30"></time></td></tr></table>'
        result = self.tgju(body)
        self.assertEqual(result['sources']['a']['prices']['gold_18k']['reason'], 'stale')
        self.assertIsNone(result['prices']['gold_18k']['price'])

    def test_tgju_profile_explicit_trade_timestamp(self):
        body = '<span data-col="info.last_trade.PDrCotVal">1000</span><span data-col="info.last_trade.DTgreg">2026-11-10T15:29:00+03:30</span>'
        result = self.tgju(body, True)
        self.assertEqual(result['prices']['gold_18k']['price'], 100)

    def test_tgju_no_year_from_news(self):
        result = self.tgju('<p>خبر ۱۴۰۵</p><table><tr data-market-row="geram18" data-price="1000"><td>۱۹ آبان ۱۵:۲۹</td></tr></table>')
        self.assertIsNone(result['prices']['gold_18k']['price'])

    def test_estjt_publication_marker(self):
        source = {'id': 'a', 'group': 'a', 'url': 'https://www.estjt.ir/tv/', 'adapter': 'estjt'}
        body = '<span data-field="IRG18">100</span><footer>آخرین بروزرسانی قیمت: ۱۹ آبان ۱۴۰۵ - ۱۵:۲۹:۱۰</footer>'
        result = build_report(config(), extract(body, source, NOW.isoformat()), [], NOW)
        self.assertEqual(result['prices']['gold_18k']['price'], 100)
        self.assertEqual(result['prices']['gold_18k']['published_at'], '2026-11-10T11:59:10Z')

    def test_unknown_time_is_comparison_only(self):
        q = quote('a', 102, published_at=None, time_basis='unknown')
        result = build_report(config(), [q, quote('b', 100)], [], NOW)
        detail = result['sources']['a']['prices']['gold_18k']
        self.assertEqual(result['prices']['gold_18k']['price'], 100)
        self.assertEqual(detail['difference'], 2)
        self.assertFalse(detail['used_in_reference'])
        self.assertFalse(detail['is_fresh'])

    def test_invalid_numbers_never_exposed(self):
        for price in ('0', '-1', 'NaN', 'Infinity'):
            result = build_report(config(), [quote('a', Decimal(price))], [], NOW)
            d = result['sources']['a']['prices']['gold_18k']
            self.assertIsNone(d['price'])
            self.assertFalse(d['is_success'])
            json.dumps(result, allow_nan=False)

    def test_group_from_configuration(self):
        cfg = config(); cfg['sources'][1]['group'] = 'a'
        result = build_report(cfg, [quote('a'), quote('b', 101, group='pretend-independent')], [], NOW)
        self.assertEqual(result['prices']['gold_18k']['source_count'], 1)

    def test_duplicate_quote_offsets(self):
        result = build_report(config(), [quote('a', 100, published_at='2026-11-10T14:30:00+03:30'),
                                        quote('a', 101, published_at='2026-11-10T11:30:00Z')], [], NOW)
        self.assertEqual(result['prices']['gold_18k']['price'], 101)

    def test_schema_missing_same_keys(self):
        empty = build_report(config(), [], [], NOW)
        full = build_report(config(), [quote('a')], [], NOW)
        self.assertEqual(empty['prices']['gold_18k'].keys(), full['prices']['gold_18k'].keys())
        self.assertEqual(empty['sources']['a']['prices']['gold_18k'].keys(), full['sources']['a']['prices']['gold_18k'].keys())

    def test_disabled_source_not_public(self):
        cfg = config(); cfg['sources'][0]['enabled'] = False
        result = build_report(cfg, [quote('a')], [], NOW)
        self.assertNotIn('a', result['sources'])

    def test_ounce_difference_no_rial(self):
        cfg = config(); cfg['primary_assets'] = ['gold_ounce']; cfg['tolerances'] = {'gold_ounce': 1}
        quotes = [replace(quote(s, value), asset='gold_ounce', currency='USD', unit='troy_ounce', purity=None)
                  for s, value in [('a', 4000), ('b', 4001), ('c', 4002)]]
        d = build_report(cfg, quotes, [], NOW)['sources']['a']['prices']['gold_ounce']
        self.assertEqual(d['difference'], -1)
        self.assertIsNone(d['difference_rial'])
        self.assertTrue(d['is_close'])

    def test_cli_uses_report_and_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config()
            primary = json.loads(Path('sources.json').read_text(encoding='utf-8'))['primary_assets']
            cfg['primary_assets'] = primary
            cfg['tolerances'] = {a: 1000 if a != 'gold_ounce' else 1 for a in primary}
            for s in cfg['sources']:
                s['enabled'] = True
                s['assets'] = ['gold_18k']
                s['url'] = 'https://example.org/' + s['id']
            cfg_path = Path(directory) / 'sources.json'; cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
            output = Path(directory) / 'prices.json'
            def collected(source, *args):
                return [quote(source['id'], {'a': 100, 'b': 101, 'c': 102}[source['id']])], {'id': source['id'], 'status': 'parsed', 'http_status': 200}
            with patch('sys.argv', ['marketfeed', '--config', str(cfg_path), '--output', str(output)]), \
                 patch('marketfeed.__main__.collect', side_effect=collected), \
                 patch('marketfeed.__main__.datetime') as dt, contextlib.redirect_stdout(io.StringIO()):
                dt.now.return_value = NOW
                main()
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['prices']['gold_18k']['price'], 101)
            self.assertEqual(result['successful_sources'], 3)
            self.assertFalse(output.with_suffix('.tmp').exists())

    def test_tgju_bubbles_and_retail_excluded(self):
        source = {'id':'a','group':'tgju','url':'https://t.me/s/tgjucoin','adapter':'channel','assets':['coin_emami']}
        body = '<div class="tgme_widget_message"><time datetime="2026-11-10T12:00:00Z"></time><div class="tgme_widget_message_text">سکه امامی................................<br>قیمت لحظه ای : 2,540,000,000 ریال<br>سکه امامی (تک فروشی): 2,550,000,000 ریال<br>حباب سکه امامی : 29,700,000 ریال</div></div>'
        quotes = extract(body, source, NOW.isoformat())
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].price, 254000000)

    def test_generic_bubble_not_a_price(self):
        source = {'id':'a','group':'a','url':'https://example.org'}
        self.assertEqual(parse_lines('حباب سکه امامی: 29700000 ریال', source, NOW.isoformat()), [])

    def test_tgju_gold_multiline_real_format(self):
        source = {'id':'a','group':'tgju','url':'https://t.me/s/tgjugold','adapter':'channel','assets':['gold_18k']}
        body = '<div class="tgme_widget_message"><time datetime="2026-11-10T11:59:30Z"></time><div class="tgme_widget_message_text">قیمت طلا و آبشده | سه شنبه 19 آبان 1405 - 15:29<br>قیمت طلای 18 عیار<br>........................<br>قیمت لحظه ای : 251,937,000 ریال<br>قیمت نقره<br>........................<br>قیمت لحظه ای : 4,607,600 ریال</div></div>'
        quotes = extract(body, source, NOW.isoformat())
        self.assertEqual([(q.asset,q.price) for q in quotes], [('gold_18k',Decimal('25193700'))])
        self.assertEqual(quotes[0].published_at,'2026-11-10T15:29:00+03:30')

    def test_nobitex_invalid_orderbook(self):
        source = {'id':'a','group':'a','url':'https://example.org','adapter':'nobitex'}
        for body in ('{"status":"failed","bids":[["100","1"]],"asks":[["101","1"]]}',
                     '{"status":"ok","bids":[["102","1"]],"asks":[["101","1"]]}'):
            with self.assertRaises(ValueError): extract(body, source, NOW.isoformat())

    def test_ontala_scoped_update_time(self):
        source={'id':'a','group':'a','url':'https://ontala.ir/','adapter':'ontala','assets':['gold_18k']}
        body='<div class="bb-last"><strong>۱۴۰۵/۰۸/۱۹ ۱۵:۲۹</strong></div><p>یک گرم طلای ۱۸ عیار</p><p>100 تومان</p><p>خرید طلای ۱۸ عیار: 98 تومان</p>'
        quotes=extract(body,source,NOW.isoformat())
        self.assertEqual(len(quotes),1)
        self.assertEqual(quotes[0].price,100)
        self.assertEqual(quotes[0].published_at,'2026-11-10T15:29:00+03:30')


class ClockTests(unittest.TestCase):
    def test_contract_one_instant(self):
        value = output_clock(datetime(2026, 11, 10, 17, 52, 0, 999, tzinfo=timezone.utc))
        self.assertEqual(value, {'date': '1405/08/19', 'time': '21:22', 'datetime': '2026-11-10T17:52:00Z',
                                 'timestamp': int(datetime(2026, 11, 10, 17, 52, tzinfo=timezone.utc).timestamp()), 'timezone': 'Asia/Tehran'})

    def test_tehran_day_boundary(self):
        before = datetime(2026, 11, 10, 20, 29, 59, tzinfo=timezone.utc)
        self.assertEqual(output_clock(before)['date'], '1405/08/19')
        after = output_clock(before + timedelta(seconds=1))
        self.assertEqual((after['date'], after['time']), ('1405/08/20', '00:00'))

    def test_nowruz_and_leap(self):
        self.assertEqual(output_clock(datetime(2025, 3, 20, 12, tzinfo=timezone.utc))['date'], '1403/12/30')
        self.assertEqual(output_clock(datetime(2025, 3, 21, 12, tzinfo=timezone.utc))['date'], '1404/01/01')
        self.assertIsNone(message_time('تاریخ: ۱۴۰۴/۱۲/۳۰ ساعت: ۱۲:۰۰'))

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError): output_clock(datetime(2026, 11, 10))


if __name__ == '__main__':
    unittest.main()
