"""Read-only Actions/local probe. Does not enable sources or publish a feed."""
from datetime import datetime, timezone
import json
from marketfeed.__main__ import load_config, collect
from marketfeed.report import structural_reason, time_reason


def main():
    config = load_config('sources.json')
    source = next(s for s in config['sources'] if s['id'] == 'nobitex_usdt')
    quotes, health = collect(source, 15, 1)
    now = datetime.now(timezone.utc)
    results = [dict(price=str(q.price), published_at=q.published_at, quote_type=q.kind,
                    reason=structural_reason(q) or time_reason(q, now, config['policy'])) for q in quotes]
    passed = bool(results) and all(q['reason'] is None and q['quote_type'] == 'midpoint' for q in results)
    print(json.dumps({'source':source['id'], 'checked_at':now.isoformat(),
                      'http_status':health.get('http_status'), 'status':health['status'],
                      'error':health.get('error_type'), 'passed':passed, 'quotes':results},allow_nan=False))
    if not passed:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
