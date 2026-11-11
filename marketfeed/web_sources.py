"""Publisher-specific snapshots. Never promote page/render clocks to quote times."""
from datetime import datetime
from decimal import Decimal
import json
import re

from bs4 import BeautifulSoup

from .core import DIGITS, number, iso
from .parsers import quote
from .times import TEHRAN, jalali_to_date


def local_jalali(value):
    match = re.fullmatch(r'(14\d{2})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?', value.translate(DIGITS).strip())
    if not match:
        return None
    try:
        y, m, d = jalali_to_date(*map(int, match.group(1, 2, 3)))
        return datetime(y, m, d, int(match[4]), int(match[5]), int(match[6] or 0), tzinfo=TEHRAN).isoformat()
    except ValueError:
        return None


def add(out, source, asset, value, fetched, timestamp=None, **kwargs):
    if asset not in source['assets']:
        return
    try:
        out.append(quote(source, asset, value, timestamp, fetched, **kwargs))
    except (ValueError, TypeError):
        pass


def tgju_snapshot(body, source, fetched):
    rows = json.loads(body)['current']
    if not isinstance(rows, dict):
        raise ValueError('TGJU current mapping required')
    mapping = {'geram18': 'gold_18k', 'silver_999': 'silver_999', 'ons': 'gold_ounce',
               'sekee': 'coin_emami', 'sekeb': 'coin_bahar', 'nim': 'coin_half',
               'rob': 'coin_quarter', 'crypto-tether-irr': 'usdt'}
    out = []
    for slug, asset in mapping.items():
        row = rows.get(slug)
        if not isinstance(row, dict):
            continue
        timestamp = None
        # Date-only displays use a midnight sentinel, not an actual trade time.
        clock = str(row.get('t_en', '')).translate(DIGITS)
        stamp = str(row.get('ts', ''))
        if re.fullmatch(r'\d{2}:\d{2}:\d{2}', clock) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', stamp):
            try:
                dt = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S')
                if dt.strftime('%H:%M:%S') == clock:
                    timestamp = dt.replace(tzinfo=TEHRAN).isoformat()
            except ValueError:
                pass
        try:
            value = number(row['p']) / (1 if asset == 'gold_ounce' else 10)
            add(out, source, asset, value, fetched, timestamp)
        except (ValueError, KeyError):
            continue
    return out


def tala_time(soup):
    node = soup.select_one('#priceLastTime')
    if not node:
        return None
    match = re.fullmatch(r'(\d{1,2}:\d{2})\s+(14\d{2}/\d{1,2}/\d{1,2})', node.get_text(' ', strip=True).translate(DIGITS))
    return local_jalali(match[2] + ' ' + match[1]) if match else None


def tala_parsian(body, source, fetched):
    soup = BeautifulSoup(body, 'html.parser')
    text = soup.get_text(' ', strip=True).translate(DIGITS)
    if not re.search(r'پارسیان با عیار\s*750', text) or 'تمامی نرخ ها به تومان' not in text or 'بدون لحاظ شدن مالیات' not in text:
        return []
    out = []
    for box in soup.select('.rateBox'):
        label, price = box.select_one('h2'), box.select_one('h3')
        if not label or not price:
            continue
        match = re.fullmatch(r'پارسیان\s*([0-9.]+)\s*(سوت|گرم)', label.get_text(' ', strip=True).translate(DIGITS))
        if not match:
            continue
        try:
            mg = number(match[1]) * (1000 if match[2] == 'گرم' else 1)
            if mg != mg.to_integral_value():
                continue
            add(out, source, f'parsian_{int(mg)}mg', price.get_text(strip=True), fetched,
                tala_time(soup), kind='indicative_ex_vat')
        except ValueError:
            continue
    return out


TALA_PAGES = {'18k': ('gold_18k', 'طلا'), 'sekke-jad': ('coin_emami', 'سکه'),
              'sekke-nim': ('coin_half', 'سکه'), 'sekke-rob': ('coin_quarter', 'سکه'),
              'ounce': ('gold_ounce', 'اونس')}


def tala_price(body, source, fetched):
    slug = source['url'].rstrip('/').split('/')[-1]
    if slug not in TALA_PAGES:
        return []
    asset, title_word = TALA_PAGES[slug]
    soup = BeautifulSoup(body, 'html.parser')
    if not soup.title or title_word not in soup.title.get_text():
        return []
    out = []
    # Currency mapping is documented per publisher/page; never inferred by magnitude.
    if source.get('currency') != ('USD' if asset == 'gold_ounce' else 'IRT'):
        return []
    for box in soup.select('.rateBox'):
        label, price = box.select_one('h2'), box.select_one('h3')
        if label and price and label.get_text(strip=True) == 'آخرین قیمت':
            add(out, source, asset, price.get_text(strip=True), fetched, tala_time(soup))
            break
    return out


TABAN_WEIGHTS = list(range(50, 501, 50)) + list(range(600, 1600, 100)) + list(range(1600, 2100, 100)) + [2500, 3000]
TABAN_FIELDS = dict(zip([f'x{i}' for i in range(30, 50)] + [f'x{i}' for i in range(71, 78)], TABAN_WEIGHTS))


def tabangohar_parsian(body, source, fetched):
    from .adapters import pubdate
    data = json.loads(body)
    timestamp = pubdate(str(data.get('date', '')) + ' ' + str(data.get('time', '')), fetched)
    out = []
    for field, mg in TABAN_FIELDS.items():
        if field in data:
            add(out, source, f'parsian_{mg}mg', data[field], fetched, timestamp, kind='metal_value')
    return out


def talaclinic(body, source, fetched):
    from .adapters import pubdate
    soup = BeautifulSoup(body, 'html.parser')
    latest = {}
    for node in soup.select('.tgme_widget_message_text'):
        parent = node.find_parent(class_='tgme_widget_message')
        if parent and parent.select_one('.tgme_widget_message_forwarded_from'):
            continue
        text = node.get_text('\n', strip=True).translate(DIGITS)
        clock = re.search(r'آخرین بروزرسانی:\s*(\d{1,2}:\d{2})', text)
        date = re.search(r'(\d{1,2}\s+(?:فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند)\s+14\d{2})', text)
        if not clock or not date or 'تابان گوهر' not in text:
            continue
        timestamp = pubdate(date[1] + ' ' + clock[1], fetched)
        if not timestamp:
            continue
        # Coin/mithqal rows abbreviate thousands while saying toman. Do not guess
        # a multiplier. Silver is per ounce, not a gram of verified 999 silver.
        for asset, pattern in [('gold_18k', r'گرم\s+18\s+تهران:\s*([\d,]+)\s*تومان'),
                               ('gold_ounce', r'انس\s+طلا:\s*([\d,.]+)\s*دلار')]:
            match = re.search(pattern, text)
            found = []
            if match:
                add(found, source, asset, match[1], fetched, timestamp)
            if found and (asset not in latest or iso(timestamp) > iso(latest[asset].published_at)):
                latest[asset] = found[0]
    return list(latest.values())


def alanchand(body, source, fetched):
    soup = BeautifulSoup(body, 'html.parser')
    out = []
    # The page-wide update clock does not establish individual quote publication.
    for key, asset in {'18ayar':'gold_18k', 'sekkeh':'coin_emami', 'bahar':'coin_bahar',
                       'nim':'coin_half', 'rob':'coin_quarter', 'usd_xau':'gold_ounce'}.items():
        node = soup.select_one(f'[data-live="g.{key}"][data-f="s"]')
        if node:
            add(out, source, asset, node.get_text(strip=True), fetched)
    return out


def wallex_gold(body, source, fetched):
    soup = BeautifulSoup(body, 'html.parser')
    node = soup.select_one('script#__NEXT_DATA__')
    if not node:
        return []
    data = json.loads(node.get_text())
    out = []
    for query in data['props']['pageProps']['dehydratedState']['queries']:
        if query.get('queryKey') == ['getGoldPrice', []]:
            result = query['state']['data']
            if result.get('success') is True:
                # Query dataUpdatedAt/currentDateTime are fetch/render clocks.
                # last_usdt_price is denominated in USDT, not USD.
                add(out, source, 'gold_18k', result['result']['carats_18'], fetched, kind='synthetic')
    return out


def taline(body, source, fetched):
    soup = BeautifulSoup(body, 'html.parser')
    text = soup.get_text(' ', strip=True).translate(DIGITS)
    out = []
    if '/parsian/' in source['url']:
        match = re.search(r'قیمت سکه پارسیان 1گرمی:\s*([\d,]+)', text)
        if match:
            add(out, source, 'parsian_1000mg', match[1], fetched, kind='retail_unspecified_tax')
    else:
        match = re.search(r'قیمت\s*1\s*گرم طلای\s*18[\s‌]*عیار:\s*([\d,]+)', text)
        if match:
            add(out, source, 'gold_18k', match[1], fetched)
    # Article dateModified, cache generation and today's date are not price time.
    return out


def chartix(body, source, fetched):
    soup = BeautifulSoup(body, 'html.parser')
    node = soup.select_one('#__NUXT_DATA__')
    if not node:
        return []
    data = json.loads(node.get_text())
    out = []
    mapping = {'GOLD18TABLO':'gold_18k', 'SEKEJADID':'coin_emami', 'SEKEGADIM':'coin_bahar'}
    for row in data:
        if not isinstance(row, dict) or not {'ticker', 'last_price', 'exchange'} <= row.keys():
            continue
        if data[row['exchange']] != 'IRAN':
            continue
        asset = mapping.get(data[row['ticker']])
        if asset:
            add(out, source, asset, data[row['last_price']], fetched)
    return out


def tabangohar_gold(body, source, fetched):
    out = []
    add(out, source, 'gold_18k', json.loads(body).get('c'), fetched)
    return out


WEB_ADAPTERS = {'tgju_snapshot':tgju_snapshot, 'tala_parsian':tala_parsian,
                'tala_price':tala_price, 'tabangohar_parsian':tabangohar_parsian,
                'talaclinic':talaclinic, 'alanchand':alanchand,
                'wallex_gold':wallex_gold, 'taline':taline, 'chartix_snapshot':chartix,
                'tabangohar_gold':tabangohar_gold}
