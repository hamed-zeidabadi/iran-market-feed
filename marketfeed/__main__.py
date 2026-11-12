import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from websocket import create_connection, WebSocketException
from .adapters import extract
from .report import build_report


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    os.replace(tmp, path)


def read_source(source, timeout):
    if source['id'] in ('nobitex_usdt', 'ramzinex_usdt'):
        try:
            return read_https(source['url'], timeout)
        except HTTPError as exc:
            if exc.code != 403 or not os.environ.get('MARKET_PROXY_URL') or not os.environ.get('MARKET_PROXY_SECRET'):
                raise
            base = os.environ['MARKET_PROXY_URL']
            parsed = urlparse(base)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
                raise ValueError('invalid private proxy origin') from None
            request = Request(base.rstrip('/') + '/' + source['id'], headers={
                'X-Market-Secret': os.environ['MARKET_PROXY_SECRET'], 'Accept': 'application/json'})
            # No redirects: credentials must never leave the configured origin.
            from urllib.request import build_opener, HTTPRedirectHandler
            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            with build_opener(NoRedirect).open(request, timeout=timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('response too large')
                return raw, response.status
    if source['adapter'] == 'tgju_snapshot':
        # Same public snapshot requested by TGJU's homepage JavaScript.
        return read_https(source['url'] + '?rev=' + str(time.time_ns()), timeout)
    if source['adapter'] == 'tabangohar_parsian':
        return read_https(source['url'], timeout, data=b'item=all')
    if source.get('json_body') is not None:
        # Public read-only endpoint that the publisher's own site calls with POST.
        return read_https(source['url'], timeout, data=json.dumps(source['json_body']).encode('utf-8'),
                          content_type='application/json')
    if source['adapter']=='tabdeal_depth':
        try:
            return read_tabdeal_stream(source, timeout)
        except (WebSocketException, OSError, TimeoutError):
            from .new_sources import TABDEAL_TRADES_URL
            return read_https(TABDEAL_TRADES_URL, timeout)
    return read_https(source['url'], timeout)


def read_tabdeal_stream(source, timeout):
    if source['adapter'] == 'tabdeal_depth':
        from .new_sources import TABDEAL_STREAM
        # One public spot snapshot per scheduled collection; no persistent stream.
        deadline = time.monotonic() + timeout
        connection = create_connection(source['url'], timeout=timeout)
        try:
            connection.send(json.dumps({'method': 'SUBSCRIBE', 'params': [TABDEAL_STREAM], 'id': 1}))
            for _ in range(16):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('public depth snapshot timed out')
                connection.settimeout(remaining)
                raw = connection.recv()
                if not raw:
                    raise ValueError('empty websocket response')
                raw = raw.encode('utf-8') if isinstance(raw, str) else raw
                if len(raw) > 2_000_000:
                    raise ValueError('response too large')
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError('invalid stream envelope')
                if 'stream' in payload:
                    return raw, None
                if payload != {'result': None, 'id': 1}:
                    raise ValueError('unexpected subscription response')
            raise ValueError('no depth snapshot received')
        finally:
            connection.close()


def read_https(url, timeout, data=None, content_type=None):
    headers = {
        'User-Agent': 'IranMarketFeed/0.1 (open-source research collector)',
        'Accept': 'text/html,application/json',
    }
    if content_type:
        headers['Content-Type'] = content_type
    req = Request(url, headers=headers, data=data)
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('response too large')
        return raw, response.status


def collect(source, timeout, attempts, diagnostics_dir=None):
    started = time.monotonic()
    fetched = datetime.now(timezone.utc).isoformat()
    for attempt in range(attempts):
        try:
            raw, http_status = read_source(source, timeout)
            body = raw.decode('utf-8-sig')
            if diagnostics_dir:
                directory = Path(diagnostics_dir)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / (source['id'] + '.txt')).write_text(body, encoding='utf-8')
            fetched = datetime.now(timezone.utc).isoformat()
            quotes = extract(body, source, fetched)
            return quotes, {'id': source['id'], 'url': source['url'], 'group': source['group'],
                'status': 'parsed' if quotes else 'no_quotes', 'quote_count': len(quotes),
                'fetched_at': fetched, 'latency_ms': round((time.monotonic()-started)*1000),
                'http_status': http_status,
                'body_sha256': hashlib.sha256(raw).hexdigest(),
                'note': 'parsed does not imply fresh or independent'}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, WebSocketException) as exc:
            retryable = (isinstance(exc, HTTPError) and exc.code in (429, 500, 502, 503, 504)) or isinstance(exc, (URLError, TimeoutError, OSError)) and not isinstance(exc, HTTPError)
            if retryable and attempt+1 < attempts:
                time.sleep(min(2**attempt+random.random(), 5)); continue
            reason = getattr(exc, 'reason', None) or str(exc)
            return [], {'id': source['id'], 'status': 'error', 'error_type': type(exc).__name__,
                        'error_reason': str(reason)[:200],
                        'http_status': exc.code if isinstance(exc, HTTPError) else None,
                        'fetched_at': fetched}
    return [], {'id': source['id'], 'status': 'error'}


def load_config(path):
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    from .core import PARSIAN_ASSETS, GLOBAL_ASSETS
    additional = config.get('parsian_assets', [])
    if len(additional) != len(set(additional)) or not set(additional) <= set(PARSIAN_ASSETS):
        raise ValueError('invalid Parsian asset weights')
    global_assets = config.get('global_assets', [])
    if len(global_assets) != len(set(global_assets)) or not set(global_assets) <= set(GLOBAL_ASSETS):
        raise ValueError('invalid global assets')
    assets = config['primary_assets'] + additional + global_assets
    ids=set(); urls=set()
    for s in config['sources']:
        if not re.fullmatch(r'[a-z0-9_]+', s['id']):raise ValueError('invalid source ID')
        if s['id'] in ids:raise ValueError('duplicate source ID')
        if s['url'] in urls:raise ValueError('duplicate source URL')
        ids.add(s['id'])
        urls.add(s['url'])
        u=urlparse(s.get('url',''))
        if not u.hostname or u.username or u.password:raise ValueError('public source URL required')
        if s['adapter']=='tabdeal_depth':
            if s['url']!='wss://api1.tabdeal.org/stream/':raise ValueError('documented Tabdeal public spot stream required')
        elif u.scheme!='https':raise ValueError('HTTPS source required')
        if not s.get('group'):raise ValueError('publisher group required')
        if 'json_body' in s and not isinstance(s['json_body'],dict):raise ValueError('json_body must be an object')
        if not 0<s.get('timeout',15)<=30 or not 1<=s.get('attempts',2)<=4:raise ValueError('invalid per-source resource limits')
        if not isinstance(s.get('enabled'),bool):raise ValueError('enabled must be boolean')
        if not s.get('assets') or not set(s['assets']) <= set(assets):raise ValueError('explicit supported assets required')
    if config['policy']['max_age_seconds']<=0:raise ValueError('invalid freshness limit')
    if config['policy']['min_groups']<1 or config['policy']['future_tolerance_seconds']<0 or config['policy']['max_spread_ratio']<0:raise ValueError('invalid aggregation policy')
    from .core import PRIMARY_ASSETS
    if len(config['primary_assets'])!=8 or set(config['primary_assets'])!=set(PRIMARY_ASSETS):raise ValueError('the eight required primary assets must be configured')
    for asset in assets:
        if config['tolerances'][asset]<0:raise ValueError('invalid tolerance')
    if any(not 0<=r<=0.05 for r in config.get('tolerance_ratios',{}).values()):raise ValueError('invalid tolerance ratio')
    for owner in [config, *config['sources']]:
        for asset, policy in owner.get('asset_policies', {}).items():
            if asset not in assets or set(policy) - {'max_age_seconds'}:
                raise ValueError('freshness overrides only support configured assets and max_age_seconds')
            age = policy.get('max_age_seconds')
            if isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 < age <= 86400:
                raise ValueError('invalid measured freshness override')
    for asset, session in config.get('market_sessions', {}).items():
        from datetime import time as clock_time, date
        if asset not in assets or asset == 'usdt' or not session.get('weekdays') or not all(type(day) is int and 0 <= day <= 6 for day in session['weekdays']):
            raise ValueError('invalid market calendar')
        if clock_time.fromisoformat(session['open']) >= clock_time.fromisoformat(session['close']):
            raise ValueError('market session must open before closing')
        for day in session.get('closed_dates', []):
            date.fromisoformat(day)
    return config


def main():
    cli=argparse.ArgumentParser(description='Iran Market Feed: one JSON endpoint, no history')
    cli.add_argument('--config',default='sources.json')
    cli.add_argument('--output',default='public/api/prices.json')
    cli.add_argument('--list',action='store_true')
    cli.add_argument('--only',help='comma-separated source IDs')
    cli.add_argument('--workers',type=int,default=6)
    cli.add_argument('--timeout',type=float,default=15)
    cli.add_argument('--diagnostics-dir', help='Private, temporary response inspection directory; never under public/')
    cli.add_argument('--state', default='.validation/monitoring.json', help='Private bounded operational state')
    args=cli.parse_args();config=load_config(args.config)
    if args.diagnostics_dir and Path(args.diagnostics_dir).resolve().is_relative_to(Path('public').resolve()):
        cli.error('diagnostics must be outside public/')
    if Path(args.state).resolve().is_relative_to(Path('public').resolve()):
        cli.error('monitoring state must be outside public/')
    if args.list:
        for s in config['sources']:print(s['id'],s['url'])
        return
    selected=[s for s in config['sources'] if s.get('enabled')]
    if args.only:
        wanted=set(args.only.split(','))
        if not wanted<={s['id'] for s in selected}:cli.error('unknown or disabled source')
        selected=[s for s in selected if s['id'] in wanted]
    if not 1<=args.workers<=16 or not 0<args.timeout<=60:cli.error('invalid resource limits')
    quotes=[];health=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks={pool.submit(collect,s,s.get('timeout',args.timeout),s.get('attempts',2),args.diagnostics_dir):s for s in selected}
        for task in as_completed(tasks):
            try:found,state=task.result()
            except Exception as exc:
                found,state=[],{'id':tasks[task]['id'],'status':'parser_error','error_type':type(exc).__name__}
            quotes.extend(found);health.append(state)
            detail=' '.join(str(state[k]) for k in ('error_type','error_reason') if state.get(k))
            print(state['id'],state['status'],len(found),detail,flush=True)
    # Unrequested sources have explicit skipped statuses, never fabricated successes.
    for s in config['sources']:
        if s['id'] not in {h['id'] for h in health}:health.append({'id':s['id'],'status':'skipped'})
    output=build_report(config,quotes,health,datetime.now(timezone.utc))
    from .monitoring import enrich
    try:
        previous = json.loads(Path(args.state).read_text(encoding='utf-8'))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    state = enrich(output, previous, config, datetime.now(timezone.utc))
    atomic_json(Path(args.state), state)
    atomic_json(Path(args.output),output)
    print('Saved',args.output,'successful routes:',output['successful_sources'],'/',output['source_count'])

if __name__=='__main__':main()
