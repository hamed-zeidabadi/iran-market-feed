import unittest
from datetime import timedelta
from dataclasses import replace
from marketfeed.report import build_report
from marketfeed.monitoring import enrich, market_status
from test_report import NOW, config, quote


class MonitoringTests(unittest.TestCase):
    def report(self, quotes, now=NOW, previous=None):
        cfg = config()
        for source in cfg['sources']:
            source['assets'] = ['gold_18k']
        report = build_report(cfg, quotes, [], now)
        state = enrich(report, previous or {}, cfg, now)
        return report, state

    def test_old_vote_visible_even_when_latest_is_now(self):
        old = quote('a', 100, published_at=(NOW-timedelta(minutes=80)).isoformat())
        report, _ = self.report([old, quote('b',101), quote('c',102)])
        item = report['prices']['gold_18k']
        self.assertEqual(item['max_contributor_age_seconds'], 4800)
        self.assertEqual(item['oldest_contributor_at'], old.published_at.replace('+00:00','Z'))
        self.assertEqual(item['reference_range'], {'min':100,'max':102})

    def test_disputed_cannot_overwrite_last_valid_or_fill_live_price(self):
        _, state = self.report([quote('a',100),quote('b',101),quote('c',102)])
        report, state2 = self.report([quote('a',100),quote('b',200)], NOW+timedelta(minutes=5),state)
        self.assertEqual(report['prices']['gold_18k']['last_valid']['price'],101)
        self.assertIn('source_disagreement', report['prices']['gold_18k']['quality_warnings'])
        report, _ = self.report([], NOW+timedelta(hours=2),state2)
        self.assertIsNone(report['prices']['gold_18k']['price'])
        self.assertEqual(report['prices']['gold_18k']['last_valid']['age_seconds'],7200)

    def test_cadence_and_unchanged_warning_need_peer_movement(self):
        _, state = self.report([quote('a',100),quote('b',101)])
        later = NOW+timedelta(hours=2)
        qs = [replace(quote('a',100), published_at=later.isoformat(),fetched_at=later.isoformat()),
              replace(quote('b',102), published_at=later.isoformat(),fetched_at=later.isoformat())]
        report, _ = self.report(qs,later,state)
        item = report['sources']['a']['prices']['gold_18k']['monitoring']
        self.assertTrue(item['suspicious_unchanged'])
        self.assertEqual(item['median_publication_interval_seconds'],7200)
        qs[1] = replace(qs[1], price=quote('b',101).price)
        report, _ = self.report(qs,later,state)
        self.assertFalse(report['sources']['a']['prices']['gold_18k']['monitoring']['suspicious_unchanged'])

    def test_source_asset_override_does_not_change_other_sources(self):
        cfg = config()
        cfg['sources'][0]['asset_policies'] = {'gold_18k': {'max_age_seconds':60}}
        stamp = (NOW-timedelta(minutes=2)).isoformat()
        report = build_report(cfg,[quote('a',published_at=stamp),quote('b',published_at=stamp)],[],NOW)
        self.assertFalse(report['sources']['a']['prices']['gold_18k']['used_in_reference'])
        self.assertTrue(report['sources']['b']['prices']['gold_18k']['used_in_reference'])

    def test_calendar_is_not_guessed(self):
        self.assertEqual(market_status('usdt',NOW,{})['status'],'open')
        self.assertEqual(market_status('gold_18k',NOW,{})['status'],'unknown')

    def test_skipped_runs_do_not_count_as_failures(self):
        cfg = config()
        for source in cfg['sources']:source['assets'] = ['gold_18k']
        report = build_report(cfg,[],[],NOW)
        enrich(report,{},cfg,NOW)
        self.assertEqual(report['sources']['a']['prices']['gold_18k']['monitoring']['samples'],0)

    def test_stable_day_does_not_warn_without_fresh_publication_and_peer_change(self):
        _, state = self.report([quote('a',100),quote('b',101)])
        later = NOW+timedelta(days=1)
        qs = [replace(quote('a',100), published_at=later.isoformat(),fetched_at=later.isoformat()),
              replace(quote('b',101), published_at=later.isoformat(),fetched_at=later.isoformat())]
        report, _ = self.report(qs,later,state)
        self.assertFalse(report['sources']['a']['prices']['gold_18k']['monitoring']['suspicious_unchanged'])

    def test_enriched_full_schema(self):
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker
        from marketfeed.__main__ import load_config
        cfg = load_config('sources.json')
        report = build_report(cfg,[],[],NOW)
        enrich(report,{},cfg,NOW)
        Draft202012Validator(json.loads(Path('docs/prices.schema.json').read_text(encoding='utf-8')),format_checker=FormatChecker()).validate(report)
