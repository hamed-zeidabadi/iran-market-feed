"""Explicit opt-in adapters. Unmapped/ambiguous prices fail closed."""
from html.parser import HTMLParser
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from .core import Quote, ASSETS, DIGITS, number, iso

from .times import TEHRAN, jalali_to_date


class Blocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('div', 'p', 'tr', 'li', 'br', 'time', 'h1', 'h2', 'td'):
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip-1)
        if tag in ('div', 'p', 'tr', 'li', 'time', 'td'):
            self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html):
    p = Blocks(); p.feed(html)
    return '\n'.join(x.strip() for x in ''.join(p.parts).splitlines() if x.strip())


def quote(source, asset, price, timestamp, fetched, **overrides):
    spec = ASSETS[asset]
    fields = dict(source=source['id'], group=source['group'], asset=asset,
        price=number(price), published_at=timestamp, fetched_at=fetched,
        currency=spec['currency'], unit=spec['unit'], purity=spec.get('purity'),
        market=source.get('market', 'IR-national'), kind=source.get('kind', 'indicative'),
        evidence_url=source['url'], time_basis='source' if timestamp else 'unknown')
    fields.update(overrides)
    return Quote(**fields)


PATTERNS = {
 'gold_18k': r'(?:طلای?\s*(?:۱۸|18)\s*عیار|طلا\s*۱۸)(?![۰-۹٠-٩0-9])',
 'silver_999': r'نقره\s*(?:۹۹۹|999)',
 'coin_bahar': r'سکه\s*(?:تمام\s*)?(?:بهار آزادی|طرح قدیم)',
 'coin_emami': r'سکه\s*(?:امامی|طرح جدید)',
 'coin_half': r'نیم\s*سکه', 'coin_quarter': r'ربع\s*سکه',
 'gold_ounce': r'(?:اونس|انس)\s*(?:جهانی\s*)?طلا',
 'coin_gram': r'سکه\s*(?:یک\s*)?گرمی',
}


def parse_lines(text, source, fetched, timestamp=None):
    """Require currency in price lines. No automatic assumptions about anonymous silver."""
    results = []
    for asset, pattern in PATTERNS.items():
        if asset not in source.get('assets', PATTERNS):
            continue
        expr = pattern + r'\s*[:：]?\s*([۰-۹٠-٩0-9٬,٫.]+)\s*(تومان|ریال|\$|دلار)'
        matches = [m for m in re.finditer(expr, text)
                   if not re.search(r'حباب|خرید|فروش|تغییر|بیشترین|کمترین', text[text.rfind('\n', 0, m.start()) + 1:m.start()])]
        if not matches:
            continue
        match = matches[-1]
        try:
            value = number(match[1])
        except ValueError:
            continue
        unit = match[2]
        if asset == 'gold_ounce' and unit not in ('$', 'دلار'):
            continue
        if asset != 'gold_ounce' and unit not in ('تومان', 'ریال'):
            continue
        if unit == 'ریال':
            value /= Decimal(10)
        results.append(quote(source, asset, value, timestamp, fetched))
    return results


def message_time(text):
    t = text.translate(DIGITS)
    date = re.search(r'تاریخ\s*[:：]\s*(\d{4})/(\d{1,2})/(\d{1,2})', t)
    clock = re.search(r'ساعت\s*[:：]\s*(\d{1,2}):(\d{2})', t)
    if not date or not clock:
        return None
    jy,jm,jd = map(int,date.groups())
    if not (1200 <= jy <= 1600 and 1 <= jm <= 12 and 1 <= jd <= (31 if jm<=6 else 30)):
        return None
    try:
        return datetime(*jalali_to_date(jy,jm,jd), *map(int,clock.groups()), tzinfo=TEHRAN).isoformat()
    except ValueError:
        return None


def channel(html, source, fetched):
    text = html_text(html)
    # Each rate post starts with 18k gold. Date/time must occur inside that post.
    posts = re.split(r'(?=(?:طلای\s*۱۸\s*عیار|طلای\s*18\s*عیار)\s*[:：])', text)
    candidates = []
    for post in posts:
        ts = message_time(post)
        if ts:
            candidates.extend(parse_lines(post, source, fetched, ts))
    # Newest by source time per asset, not DOM order.
    latest = {}
    for q in sorted(candidates, key=lambda q: iso(q.published_at)):
        latest[q.asset] = q
    return list(latest.values())


def path(data, dotted):
    for key in dotted.split('.'):
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def json_mapping(body, source, fetched):
    data = json.loads(body); results=[]
    for mapping in source.get('mapping', []):
        try:
            value = number(path(data, mapping['price_path'])) * Decimal(str(mapping.get('multiplier', 1)))
            ts = path(data, mapping['time_path']) if mapping.get('time_path') else None
            if ts:
                ts = (datetime.fromtimestamp(float(ts), timezone.utc).isoformat() if mapping.get('time_format') == 'unix' else iso(ts).isoformat())
            results.append(quote(source, mapping['asset'], value, ts, fetched))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return results


