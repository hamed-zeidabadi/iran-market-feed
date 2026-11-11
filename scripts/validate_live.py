"""Validate real CLI output repeatedly, keeping only non-price audit summaries.

Run: python scripts/validate_live.py --runs 3 --interval 180
The public JSON is overwritten; no price history is retained by this script.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from statistics import median
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marketfeed.core import ASSETS, iso
from marketfeed.times import output_clock


def validate(report, config, require_all_fresh=True):
    schema = json.loads((ROOT / 'docs/prices.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    instant = iso(report['datetime'])
    assert all(report[key] == value for key, value in output_clock(instant).items())
    assert type(report['timestamp']) is int
    assert set(report['prices']) == set(config['primary_assets'])
    assert set(report['parsian']) == set(config.get('parsian_assets', []))
    references = report['prices'] | report['parsian']
    active = {s['id']: s for s in config['sources'] if s['enabled']}
    assert set(report['sources']) == set(active)
    assert report['source_count'] == len(active)
    assert report['successful_sources'] == sum(s['is_success'] for s in report['sources'].values())
    if require_all_fresh:
        assert report['successful_sources'] == len(active)
    assert report['refresh_interval_seconds'] == 600
    for source_id, source in report['sources'].items():
        expected_statuses = (None, 200) if active[source_id]['url'].startswith('wss://') else (200,)
        if require_all_fresh:
            assert source['is_success'] and source['status'] == 'parsed' and source['http_status'] in expected_statuses, source_id
        assert set(source['prices']) == set(config['primary_assets'])
        assert set(source['parsian']) == set(config.get('parsian_assets', []))
        details = source['prices'] | source['parsian']
        valid = [d for d in details.values() if d['is_success']]
        assert source['is_success'] == bool(valid)
        if require_all_fresh:
            assert valid and all(d['is_fresh'] and d['published_at'] for d in valid), source_id
        for detail in valid:
            if require_all_fresh or detail['is_fresh']:
                age = (instant - iso(detail['published_at'])).total_seconds()
                assert -config['policy']['future_tolerance_seconds'] <= age <= config['policy']['max_age_seconds'], source_id
        if require_all_fresh:
            assert {a for a, d in details.items() if d['is_success']} == set(active[source_id]['assets']), source_id
        for asset, detail in details.items():
            assert type(detail['is_success']) is bool
            assert detail['currency'] == ASSETS[asset]['currency']
            assert detail['unit'] == ASSETS[asset]['unit']
            reference = references[asset]['price']
            if detail['used_in_reference']:
                assert detail['is_usable'] and detail['is_fresh'] and detail['published_at']
                assert detail['quote_type'] == references[asset]['quote_type']
                assert source_id in references[asset]['contributors']
            if detail['price'] is None or reference is None:
                assert detail['difference'] is detail['difference_rial'] is detail['is_close'] is None
                continue
            difference = Decimal(str(detail['price'])) - Decimal(str(reference))
            assert abs(Decimal(str(detail['difference'])) - difference) <= Decimal('0.000001')
            assert detail['is_close'] == (abs(difference) <= Decimal(str(references[asset]['tolerance'])))
            if detail['currency'] == 'IRT':
                assert abs(Decimal(str(detail['difference_rial'])) - difference * 10) <= Decimal('0.000001')
            else:
                assert detail['difference_rial'] is None
    for asset, price in references.items():
        if require_all_fresh and asset in config['primary_assets']:
            assert price['price'] is not None and price['price'] > 0, asset
        groups = [active[s]['group'] for s in price['contributors']]
        assert len(groups) == len(set(groups)) == price['source_count']
        assert not price['uses_observed_time']
        if not groups:
            assert price['price'] is None
            continue
        section = 'prices' if asset in config['primary_assets'] else 'parsian'
        expected = median(Decimal(str(report['sources'][s][section][asset]['price'])) for s in price['contributors'])
        assert abs(Decimal(str(price['price'])) - expected) <= Decimal('0.000001'), asset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--interval', type=int, default=180, help='Minimum seconds between run starts')
    parser.add_argument('--allow-unavailable', action='store_true', help='Validate integrity while reporting stale/missing sources honestly')
    args = parser.parse_args()
    if args.runs < 1 or (args.runs > 1 and args.interval < 120):
        parser.error('Use at least 120 seconds between repeated runs')
    audit = ROOT / '.validation'
    audit.mkdir(exist_ok=True)
    summaries = []
    for index in range(args.runs):
        started = time.monotonic()
        subprocess.run([sys.executable, '-X', 'utf8', '-m', 'marketfeed'], cwd=ROOT, check=True)
        raw = (ROOT / 'public/api/prices.json').read_bytes()
        report = json.loads(raw)
        config = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
        validate(report, config, require_all_fresh=not args.allow_unavailable)
        summaries.append({
            'run': index + 1, 'datetime': report['datetime'], 'successful_sources': report['successful_sources'],
            'source_count': report['source_count'], 'statuses': {a: p['status'] for a, p in report['prices'].items()},
            'source_times': {s: {a: d['published_at'] for a, d in v['prices'].items() if d['is_success']}
                             for s, v in report['sources'].items()},
            'sha256': hashlib.sha256(raw).hexdigest(),
        })
        (audit / 'summary.json').write_text(json.dumps(summaries, indent=2) + '\n', encoding='utf-8')
        print(f'VALIDATED {index + 1}/{args.runs}: {report["datetime"]}, {report["successful_sources"]}/{report["source_count"]}', flush=True)
        if index + 1 < args.runs:
            remaining = max(0, args.interval - (time.monotonic() - started))
            print(f'Waiting {remaining:.0f}s before next full run', flush=True)
            while remaining > 0:
                wait = min(30, remaining)
                time.sleep(wait)
                remaining -= wait


if __name__ == '__main__':
    main()
