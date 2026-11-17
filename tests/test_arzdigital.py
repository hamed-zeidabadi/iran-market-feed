from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from marketfeed.adapters import extract
from marketfeed.__main__ import load_config
from marketfeed.parsers import quote
from marketfeed.report import build_report


NOW = datetime(2026, 11, 17, 6, 10, tzinfo=timezone.utc)


def row(exchange='nobitex'):
    return {'contract_type': 'spot', 'deprecated': False, 'bid': 270600, 'ask': 270790,
            'updated_at': int(NOW.timestamp()),
            'source': {'slug': exchange + '-spot', 'deprecated': False,
                       'exchange': {'slug': exchange, 'deprecated': False}},
            'base': {'slug': 'tether', 'symbol': 'USDT'},
            'quote': {'slug': 'ir-toman', 'symbol': 'IRT'}}


class ArzdigitalTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config('sources.json')
        self.sources = {s['id']: s for s in self.cfg['sources']}

    def parse(self, rows, source='arzdigital_nobitex'):
        return extract(json.dumps({'status': 'success', 'data': rows}), self.sources[source], NOW.isoformat())

    def test_midpoint_snapshot_with_correct_units_and_group(self):
        q = self.parse([row()])[0]
        self.assertEqual(q.price, Decimal('270695'))
        self.assertEqual((q.kind, q.currency, q.group), ('midpoint', 'IRT', 'nobitex'))
        self.assertEqual(q.time_basis, 'provider_snapshot')
        self.assertIsNone(q.published_at)
        report = build_report(self.cfg, [q], [], NOW)
        self.assertTrue(report['sources']['arzdigital_nobitex']['prices']['usdt']['used_in_reference'])
        self.assertIn('snapshot_contributor', report['prices']['usdt']['quality_warnings'])

    def test_invalid_crossed_zero_duplicate_or_other_market_rejected(self):
        for change in ({'bid': 0}, {'bid': 271000}, {'ask': True}, {'updated_at': 0},
                       {'contract_type': 'otc'}, {'deprecated': True},
                       {'quote': {'slug': 'tether', 'symbol': 'USDT'}}):
            with self.subTest(change=change):
                self.assertEqual(self.parse([{**row(), **change}]), [])
        self.assertEqual(self.parse([row(), row()]), [])
        self.assertEqual(self.parse([row('wallex')]), [])

    def test_old_snapshot_is_rejected_and_direct_source_preferred(self):
        old = row(); old['updated_at'] = int((NOW - timedelta(minutes=6)).timestamp())
        quote_old = self.parse([old])[0]
        report = build_report(self.cfg, [quote_old], [], NOW)
        self.assertFalse(report['sources']['arzdigital_nobitex']['prices']['usdt']['used_in_reference'])
        self.assertEqual(report['sources']['arzdigital_nobitex']['prices']['usdt']['reason'], 'stale')
        fallback = self.parse([row()])[0]
        direct = quote(self.sources['nobitex_usdt'], 'usdt', 270700, NOW.isoformat(), NOW.isoformat(), kind='midpoint')
        report = build_report(self.cfg, [fallback, direct], [], NOW)
        self.assertEqual(report['prices']['usdt']['source_count'], 1)
        self.assertEqual(report['prices']['usdt']['contributors'], ['nobitex_usdt'])
        report = build_report(self.cfg, [fallback], [], NOW)
        self.assertEqual(report['prices']['usdt']['contributors'], ['arzdigital_nobitex'])


if __name__ == '__main__':
    unittest.main()
