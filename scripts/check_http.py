"""Serve the actual public directory and verify a real GET, then stop the server."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.request import urlopen
from validate_live import ROOT, validate


def main():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / 'public'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/api/prices.json'
        with urlopen(url, timeout=10) as response:
            raw = response.read()
            content_type = response.headers.get_content_type()
            assert response.status == 200
            assert content_type == 'application/json'
            report = json.loads(raw)
            validate(report, json.loads((ROOT / 'sources.json').read_text(encoding='utf-8')), require_all_fresh=False)
            assert report == json.loads((ROOT / 'public/api/prices.json').read_text(encoding='utf-8'))
            result = {'url': url, 'status': response.status, 'content_type': content_type,
                      'datetime': report['datetime'], 'source_count': report['source_count'], 'json_valid': True}
        print(json.dumps(result, indent=2))
        (ROOT / '.validation/http.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
