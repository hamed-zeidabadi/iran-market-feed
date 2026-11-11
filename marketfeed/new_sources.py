"""Validated public spot depth and explicit rate posts; no inferred timestamps."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re

from bs4 import BeautifulSoup

from .core import iso, number
from .parsers import message_time, parse_lines, quote

TABDEAL_STREAM = 'usdtirt@depth@2000ms'
TABDEAL_TRADES_URL = 'https://api1.tabdeal.org/r/api/v1/trades?symbol=USDTIRT&limit=5'


def milliseconds(value):
    # The documented event E is Unix milliseconds, not sequence ID or server time.
    if type(value) is not int or not 1_000_000_000_000 <= value < 10_000_000_000_000:
        raise ValueError('expected Unix milliseconds')
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def depth_midpoint(bids, asks):
    def levels(rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError('empty orderbook')
        prices = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError('invalid depth level')
            price = number(row[0])
            try:
                quantity = Decimal(str(row[1]))
            except InvalidOperation as exc:
                raise ValueError('invalid depth quantity') from exc
            if not quantity.is_finite() or quantity < 0:
                raise ValueError('invalid depth quantity')
            if quantity > 0:
                prices.append(price)
        if not prices:
            raise ValueError('no executable depth')
        return prices
    bid, ask = max(levels(bids)), min(levels(asks))
    if bid > ask:
        raise ValueError('crossed orderbook')
    return (bid + ask) / 2


def tabdeal_depth(body, source, fetched):
    envelope = json.loads(body)
    if isinstance(envelope, list):
        # Only the receiver's fixed, documented USDTIRT trades URL supplies lists.
        # An old last trade is still dated honestly and never becomes a midpoint.
        if not envelope:
            raise ValueError('empty trade response')
        trades = []
        for trade in envelope:
            if not isinstance(trade, dict):
                raise ValueError('invalid trade')
            timestamp = milliseconds(trade.get('time'))
            price = number(trade.get('price'))
            number(trade.get('qty'))
            trades.append((iso(timestamp), price, timestamp))
        _, value, timestamp = max(trades)
        return [quote(source, 'usdt', value, timestamp, fetched, kind='last_trade')]
    if not isinstance(envelope, dict) or not isinstance(envelope.get('data'), dict):
        raise ValueError('invalid depth envelope')
    data = envelope.get('data', {})
    if envelope.get('stream') != TABDEAL_STREAM or data.get('e') != 'depthUpdate' or data.get('s') != 'USDTIRT':
        raise ValueError('not the USDT/IRT spot depth snapshot')
    timestamp = milliseconds(data.get('E'))
    value = depth_midpoint(data.get('b'), data.get('a'))
    # Tabdeal IRT prices are already toman, unlike Nobitex's IRR depth prices.
    return [quote(source, 'usdt', value, timestamp, fetched, kind='midpoint')]


def dollargold_posts(body, source, fetched):
    soup = BeautifulSoup(body, 'html.parser')
    latest = {}
    for message in soup.select('.tgme_widget_message'):
        # Forwarded messages retain the original upstream time, which this feed
        # cannot independently establish; do not date them with repost time.
        if message.select_one('.tgme_widget_message_forwarded_from'):
            continue
        node = message.select_one('.tgme_widget_message_text')
        if not node:
            continue
        text = node.get_text('\n', strip=True)
        if re.search(r'تبلیغ|آگهی|رپرتاژ|پیش[‌\s]*بینی|خبر فوری', text):
            continue
        timestamp = message_time(text)
        if not timestamp or not re.search(r'_{5,}', text):
            continue
        quotes = parse_lines(text, source, fetched, timestamp)
        # A dated multi-asset rate bulletin, not a headline or isolated ad quote.
        if len(quotes) < 3:
            continue
        tether = re.search(r'(?:^|\n)[^\w\n]*تتر\s*:\s*([۰-۹٠-٩0-9٬,٫.]+)\s*(تومان|ریال)(?:\s|$)', text)
        if tether and 'usdt' in source.get('assets', []):
            try:
                value = number(tether[1]) / (10 if tether[2] == 'ریال' else 1)
                quotes.append(quote(source, 'usdt', value, timestamp, fetched, kind='indicative'))
            except ValueError:
                pass
        for candidate in quotes:
            if candidate.asset not in latest or iso(candidate.published_at) > iso(latest[candidate.asset].published_at):
                latest[candidate.asset] = candidate
    return list(latest.values())
