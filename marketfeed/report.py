"""The only aggregation implementation, shared by CLI and tests."""
from decimal import Decimal
from datetime import datetime, timezone
from statistics import median
from .core import iso, ASSETS
from .times import output_clock


def numeric(value):
    if value is None:
        return None
    value = Decimal(str(value))
    return int(value) if value == value.to_integral_value() else float(value.quantize(Decimal('0.000001')))


def structural_reason(q):
    spec = ASSETS[q.asset]
    if not q.price.is_finite() or q.price <= 0:
        return 'invalid_price'
    if (q.currency, q.unit, q.purity) != (spec['currency'], spec['unit'], spec.get('purity')):
        return 'unit_or_purity_mismatch'
    return None


def time_reason(q, now, policy):
    if q.time_basis == 'provider_snapshot':
        return 'snapshot_time_only'
    if q.time_basis != 'source' or not q.published_at:
        return 'unknown_time'
    try:
        age = (now - iso(q.published_at)).total_seconds()
    except (ValueError, TypeError, OverflowError, AttributeError):
        return 'invalid_timestamp'
    if age < -policy['future_tolerance_seconds']:
        return 'future_timestamp'
    if age > policy['max_age_seconds']:
        return 'stale'
    return None


def tolerance_for(config, asset, center):
    """Closeness limit: the larger of the fixed floor and a ratio of the reference price."""
    floor = Decimal(str(config['tolerances'][asset]))
    ratios = config.get('tolerance_ratios', {})
    ratio = Decimal(str(ratios.get(asset, ratios.get('default', 0))))
    return max(floor, ratio * Decimal(str(center))) if center is not None else floor


def canonical_time(value):
    try:
        return iso(value).isoformat().replace('+00:00', 'Z') if value else None
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def build_report(config, quotes, health, now):
    clock = output_clock(now)
    assets = config['primary_assets'] + config.get('parsian_assets', []) + config.get('global_assets', [])
    sources_config = {s['id']: s for s in config['sources'] if s.get('enabled', True)}
    found = {}
    for q in quotes:
        if q.source not in sources_config or q.asset not in assets:
            continue
        key = (q.source, q.asset)
        rank = lambda x: (iso(x.published_at) if canonical_time(x.published_at) else datetime.min.replace(tzinfo=timezone.utc),
                          iso(x.fetched_at) if canonical_time(x.fetched_at) else datetime.min.replace(tzinfo=timezone.utc), str(x.price))
        if key not in found or rank(q) > rank(found[key]):
            found[key] = q
    reasons = {key: structural_reason(q) or time_reason(q, now, freshness_policy(config, q)) for key, q in found.items()}
    prices, selected_by_asset, centers = {}, {}, {}
    for asset in assets:
        by_group = {}
        eligible = [q for key, q in found.items() if q.asset == asset and reasons[key] is None
                    and sources_config[q.source].get('reference_eligible', True)
                    and q.market == 'IR-national' and q.kind == ASSETS[asset].get('quote_type', 'indicative')]
        for q in sorted(eligible, key=lambda q: (iso(q.published_at), q.source)):
            by_group[sources_config[q.source]['group']] = q
        selected = list(by_group.values())
        selected_by_asset[asset] = {q.source for q in selected}
        center = median(q.price for q in selected) if selected else None
        centers[asset] = center
        spread = (max(q.price for q in selected) - min(q.price for q in selected)) / center if selected else None
        status = 'unavailable' if not selected else 'ok' if len(selected) >= config['policy']['min_groups'] else 'provisional'
        if spread is not None and spread > Decimal(str(config['policy']['max_spread_ratio'])):
            status = 'disputed'
        spec = ASSETS[asset]
        prices[asset] = {
            'label': spec['label'], 'price': numeric(center), 'currency': spec['currency'], 'unit': spec['unit'],
            'purity': spec.get('purity'), 'quote_type': spec.get('quote_type', 'indicative'), 'status': status, 'direction': None, 'source_count': len(selected),
            'published_at': canonical_time(max((q.published_at for q in selected), key=iso)) if selected else None,
            'oldest_contributor_at': canonical_time(min((q.published_at for q in selected), key=iso)) if selected else None,
            'max_contributor_age_seconds': max(0, max((now - iso(q.published_at)).total_seconds() for q in selected)) if selected else None,
            'reference_range': {'min': numeric(min(q.price for q in selected)), 'max': numeric(max(q.price for q in selected))} if selected else None,
            'quality_warnings': (['source_disagreement'] if status == 'disputed' else []) + (['insufficient_groups'] if len(selected) < config['policy']['min_groups'] else []),
            'observed_at': max((canonical_time(q.fetched_at) for q in selected), default=None),
            'uses_observed_time': False,
            'spread_percent': numeric(spread * 100) if spread is not None else None,
            'tolerance': numeric(tolerance_for(config, asset, center)), 'contributors': sorted(selected_by_asset[asset]),
        }
        if 'weight_grams' in spec:
            prices[asset]['weight_grams'] = spec['weight_grams']
    health_by_id = {h['id']: h for h in health}
    sources = {}
    for source_id, source in sources_config.items():
        detail = {}
        for asset in assets:
            q = found.get((source_id, asset))
            valid = q is not None and structural_reason(q) is None
            reason = reasons.get((source_id, asset), 'missing')
            published = canonical_time(q.published_at) if q and q.time_basis == 'source' else None
            snapshot = canonical_time(q.snapshot_at) if q else None
            snapshot_age = (now - iso(snapshot)).total_seconds() if snapshot else None
            policy = freshness_policy(config, q) if q else config['policy']
            diff = q.price - centers[asset] if valid and centers[asset] is not None else None
            compatible = q is not None and q.market == 'IR-national' and q.kind == ASSETS[asset].get('quote_type', 'indicative')
            detail[asset] = {
                'is_success': valid, 'price': numeric(q.price) if valid else None,
                'currency': ASSETS[asset]['currency'], 'unit': ASSETS[asset]['unit'], 'purity': ASSETS[asset].get('purity'),
                'quote_type': q.kind if q else None,
                'difference': numeric(diff),
                'difference_rial': numeric(diff * 10) if diff is not None and ASSETS[asset]['currency'] == 'IRT' else None,
                'is_close': abs(diff) <= tolerance_for(config, asset, centers[asset]) if diff is not None else None,
                'is_fresh': reason is None, 'is_usable': valid and reason is None and compatible,
                'used_in_reference': source_id in selected_by_asset[asset],
                'reason': reason if reason else None if compatible else 'different_market_or_quote_type',
                'published_at': published, 'observed_at': canonical_time(q.fetched_at) if q else None,
                'snapshot_at': snapshot,
                'snapshot_age_seconds': max(0, snapshot_age) if snapshot_age is not None else None,
                'snapshot_is_recent': bool(valid and snapshot_age is not None and
                    -policy['future_tolerance_seconds'] <= snapshot_age <= policy['max_age_seconds']),
                'time_basis': 'source' if published else 'provider_snapshot' if q and q.time_basis == 'provider_snapshot' else 'unknown' if q else None,
            }
            if 'weight_grams' in ASSETS[asset]:
                detail[asset]['weight_grams'] = ASSETS[asset]['weight_grams']
        state = health_by_id.get(source_id, {'status': 'skipped'})
        sources[source_id] = {
            'is_success': any(d['is_success'] for d in detail.values()), 'status': state['status'],
            'group': source['group'], 'url': source['url'], 'reference_eligible': source.get('reference_eligible', True),
            'latency_ms': state.get('latency_ms'), 'http_status': state.get('http_status'),
            'error': state.get('error_type'),
            'prices': {a: detail[a] for a in config['primary_assets']},
            'parsian': {a: detail[a] for a in config.get('parsian_assets', [])},
            'global_prices': {a: detail[a] for a in config.get('global_assets', [])},
        }
    return {
        'schema_version': 3, **clock, 'refresh_interval_seconds': 300,
        'method': 'median_one_vote_per_publisher',
        'prices': {a: prices[a] for a in config['primary_assets']},
        'parsian': {a: prices[a] for a in config.get('parsian_assets', [])},
        'global_prices': {a: prices[a] for a in config.get('global_assets', [])},
        'source_summary': {key: s['is_success'] for key, s in sources.items()},
        'source_count': len(sources), 'successful_sources': sum(s['is_success'] for s in sources.values()),
        'sources': sources,
    }


def freshness_policy(config, q):
    """Explicit measured overrides only; the existing default remains unchanged."""
    policy = dict(config['policy'])
    policy.update(config.get('asset_policies', {}).get(q.asset, {}))
    source = next(s for s in config['sources'] if s['id'] == q.source)
    policy.update(source.get('asset_policies', {}).get(q.asset, {}))
    return policy
