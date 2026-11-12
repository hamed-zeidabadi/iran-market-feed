"""Private incident records. Never change prices, weights or acceptance rules."""
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import uuid

from .core import iso
from zoneinfo import ZoneInfo
TEHRAN = ZoneInfo('Asia/Tehran')

TRANSPORTS = ContextVar('market_transports', default=None)
SECTIONS = ('prices', 'parsian', 'global_prices')


def measure(value):
    result=float(value)
    return result if math.isfinite(result) else str(value)


def transport(route, status=None, error=None):
    trace = TRANSPORTS.get()
    if trace is not None:
        trace.append({'occurred_at': datetime.now(timezone.utc).isoformat(), 'route': route,
                      'http_status': status, 'error_type': type(error).__name__ if error else None,
                      'reason_type': type(getattr(error, 'reason', None)).__name__ if error else None})


class Journal:
    def __init__(self, path, phase='collection', config=None):
        self.path = Path(path)
        if self.path.resolve().is_relative_to(Path('public').resolve()):
            raise ValueError('incident journal must be outside public/')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('', encoding='utf-8')
        run = os.environ.get('GITHUB_RUN_ID') or ('local-' + uuid.uuid4().hex)
        attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
        self.context = {'log_version': 1, 'run_id': run, 'run_attempt': attempt, 'phase': phase,
                        'environment': 'github_actions' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'local',
                        'python_version': platform.python_version(),
                        'commit': os.environ.get('GITHUB_SHA'),
                        'run_url': f'https://github.com/hamed-zeidabadi/iran-market-feed/actions/runs/{run}' if run.isdigit() else None}
        self.batch_id = f'{run}:{attempt}:{phase}'
        self.context['batch_id'] = self.batch_id
        if config:
            self.context['config_sha256'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def emit(self, event, severity='info', **fields):
        now = datetime.now(timezone.utc)
        record = {**self.context, 'at': now.isoformat().replace('+00:00', 'Z'),
                  'at_tehran': now.astimezone(TEHRAN).isoformat(),
                  'event': event, 'severity': severity, **fields}
        # Defence in depth: known environment secrets never enter an artifact.
        text = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        for name, value in os.environ.items():
            if any(marker in name.upper() for marker in ('SECRET', 'TOKEN', 'PASSWORD', 'API_KEY')) and len(value) >= 8:
                text = text.replace(value, '[REDACTED]')
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(text + '\n')
        return record


def record_collection(journal, report, config, health, previous):
    """Log successes for denominators and issues with source/asset evidence."""
    configs = {s['id']: s for s in config['sources']}
    health_by_id = {h['id']: h for h in health}
    for sid, source in report['sources'].items():
        h = health_by_id.get(sid, {})
        if source['status'] == 'skipped':
            continue
        trace = h.get('transports', [])
        for step in trace:
            if step['error_type'] or (step['http_status'] or 0) >= 400:
                journal.emit('transport_failed', 'warning', source=sid, **step)
        if source['is_success'] and any(s['error_type'] for s in trace):
            journal.emit('source_recovered_after_retry', source=sid, routes=[s['route'] for s in trace])
        journal.emit('source_result', 'info' if source['is_success'] else 'error', source=sid,
                     status=source['status'], http_status=source['http_status'],
                     error_type=source['error'], latency_ms=source['latency_ms'],
                     attempts=h.get('attempts'), body_sha256=h.get('body_sha256'),
                     quote_count=h.get('quote_count', 0), routes=[s['route'] for s in trace])
        if (source['latency_ms'] or 0) >= 10000:
            journal.emit('slow_source', 'warning', source=sid, latency_ms=source['latency_ms'])
        old_source = previous.get('sources', {}).get(sid, {}).get('assets', {})
        for section in SECTIONS:
            for asset, item in source.get(section, {}).items():
                if asset not in configs[sid]['assets']:
                    continue
                ref = report[section][asset]
                journal.emit('quote_observation', source=sid, asset=asset, price=item['price'],
                             currency=item['currency'], unit=item['unit'], purity=item['purity'],
                             quote_type=item['quote_type'], reason=item['reason'],
                             published_at=item['published_at'], snapshot_at=item['snapshot_at'],
                             observed_at=item['observed_at'], is_fresh=item['is_fresh'],
                             snapshot_is_recent=item['snapshot_is_recent'],
                             used_in_reference=item['used_in_reference'], reference_price=ref['price'])
                reason = item['reason']
                if source['is_success'] and not item['is_success']:
                    journal.emit('asset_missing', 'warning', source=sid, asset=asset, reason=reason)
                elif reason and reason not in ('different_market_or_quote_type', 'snapshot_time_only'):
                    journal.emit('quote_rejected', 'warning', source=sid, asset=asset, reason=reason,
                                 price=item['price'], published_at=item['published_at'])
                if item['snapshot_at'] and not item['snapshot_is_recent']:
                    journal.emit('snapshot_not_recent', 'warning', source=sid, asset=asset,
                                 snapshot_at=item['snapshot_at'], age_seconds=item['snapshot_age_seconds'])
                if not item['is_success']:
                    continue
                old = old_source.get(asset, {})
                stamp = item['published_at'] or item['snapshot_at']
                old_stamp = old.get('last_published_at') or old.get('last_snapshot_at')
                if old.get('price') and stamp and old_stamp and old.get('time_basis') == item['time_basis'] and iso(stamp) > iso(old_stamp):
                    jump = abs(Decimal(str(item['price'])) / Decimal(str(old['price'])) - 1)
                    if jump > Decimal('0.05'):
                        journal.emit('price_jump_candidate', 'warning', source=sid, asset=asset,
                                     previous_price=old['price'], price=item['price'], change_percent=measure(jump*100),
                                     previous_at=old_stamp, published_or_snapshot_at=stamp, requires_review=True)
                if item.get('monitoring', {}).get('suspicious_unchanged'):
                    journal.emit('frozen_price_candidate', 'warning', source=sid, asset=asset,
                                 price=item['price'], unchanged_seconds=item['monitoring']['unchanged_seconds'],
                                 requires_review=True)
                # Leave this publisher out; two other reference publishers are
                # required before calling its price an outlier candidate.
                peers = [report['sources'][peer][section][asset]['price']
                         for peer in ref['contributors'] if report['sources'][peer]['group'] != source['group']]
                comparable = item['quote_type'] == ref['quote_type'] and configs[sid].get('market','IR-national') == 'IR-national'
                if comparable and (item['is_fresh'] or item['snapshot_is_recent']) and len(peers) >= 2:
                    from statistics import median
                    values=[Decimal(str(p)) for p in peers]
                    center = median(values)
                    if (max(values)-min(values))/center > Decimal(str(config['policy']['max_spread_ratio'])):
                        continue
                    deviation = abs(Decimal(str(item['price'])) / center - 1)
                    if deviation > Decimal(str(config['policy']['max_spread_ratio'])):
                        journal.emit('price_outlier_candidate', 'warning', source=sid, asset=asset,
                                     price=item['price'], peer_median=measure(center), peer_prices=peers,
                                     deviation_percent=measure(deviation*100), time_basis=item['time_basis'],
                                     source_is_fresh=item['is_fresh'], requires_review=True)
    for section in SECTIONS:
        for asset, ref in report.get(section, {}).items():
            potential = {s['group'] for s in config['sources'] if s.get('enabled') and s.get('reference_eligible', True) and asset in s['assets']}
            severity = 'info' if ref['status'] == 'ok' or not potential else 'error' if ref['status'] == 'unavailable' else 'warning'
            journal.emit('reference_result', severity, asset=asset, status=ref['status'], price=ref['price'],
                         source_count=ref['source_count'], contributors=ref['contributors'],
                         spread_percent=ref['spread_percent'], reference_range=ref['reference_range'],
                         oldest_contributor_at=ref['oldest_contributor_at'],
                         max_contributor_age_seconds=ref['max_contributor_age_seconds'],
                         warnings=ref['quality_warnings'], eligible_groups=len(potential))
    journal.emit('collection_finished', report_at=report['datetime'],
                 source_count=report['source_count'], successful_sources=report['successful_sources'])
