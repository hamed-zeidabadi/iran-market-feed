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


def canonical_time(value):
    try:
        return iso(value).isoformat().replace('+00:00', 'Z') if value else None
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def build_report(config, quotes, health, now):
    clock = output_clock(now)
    sources_config = {s['id']: s for s in config['sources'] if s.get('enabled', True)}
    found = {}
    for q in quotes:
        if q.source not in sources_config or q.asset not in config['primary_assets']:
            continue
        key = (q.source, q.asset)
        rank = lambda x: (iso(x.published_at) if canonical_time(x.published_at) else datetime.min.replace(tzinfo=timezone.utc),
                          iso(x.fetched_at) if canonical_time(x.fetched_at) else datetime.min.replace(tzinfo=timezone.utc), str(x.price))
        if key not in found or rank(q) > rank(found[key]):
            found[key] = q
    reasons = {key: structural_reason(q) or time_reason(q, now, config['policy']) for key, q in found.items()}
    prices, selected_by_asset, centers = {}, {}, {}
    for asset in config['primary_assets']:
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
            'observed_at': max((canonical_time(q.fetched_at) for q in selected), default=None),
            'uses_observed_time': False,
            'spread_percent': numeric(spread * 100) if spread is not None else None,
            'tolerance': config['tolerances'][asset], 'contributors': sorted(selected_by_asset[asset]),
        }
    health_by_id = {h['id']: h for h in health}
    sources = {}
    for source_id, source in sources_config.items():
        detail = {}
        for asset in config['primary_assets']:
            q = found.get((source_id, asset))
            valid = q is not None and structural_reason(q) is None
            reason = reasons.get((source_id, asset), 'missing')
            published = canonical_time(q.published_at) if q and q.time_basis == 'source' else None
            diff = q.price - centers[asset] if valid and centers[asset] is not None else None
            compatible = q is not None and q.market == 'IR-national' and q.kind == ASSETS[asset].get('quote_type', 'indicative')
            detail[asset] = {
                'is_success': valid, 'price': numeric(q.price) if valid else None,
                'currency': ASSETS[asset]['currency'], 'unit': ASSETS[asset]['unit'], 'purity': ASSETS[asset].get('purity'),
                'quote_type': q.kind if q else None,
                'difference': numeric(diff),
                'difference_rial': numeric(diff * 10) if diff is not None and ASSETS[asset]['currency'] == 'IRT' else None,
                'is_close': abs(diff) <= Decimal(str(config['tolerances'][asset])) if diff is not None else None,
                'is_fresh': reason is None, 'is_usable': valid and reason is None and compatible,
                'used_in_reference': source_id in selected_by_asset[asset],
                'reason': reason if reason else None if compatible else 'different_market_or_quote_type',
                'published_at': published, 'observed_at': canonical_time(q.fetched_at) if q else None,
                'time_basis': 'source' if published else 'unknown' if q else None,
            }
        state = health_by_id.get(source_id, {'status': 'skipped'})
        sources[source_id] = {
            'is_success': any(d['is_success'] for d in detail.values()), 'status': state['status'],
            'group': source['group'], 'url': source['url'], 'reference_eligible': source.get('reference_eligible', True),
            'latency_ms': state.get('latency_ms'), 'http_status': state.get('http_status'),
            'error': state.get('error_type'), 'prices': detail,
        }
    return {
        'schema_version': 2, **clock, 'refresh_interval_seconds': 600,
        'method': 'median_one_vote_per_publisher', 'prices': prices,
        'source_summary': {key: s['is_success'] for key, s in sources.items()},
        'source_count': len(sources), 'successful_sources': sum(s['is_success'] for s in sources.values()),
        'sources': sources,
    }
