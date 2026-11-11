import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import yaml
from marketfeed.__main__ import load_config
from marketfeed.core import PRIMARY_ASSETS
from marketfeed.report import build_report
from test_report import quote, NOW


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config('sources.json')
        self.schema = json.loads(Path('docs/prices.schema.json').read_text(encoding='utf-8'))
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def test_empty_full_schema(self):
        self.validator.validate(build_report(self.config, [], [], NOW))

    def test_schema_rejects_string_price_and_milliseconds(self):
        result = build_report(self.config, [], [], NOW)
        result['prices']['gold_18k']['price'] = '100'
        with self.assertRaises(ValidationError): self.validator.validate(result)
        result['prices']['gold_18k']['price'] = None
        result['timestamp'] = '1794300000000'
        with self.assertRaises(ValidationError): self.validator.validate(result)

    def test_exact_primary_assets(self):
        self.assertEqual(set(self.config['primary_assets']), set(PRIMARY_ASSETS))
        self.assertNotIn('usd_cash', self.config['primary_assets'])

    def test_schema_no_raw_responses(self):
        result = build_report(self.config, [], [], NOW)
        result['raw_responses'] = {}
        with self.assertRaises(ValidationError): self.validator.validate(result)

    def test_workflow_loads_with_expected_deployment_contract(self):
        workflow = yaml.load(Path('.github/workflows/update.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on']['schedule'][0]['cron'], '7,17,27,37,47,57 * * * *')
        self.assertEqual(workflow['permissions']['contents'], 'read')
        self.assertEqual(workflow['permissions']['pages'], 'write')
        self.assertEqual(workflow['permissions']['id-token'], 'write')
        steps = workflow['jobs']['collect']['steps']
        self.assertTrue(any(s.get('run') == 'python -m marketfeed' for s in steps))
        self.assertTrue(any(s.get('with', {}).get('path') == 'public' for s in steps))
        self.assertEqual(workflow['jobs']['deploy']['needs'], 'collect')
        self.assertTrue(any(s.get('uses', '').startswith('actions/deploy-pages@') for s in workflow['jobs']['deploy']['steps']))
        self.assertFalse(any('git commit' in s.get('run', '') for s in steps))

    def test_schedule_intervals_match_public_refresh_contract(self):
        workflow = yaml.load(Path('.github/workflows/update.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        cron = workflow['on']['schedule'][0]['cron'].split()
        self.assertEqual(cron[1:], ['*', '*', '*', '*'])
        minutes = sorted(map(int, cron[0].split(',')))
        gaps = [(minutes[(i + 1) % len(minutes)] - minute) % 60 for i, minute in enumerate(minutes)]
        self.assertEqual(gaps, [10] * 6)
        report = build_report(self.config, [], [], NOW)
        self.assertEqual(report['refresh_interval_seconds'], gaps[0] * 60)
        self.assertIn('workflow_dispatch', workflow['on'])
        self.assertLess(sum(int(workflow['jobs'][j]['timeout-minutes']) for j in ('collect', 'deploy')), 10)


if __name__ == '__main__':
    unittest.main()
