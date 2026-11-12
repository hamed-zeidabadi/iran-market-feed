"""Read-only live evidence capture, outside public. No source promotion."""
import json
import argparse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from datetime import datetime, timezone

TARGETS = {
    'feed': 'https://hamed-zeidabadi.github.io/iran-market-feed/api/prices.json',
    'tgju': 'https://call2.tgju.org/ajax.json',
    'ecogold': 'https://ble.ir/s/ecogold_ir',
    'tala_half': 'https://www.tala.ir/price/sekke-nim',
    'tala_ajax': 'https://www.tala.ir/ajax/price/talair',
    'nobitex': 'https://apiv2.nobitex.ir/v3/orderbook/USDTIRT',
    'ramzinex': 'https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/orderbooks/11/buys_sells',
    'talanex': 'https://talanex.com/price/silver',
    'noqrei': 'https://noqrei.com/gheymat-noghre.php',
    'bitpin': 'https://api.bitpin.ir/api/v1/mth/matches/USDT_IRT/',
    'noghrebakhshi': 'https://noghrebakhshi.com/price/',
    'noghresea': 'https://noghresea.ir/',
    'noghresea_www': 'https://www.noghresea.ir/',
    'noghrebakhshi_home': 'https://noghrebakhshi.com/',
    'noghrebakhshi_js': 'https://noghrebakhshi.com/wp-content/plugins/wp-gold/assets/js/frontend.js',
    'wallex_page': 'https://wallex.ir/price/usdt',
    'alanchand_usdt': 'https://alanchand.com/crypto-price/usdt',
    'wallex_depth': 'https://api.wallex.ir/v1/depth?symbol=USDTTMN',
    'wallex_trades': 'https://api.wallex.ir/v1/trades?symbol=USDTTMN',
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', help='comma-separated target names')
    args = parser.parse_args()
    directory = Path('.validation/live') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory.mkdir(parents=True)
    for name, url in TARGETS.items():
        if args.only and name not in args.only.split(','):
            continue
        try:
            with urlopen(Request(url, headers={'User-Agent': 'IranMarketFeed/0.1'}), timeout=20) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('response too large')
                (directory / (name + '.txt')).write_bytes(raw)
                print(name, response.status, len(raw), flush=True)
        except Exception as exc:
            print(name, type(exc).__name__, getattr(exc, 'code', ''), flush=True)
            if isinstance(exc, HTTPError):
                print('redirect:', exc.headers.get('Location'), flush=True)
    print(directory)
