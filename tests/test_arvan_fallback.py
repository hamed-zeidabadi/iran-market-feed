from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
import unittest

from marketfeed.adapters import extract
from marketfeed.__main__ import load_config
from marketfeed.parsers import quote
from marketfeed.report import build_report

NOW = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


def payload(source='ramzinex', **changes):
    row = {'asset': 'usdt', 'price': 270100, 'currency': 'IRT', 'unit': 'USDT',
           'source': source, 'time_basis': 'book_level_change', 'source_time': NOW.isoformat(),
           'observed_at': NOW.isoformat(), 'quote_type': 'midpoint'}
    row.update(changes)
    return json.dumps({'sources': {source: {'status': 'parsed', 'quotes': [row]}}})


class ArvanFallbackTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config('sources.json')
        self.sources = {s['id']: s for s in self.cfg['sources']}

    def parse(self, body, name='arvan_ramzinex'):
        return extract(body, self.sources[name], NOW.isoformat())

    def test_fallback_and_direct_preference_keep_one_vote(self):
        fallback = self.parse(payload())[0]
        report = build_report(self.cfg, [fallback], [], NOW)
        self.assertEqual(report['prices']['usdt']['contributors'], ['arvan_ramzinex'])
        direct = quote(self.sources['ramzinex_usdt'], 'usdt', 270200,
                       (NOW - timedelta(seconds=20)).isoformat(), NOW.isoformat(), kind='midpoint')
        report = build_report(self.cfg, [direct, fallback], [], NOW)
        self.assertEqual(report['prices']['usdt']['contributors'], ['ramzinex_usdt'])
        self.assertEqual(report['prices']['usdt']['source_count'], 1)
        stale_direct = replace(direct, published_at=(NOW - timedelta(hours=2)).isoformat())
        report = build_report(self.cfg, [stale_direct, fallback], [], NOW + timedelta(minutes=6))
        self.assertIsNone(report['prices']['usdt']['price'])

    def test_untrusted_data_and_stale_observation_rejected(self):
        for changes in ({'price': 0}, {'price': True}, {'currency': 'USD'}, {'unit': 'gram'},
                        {'source': 'nobitex'}, {'quote_type': 'last_trade'}, {'source_time': None},
                        {'observed_at': (NOW - timedelta(minutes=10)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertEqual(self.parse(payload(**changes)), [])
        doc = json.loads(payload()); doc['sources']['ramzinex']['quotes'] *= 2
        self.assertEqual(self.parse(json.dumps(doc)), [])

    def test_aggregator_exchange_and_snapshot(self):
        body = payload('arzdigital', time_basis='provider_snapshot', exchange='wallex')
        self.assertEqual(self.parse(body, 'arvan_arzdigital_nobitex'), [])
        q = self.parse(body, 'arvan_arzdigital_wallex')[0]
        self.assertEqual(q.time_basis, 'provider_snapshot')
        report = build_report(self.cfg, [q], [], NOW)
        self.assertIn('snapshot_contributor', report['prices']['usdt']['quality_warnings'])


if __name__ == '__main__':
    unittest.main()
