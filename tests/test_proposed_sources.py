from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest

from marketfeed.adapters import extract
from marketfeed.__main__ import load_config
from marketfeed.report import build_report


class ProposedSourcesTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config('sources.json')
        self.sources = {s['id']: s for s in self.config['sources']}
        self.now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    def parse(self, name, payload):
        return extract(payload, self.sources[name], self.now.isoformat())

    def test_widget_mapping_and_undocumented_time(self):
        rows = [{'item_id': 137121, 'name': 'geram18', 'p': '246309000',
                 'updated_at': '2026-11-15 12:43:52'},
                {'item_id': 137138, 'name': 'sekee', 'p': '2510600000', 'updated_at': '2026-11-16 15:20:00'},
                {'item_id': 137119, 'name': 'ons', 'p': '3871.25', 'updated_at': '2026-11-16 15:20:00'},
                {'item_id': 137123, 'name': 'silver', 'p': '47.74', 'updated_at': '2026-11-16 15:20:00'},
                {'item_id': 137139, 'name': 'wrong', 'p': '1323100000'},
                {'item_id': 137120, 'name': 'mesghal', 'p': '1066950000'}]
        quotes = self.parse('tgju_widget', json.dumps({'response': {'indicators': rows}}))
        self.assertEqual({q.asset: q.price for q in quotes},
                         {'gold_18k': Decimal('24630900'), 'coin_emami': Decimal('251060000'),
                          'gold_ounce': Decimal('3871.25'), 'silver_ounce': Decimal('47.74')})
        self.assertTrue(all(q.published_at is None for q in quotes))
        result = build_report(self.config, quotes, [], self.now)
        self.assertFalse(result['sources']['tgju_widget']['prices']['gold_18k']['used_in_reference'])
        self.assertEqual(self.sources['tgju_widget']['group'], self.sources['tgju_web']['group'])

    def test_snapshot_reference_and_expiration(self):
        data = {'gold18': 24659000, 'verifiedAtUtc': '2026-11-16T11:55:00Z', 'fresh': True}
        quotes = self.parse('ordibehesht_gold', json.dumps(data))
        result = build_report(self.config, quotes, [], self.now)
        item = result['sources']['ordibehesht_gold']['prices']['gold_18k']
        self.assertTrue(item['used_in_reference'])
        self.assertEqual(result['prices']['gold_18k']['price'], 24659000)
        self.assertIsNone(result['prices']['gold_18k']['published_at'])
        self.assertIn('snapshot_contributor', result['prices']['gold_18k']['quality_warnings'])
        data['verifiedAtUtc'] = '2026-11-15T11:55:00Z'
        result = build_report(self.config, self.parse('ordibehesht_gold', json.dumps(data)), [], self.now)
        self.assertFalse(result['sources']['ordibehesht_gold']['prices']['gold_18k']['used_in_reference'])

    def test_ordibehesht_verification_is_only_snapshot(self):
        data = {'gold18': 24659000, 'verifiedAtUtc': '2026-11-16T11:28:13.3998155Z', 'fresh': True}
        quotes = self.parse('ordibehesht_gold', json.dumps(data))
        self.assertEqual(quotes[0].price, Decimal('24659000'))
        self.assertIsNone(quotes[0].published_at)
        self.assertEqual(quotes[0].time_basis, 'provider_snapshot')
        data['fresh'] = False
        self.assertEqual(self.parse('ordibehesht_gold', json.dumps(data)), [])

    def test_bazaretala_exact_row_and_ambiguous_time(self):
        row = '<tr class="tr-price" data-slug="geram18"><th class="ptitle">طلای ۱۸ عیار</th><td class="p">247,202,000</td><td class="t">۱۵:۰۷:۴۳</td></tr>'
        quotes = self.parse('bazaretala_gold', '<table>' + row + '</table>')
        self.assertEqual(quotes[0].price, Decimal('24720200'))
        self.assertIsNone(quotes[0].published_at)
        self.assertEqual(self.parse('bazaretala_gold', '<table>' + row + row + '</table>'), [])

    def test_ordibehesht_page_fallback_only_on_api_failure(self):
        html = '''<span id="last-verified"><bdi>دوشنبه ۲۵ آبان ۱۴۰۵ ساعت ۰۳:۲۰ عصر</bdi></span>
        <div id="gold-price-body"><div class="price-pill"><span class="product-name">هر گرم طلای ۱۸ عیار</span>
        <span class="price-value">۲۴,۶۲۳,۰۰۰ تومان</span></div>
        <div class="price-pill"><span class="product-name">نیم‌سکه</span><span class="price-value">۱۳۱,۳۰۰,۰۰۰ تومان</span></div></div>'''
        quotes = self.parse('ordibehesht_page', html)
        self.assertEqual({q.asset: q.price for q in quotes}, {'gold_18k': 24623000, 'coin_half': 131300000})
        self.assertEqual(quotes[0].snapshot_at, '2026-11-16T15:20:00+03:30')
        for status, expected in [('error', True), ('parser_error', True), ('parsed', False), ('no_quotes', False)]:
            result = build_report(self.config, quotes, [{'id': 'ordibehesht_gold', 'status': status}], self.now)
            self.assertEqual(result['sources']['ordibehesht_page']['prices']['gold_18k']['used_in_reference'], expected)
            self.assertFalse(result['sources']['ordibehesht_page']['prices']['coin_half']['used_in_reference'])
        stale = build_report(self.config, quotes, [{'id': 'ordibehesht_gold', 'status': 'error'}],
                             datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertFalse(stale['sources']['ordibehesht_page']['prices']['gold_18k']['used_in_reference'])

    def test_baazar_platform_buy_is_only_comparison(self):
        data = {'error': None, 'data': {'buyPrice': 247013476, 'sellPrice': 244704967,
                                       'currentTime': 1794833700000, 'prices': []}}
        quotes = self.parse('baazar_gold', json.dumps(data))
        self.assertEqual(quotes[0].price, Decimal('24701347.6'))
        self.assertEqual(quotes[0].kind, 'platform_buy')
        self.assertIsNone(quotes[0].published_at)
        result = build_report(self.config, quotes, [], self.now)
        detail = result['sources']['baazar_gold']['prices']['gold_18k']
        self.assertEqual(detail['reason'], 'snapshot_time_only')
        self.assertFalse(detail['used_in_reference'])
        self.assertIsNone(result['prices']['gold_18k']['price'])
        for change in ({'buyPrice': 1}, {'sellPrice': 0}, {'currentTime': True}, {'currentTime': 1794833700}):
            bad = {'error': None, 'data': {**data['data'], **change}}
            self.assertEqual(self.parse('baazar_gold', json.dumps(bad)), [])

    def test_digikala_observed_time_is_explicit(self):
        payload = json.dumps({'gold18': {'price': 247015, 'ttl': 60},
                              'silver999': {'price': 4278, 'ttl': 60}})
        quotes = self.parse('digikala_wealth', payload)
        self.assertEqual({q.asset: q.price for q in quotes},
                         {'gold_18k': Decimal('24701500'), 'silver_999': Decimal('427800')})
        self.assertTrue(all(q.published_at is None and q.time_basis == 'observed_only' for q in quotes))
        result = build_report(self.config, quotes, [], self.now)
        for asset in ('gold_18k', 'silver_999'):
            item = result['sources']['digikala_wealth']['prices'][asset]
            self.assertTrue(item['used_in_reference'])
            self.assertFalse(item['is_fresh'])
            self.assertTrue(result['prices'][asset]['uses_observed_time'])
            self.assertIsNone(result['prices'][asset]['published_at'])
            self.assertIn('observed_time_contributor', result['prices'][asset]['quality_warnings'])
        stale = build_report(self.config, quotes, [], datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertFalse(stale['sources']['digikala_wealth']['prices']['gold_18k']['used_in_reference'])
        bad = self.parse('digikala_wealth', json.dumps({'gold18': {'price': True}, 'silver999': {'price': -1}}))
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
