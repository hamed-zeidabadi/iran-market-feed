from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from marketfeed.adapters import extract
from marketfeed.__main__ import load_config
from marketfeed.monitoring import enrich
from marketfeed.report import build_report
from test_report import NOW, config, quote


class ResearchSourcesTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config('sources.json')

    def source(self, sid):
        return next(s for s in self.cfg['sources'] if s['id'] == sid)

    def trades(self, rows):
        return extract(json.dumps(rows), self.source('hitobit_usdt'), NOW.isoformat())

    def test_hitobit_newest_trade_toman_no_midpoint_vote(self):
        ms = int(NOW.timestamp() * 1000)
        quotes = self.trades([{'Price':'260000','Time':ms-1000,'BaseQuantity':1},
                              {'Price':'261000','Time':ms,'BaseQuantity':2}])
        self.assertEqual(len(quotes),1)
        self.assertEqual(quotes[0].price,261000)
        report = build_report(self.cfg,quotes,[],NOW)
        item = report['sources']['hitobit_usdt']['prices']['usdt']
        self.assertTrue(item['is_fresh'])
        self.assertEqual(item['quote_type'],'last_trade')
        self.assertFalse(item['used_in_reference'])
        self.assertIsNone(report['prices']['usdt']['price'])

    def test_hitobit_rejects_missing_time_sequence_seconds_zero_quantity(self):
        ms = int(NOW.timestamp() * 1000)
        for row in [None, {'Price':100,'BaseQuantity':1,'lastUpdateId':ms},
                    {'Price':100,'BaseQuantity':1,'Time':-62135596800000},
                    {'Price':100,'BaseQuantity':1,'Time':int(NOW.timestamp())},
                    {'Price':'NaN','BaseQuantity':1,'Time':ms},
                    {'Price':100,'BaseQuantity':0,'Time':ms}]:
            with self.subTest(row=row):self.assertEqual(self.trades([row]),[])

    def test_trade_time_does_not_refresh_on_new_fetch(self):
        quotes = self.trades([{'Price':100,'BaseQuantity':1,'Time':int((NOW-timedelta(days=1)).timestamp()*1000)}])
        report = build_report(self.cfg,quotes,[],NOW)
        self.assertEqual(report['sources']['hitobit_usdt']['prices']['usdt']['reason'],'stale')

    def test_goldapi_xag_snapshot_not_local_silver_or_published_time(self):
        data = {'symbol':'XAG','currency':'USD','exchangeRate':1,'price':51,'updatedAt':NOW.isoformat().replace('+00:00','Z')}
        quotes = extract(json.dumps(data),self.source('gold_api_xag'),NOW.isoformat())
        self.assertEqual(quotes[0].asset,'silver_ounce')
        self.assertIsNone(quotes[0].published_at)
        report = build_report(self.cfg,quotes,[],NOW)
        item = report['sources']['gold_api_xag']['global_prices']['silver_ounce']
        self.assertEqual(item['price'],51)
        self.assertEqual(item['currency'],'USD')
        self.assertEqual(item['unit'],'troy_ounce')
        self.assertEqual(item['time_basis'],'provider_snapshot')
        self.assertEqual(item['reason'],'snapshot_time_only')
        self.assertTrue(item['snapshot_is_recent'])
        self.assertFalse(item['is_fresh'])
        self.assertIsNone(item['published_at'])
        self.assertIsNone(report['prices']['silver_999']['price'])
        self.assertIsNone(report['global_prices']['silver_ounce']['price'])
        enrich(report,{},self.cfg,NOW)
        schema = json.loads(Path('docs/prices.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema,format_checker=FormatChecker()).validate(report)

    def test_snapshot_old_future_and_missing_cannot_look_recent(self):
        for timestamp in [(NOW-timedelta(days=1)).isoformat(),(NOW+timedelta(hours=1)).isoformat()]:
            q = extract(json.dumps({'symbol':'XAU','currency':'USD','exchangeRate':1,'price':4000,'updatedAt':timestamp}),self.source('gold_api'),NOW.isoformat())
            report = build_report(self.cfg,q,[],NOW)
            item = report['sources']['gold_api']['prices']['gold_ounce']
            self.assertFalse(item['snapshot_is_recent'])
            self.assertFalse(item['used_in_reference'])

    def test_wrong_goldapi_symbol_rejected(self):
        for changes in [{'symbol':'XAU'},{'currency':'IRT'},{'exchangeRate':2}]:
            data={'symbol':'XAG','currency':'USD','exchangeRate':1,'price':51,'updatedAt':NOW.isoformat(),**changes}
            with self.assertRaises(ValueError):extract(json.dumps(data),self.source('gold_api_xag'),NOW.isoformat())

    def test_swissquote_snapshot_never_vote_even_if_eligible_misconfigured(self):
        source=deepcopy(self.source('swissquote_xag'));source['reference_eligible']=True
        self.cfg['sources']=[source]
        body=json.dumps([{'ts':int(NOW.timestamp()*1000),'spreadProfilePrices':[{'bid':50,'ask':52}]}])
        quotes=extract(body,source,NOW.isoformat())
        self.assertEqual(quotes[0].price,51)
        report=build_report(self.cfg,quotes,[],NOW)
        self.assertEqual(report['global_prices']['silver_ounce']['source_count'],0)
        self.assertIsNone(report['sources']['swissquote_xag']['global_prices']['silver_ounce']['published_at'])

    def test_last_valid_with_demoted_contributor_is_invalidated(self):
        cfg=config()
        for s in cfg['sources']:s['assets']=['gold_18k']
        report=build_report(cfg,[quote('a',100),quote('b',101),quote('c',102)],[],NOW)
        state=enrich(report,{},cfg,NOW)
        cfg['sources'][0]['reference_eligible']=False
        report=build_report(cfg,[],[],NOW)
        enrich(report,state,cfg,NOW)
        self.assertIsNone(report['prices']['gold_18k']['last_valid'])
