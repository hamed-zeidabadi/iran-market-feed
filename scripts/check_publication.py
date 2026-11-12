"""Measure actual fetch-to-publication delay only after this snapshot is live."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from marketfeed.audit import Journal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    journal = Journal('.validation/publication.jsonl', phase='publication')
    journal.emit('publication_check_started')
    expected = json.loads(Path('public/api/prices.json').read_text(encoding='utf-8'))
    for attempt in range(6):
        try:
            request = Request(args.url + '?publication_check=' + str(time.time_ns()), headers={'Cache-Control':'no-cache'})
            with urlopen(request, timeout=15) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('response too large')
                live = json.loads(raw)
            if live['datetime'] != expected['datetime']:
                raise ValueError('expected snapshot not visible yet')
            now = datetime.now(timezone.utc)
            parse = lambda s: datetime.fromisoformat(s.replace('Z','+00:00'))
            ages = [(now-parse(item['observed_at'])).total_seconds()
                    for source in live['sources'].values()
                    for section in ('prices','parsian','global_prices') for item in source.get(section,{}).values()
                    if item.get('observed_at')]
            metrics={'checked_at':now.isoformat(), 'snapshot':live['datetime'],
                              'report_to_publication_seconds':(now-parse(live['datetime'])).total_seconds(),
                              'max_fetch_to_publication_seconds':max(ages,default=None)}
            journal.emit('publication_confirmed', **metrics)
            if metrics['report_to_publication_seconds'] > 300:
                journal.emit('publication_delayed','warning',**metrics)
            print(json.dumps(metrics))
            return
        except (OSError, ValueError, KeyError) as exc:
            journal.emit('publication_check_retry','warning',attempt=attempt+1,error_type=type(exc).__name__,http_status=getattr(exc,'code',None))
            if attempt == 5:
                journal.emit('publication_not_confirmed','error',snapshot=expected['datetime'])
                raise RuntimeError('publication measurement failed') from exc
            time.sleep(5)

if __name__ == '__main__':
    main()
