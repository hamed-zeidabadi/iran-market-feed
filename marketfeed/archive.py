"""Authenticated archive client; no raw error bodies or credentials in output."""
import gzip
import io
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def private_path(path):
    path = Path(path)
    if path.resolve().is_relative_to(Path('public').resolve()):
        raise ValueError('archive files must be outside public/')
    return path


def unpack(raw, limit=1_500_000):
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        data = stream.read(limit+1)
    if len(data)>limit:
        raise ValueError('archive batch too large')
    return json.loads(data)


class Archive:
    def __init__(self, origin=None, secret=None):
        self.origin = origin if origin is not None else os.environ.get('AUDIT_URL', '')
        self.secret = secret if secret is not None else os.environ.get('AUDIT_SECRET', '')
        p = urlparse(self.origin)
        if not self.secret or p.scheme!='https' or not p.hostname or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:
            raise ValueError('private audit archive is not configured')
        self.origin = self.origin.rstrip('/')
        self.opener = build_opener(NoRedirect)

    def call(self, path, data=None):
        for attempt in range(3):
            try:
                req = Request(self.origin+path, data=data, headers={
                    'X-Audit-Secret':self.secret,'User-Agent':'IranMarketFeed/0.1 incident archive',
                    'Content-Type':'application/gzip' if data else 'application/json'})
                with self.opener.open(req,timeout=25) as response:
                    body = response.read(20_000_001)
                    if len(body)>20_000_000:raise ValueError('archive response too large')
                    return body
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                if isinstance(exc,HTTPError) and exc.code not in (429,500,502,503,504):raise
                if attempt==2:raise
                time.sleep(attempt+1)

    def upload(self, path, state_path=None):
        path = private_path(path)
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not records or len({r['batch_id'] for r in records})!=1:
            raise ValueError('one nonempty run journal required')
        first = records[0]
        completed = any(r.get('event') == 'collection_finished' for r in records)
        if first['phase']=='collection' and not completed:
            records.append({**{k:first[k] for k in ('log_version','run_id','run_attempt','phase','batch_id','commit','run_url')},
                            'at':records[-1]['at'],'at_tehran':records[-1].get('at_tehran'),
                            'event':'collection_incomplete','severity':'error'})
        batch = {'id':first['batch_id'],'kind':first['phase'],'at':first['at'],'records':records}
        if state_path and first['phase']=='collection' and completed:
            state=json.loads(private_path(state_path).read_text(encoding='utf-8'))
            if state.get('version')!=1:raise ValueError('invalid operational state')
            batch['state']=state
        raw=json.dumps(batch,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        if len(raw)>1_500_000:raise ValueError('audit batch exceeds archive bound')
        response=json.loads(self.call('/ingest',gzip.compress(raw,mtime=0)))
        if response.get('stored')!=batch['id']:raise ValueError('archive acknowledgement mismatch')
        return {'stored':batch['id'],'records':len(records),'packed_bytes':len(gzip.compress(raw,mtime=0))}

    def restore(self, path):
        raw=self.call('/state')
        if not raw:return False
        state=unpack(raw)
        if not isinstance(state,dict) or state.get('version')!=1 or not isinstance(state.get('sources'),dict):
            raise ValueError('invalid archived state')
        path=private_path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.restore.tmp')
        temp.write_text(json.dumps(state,allow_nan=False),encoding='utf-8')
        os.replace(temp,path)
        return True
