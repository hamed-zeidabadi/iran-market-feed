"""Shared quote model and strict numeric/time parsing."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

UTC = timezone.utc
PRIMARY_ASSETS = ('gold_18k', 'silver_999', 'coin_bahar', 'coin_emami', 'coin_half', 'coin_quarter', 'gold_ounce', 'usdt')
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬٫', '01234567890123456789,.')
ASSETS = {
 'gold_18k': {'label': 'طلای ۱۸ عیار', 'currency': 'IRT', 'unit': 'gram', 'purity': '750'},
 'silver_999': {'label': 'نقره ۹۹۹', 'currency': 'IRT', 'unit': 'gram', 'purity': '999'},
 'coin_bahar': {'label': 'سکه بهار آزادی', 'currency': 'IRT', 'unit': 'coin'},
 'coin_emami': {'label': 'سکه امامی', 'currency': 'IRT', 'unit': 'coin'},
 'coin_half': {'label': 'نیم سکه', 'currency': 'IRT', 'unit': 'coin'},
 'coin_quarter': {'label': 'ربع سکه', 'currency': 'IRT', 'unit': 'coin'},
 'gold_ounce': {'label': 'اونس طلا', 'currency': 'USD', 'unit': 'troy_ounce'},
 'gold_24k': {'label': 'طلای ۲۴ عیار', 'currency': 'IRT', 'unit': 'gram', 'purity': '999.9'},
 'coin_gram': {'label': 'سکه گرمی', 'currency': 'IRT', 'unit': 'coin'},
 'usd_cash': {'label': 'دلار آزاد', 'currency': 'IRT', 'unit': 'USD'},
 'usdt': {'label': 'تتر', 'currency': 'IRT', 'unit': 'USDT', 'quote_type': 'midpoint'},
}


def number(value):
    text = str(value).translate(DIGITS).replace(',', '').replace('\u200e', '').strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        raise ValueError('invalid numeric value')
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('invalid numeric value') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('price must be positive and finite')
    return result


def iso(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class Quote:
    source: str
    group: str
    asset: str
    price: Decimal
    published_at: str | None
    fetched_at: str
    currency: str
    unit: str
    market: str = 'IR-national'
    kind: str = 'indicative'
    purity: str | None = None
    evidence_url: str = ''
    time_basis: str = 'unknown'

    def json(self):
        return {**asdict(self), 'price': str(self.price)}

