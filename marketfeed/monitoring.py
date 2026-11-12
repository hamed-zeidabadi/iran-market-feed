"""Bounded private operational state; never substitute old data for live votes."""
from datetime import time
from statistics import median

from .core import iso
from .times import TEHRAN


def market_status(asset, now, config):
    if asset == 'usdt':
        return {'status': 'open', 'basis': '24/7'}
    session = config.get('market_sessions', {}).get(asset)
    if not session:
        return {'status': 'unknown', 'basis': 'no_verified_calendar'}
    local = now.astimezone(TEHRAN)
    if local.date().isoformat() in session.get('closed_dates', []):
        return {'status': 'closed', 'basis': 'configured_calendar'}
    opened = (local.weekday() in session['weekdays'] and
              time.fromisoformat(session['open']) <= local.time().replace(tzinfo=None) < time.fromisoformat(session['close']))
    return {'status': 'open' if opened else 'closed', 'basis': 'configured_calendar'}


def enrich(report, previous, config, now):
    """Return next state. Counters are cumulative within this state's lifetime.

    Cadence is an observed interval, not an automatic freshness recommendation.
    A long unchanged price is only suspicious if another publisher moved while
    this publisher advanced its claimed quote time. No automatic quarantine.
    """
    state = {'version': 1, 'sources': {}, 'last_valid': {}, 'last_run_at': report['datetime']}
    previous = previous if previous.get('version') == 1 else {}
    for section in ('prices', 'parsian', 'global_prices'):
        for asset, ref in report.get(section, {}).items():
            ref['market'] = market_status(asset, now, config)
            old = previous.get('last_valid', {}).get(asset)
            eligible_ids = {s['id'] for s in config['sources'] if s.get('enabled', True) and s.get('reference_eligible', True)}
            if old and not set(old['contributors']) <= eligible_ids:
                old = None
            if ref['status'] == 'ok':
                old = {k: ref[k] for k in ('price', 'published_at', 'oldest_contributor_at', 'contributors')}
                old['accepted_at'] = report['datetime']
            if old:
                state['last_valid'][asset] = old
                ref['last_valid'] = {**old, 'age_seconds': max(0, (now - iso(old['oldest_contributor_at'])).total_seconds())}
            else:
                ref['last_valid'] = None
    for sid, source in report['sources'].items():
        old_source = previous.get('sources', {}).get(sid, {})
        entry = {'assets': {}}
        for section in ('prices', 'parsian', 'global_prices'):
            for asset, item in source.get(section, {}).items():
                if asset not in next(s['assets'] for s in config['sources'] if s['id'] == sid):
                    continue
                old = old_source.get('assets', {}).get(asset, {})
                if item['is_success'] and old and old.get('time_basis') != item['time_basis']:
                    old = {}
                count = old.get('samples', 0) + (source['status'] != 'skipped')
                success = old.get('successes', 0) + (item['is_success'] and source['status'] != 'skipped')
                fresh = old.get('fresh_samples', 0) + (item['is_fresh'] and source['status'] != 'skipped')
                intervals = list(old.get('publication_intervals_seconds', []))
                timestamp = item['published_at']
                last_timestamp = old.get('last_published_at')
                advanced = bool(timestamp and last_timestamp and iso(timestamp) > iso(last_timestamp))
                if advanced:
                    intervals = (intervals + [(iso(timestamp) - iso(last_timestamp)).total_seconds()])[-128:]
                same = item['is_success'] and old.get('price') == item['price']
                since = old.get('unchanged_since') if same else report['datetime'] if item['is_success'] else None
                unchanged = max(0, (now - iso(since)).total_seconds()) if since else None
                peers_moved = any(
                    other['group'] != source['group'] and
                    other.get(section, {}).get(asset, {}).get('is_fresh') and
                    previous.get('sources', {}).get(oid, {}).get('assets', {}).get(asset, {}).get('price') is not None and
                    previous['sources'][oid]['assets'][asset]['price'] != other[section][asset]['price']
                    for oid, other in report['sources'].items())
                suspicious = bool(advanced and same and peers_moved and unchanged is not None and
                                  unchanged >= config.get('monitoring', {}).get('unchanged_warning_seconds', 5400))
                entry['assets'][asset] = {
                    'time_basis': item['time_basis'] if item['is_success'] else old.get('time_basis'),
                    'samples': count, 'successes': success, 'fresh_samples': fresh,
                    'price': item['price'] if item['is_success'] else old.get('price'),
                    'last_published_at': timestamp if timestamp and (not last_timestamp or iso(timestamp) >= iso(last_timestamp)) else last_timestamp,
                    'publication_intervals_seconds': intervals,
                    'unchanged_since': since if item['is_success'] else old.get('unchanged_since'),
                }
                item['monitoring'] = {
                    'samples': count, 'success_ratio': success / count if count else None,
                    'fresh_ratio': fresh / count if count else None,
                    'publication_interval_samples': len(intervals),
                    'median_publication_interval_seconds': median(intervals) if intervals else None,
                    'unchanged_seconds': unchanged, 'suspicious_unchanged': suspicious,
                    'publication_age_seconds': max(0, (now - iso(timestamp)).total_seconds()) if timestamp else None,
                    'fetch_to_report_seconds': max(0, (now - iso(item['observed_at'])).total_seconds()) if item['observed_at'] else None,
                }
        state['sources'][sid] = entry
    return state
