"""Test fixed proxy routes with existing parsers. Secrets stay in environment."""
import json
import os
from datetime import datetime, timezone
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlparse
from pathlib import Path
from marketfeed.adapters import extract
from marketfeed.report import structural_reason, time_reason


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


if __name__ == '__main__':
    origin = os.environ['MARKET_PROXY_URL']
    parsed = urlparse(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('HTTPS origin required')
    config = json.loads(Path('sources.json').read_text(encoding='utf-8'))
    for sid in ('nobitex_usdt','ramzinex_usdt'):
        source = next(s for s in config['sources'] if s['id'] == sid)
        try:
            req = Request(origin.rstrip('/')+'/'+sid,headers={'X-Market-Secret':os.environ['MARKET_PROXY_SECRET']})
            with build_opener(NoRedirect).open(req,timeout=20) as response:
                body = response.read(2_000_001)
                if len(body)>2_000_000: raise ValueError('response too large')
            now = datetime.now(timezone.utc)
            quotes = extract(body.decode('utf-8-sig'),source,now.isoformat())
            print(json.dumps({'id':sid,'quotes':[{'price':str(q.price),'published_at':q.published_at,
                  'reason':structural_reason(q) or time_reason(q,now,config['policy'])} for q in quotes]}))
        except Exception as exc:
            print(json.dumps({'id':sid,'error':type(exc).__name__,'status':getattr(exc,'code',None)}))
