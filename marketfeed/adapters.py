"""Source-specific, read-only adapters. A 200 response is never enough for success."""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from bs4 import BeautifulSoup
from .core import ASSETS, DIGITS, number, iso
from .parsers import quote, parse_lines, channel, json_mapping, message_time, jalali_to_date, TEHRAN, PATTERNS

TGJU={'geram18':'gold_18k','silver_999':'silver_999','sekee':'coin_emami','sekeb':'coin_bahar',
      'nim':'coin_half','rob':'coin_quarter','ons':'gold_ounce','geram24':'gold_24k',
      'gerami':'coin_gram','price_dollar_rl':'usd_cash','crypto-tether':'usdt'}
ESTJT={'GOLD':'gold_ounce','IRG18':'gold_18k','IRG24':'gold_24k','IRCOLD':'coin_bahar',
       'IRCNEW':'coin_emami','IRC2':'coin_half','IRC4':'coin_quarter','IRCGRAM':'coin_gram'}
MONTHS={'فروردین':1,'اردیبهشت':2,'خرداد':3,'تیر':4,'مرداد':5,'شهریور':6,'مهر':7,'آبان':8,'آذر':9,'دی':10,'بهمن':11,'اسفند':12}

def pubdate(text, fetched):
    text=text.translate(DIGITS)
    ts=message_time(text)
    if ts:return ts
    # Full named Jalali dates only; no guess from a page's generic clock.
    m=re.search(r'(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(14\d{2})(?:[^\n]{0,25}?(\d{1,2}):(\d{2})(?::(\d{2}))?)?',text)
    if m:
        if m[4]:
            try:
                y,mo,d=jalali_to_date(int(m[3]),MONTHS[m[2]],int(m[1]))
                return datetime(y,mo,d,int(m[4]),int(m[5]),int(m[6] or 0),tzinfo=TEHRAN).isoformat()
            except ValueError:
                return None
    return None

def make(s,asset,value,fetched,ts=None):
    return quote(s,asset,value,ts,fetched)


def tgju_time(node, profile=False):
    """Only a complete timestamp attached to this trade; never a site/news clock."""
    stamp = node.select_one('[data-col="info.last_trade.datetime"][datetime]') if profile else node.select_one('time[datetime]')
    if stamp:
        try:
            return iso(stamp['datetime']).isoformat()
        except (ValueError, TypeError):
            return None
    # A full date and clock in a trade time field can be trusted. Clock-only and
    # day/month-only displays cannot establish a publication instant.
    stamp = node.select_one('[data-col="info.last_trade.DTgreg"]')
    if stamp:
        try:
            return iso(stamp.get_text(strip=True)).isoformat()
        except (ValueError, TypeError):
            return None
    return None


def tgju_message(text, source, fetched, timestamp):
    """TGJU's explicit current-price block, excluding bubbles and retail coins."""
    out = []
    for asset, pattern in PATTERNS.items():
        if asset not in source.get('assets', PATTERNS):
            continue
        expression = r'(?:^|\n)(?:قیمت\s*)?' + pattern + r'\s*\.{3,}\s*قیمت لحظه ای\s*:\s*([۰-۹0-9٬,.]+)\s*(ریال|تومان|دلار|\$)'
        match = re.search(expression, text)
        if not match:
            continue
        currency = match[2]
        if (asset == 'gold_ounce') != (currency in ('دلار', '$')):
            continue
        try:
            value = number(match[1]) / (10 if currency == 'ریال' else 1)
            out.append(make(source, asset, value, fetched, timestamp))
        except ValueError:
            continue
    return out

def _extract(body,s,fetched):
    adapter=s['adapter']
    from .web_sources import WEB_ADAPTERS
    if adapter in WEB_ADAPTERS:
        return WEB_ADAPTERS[adapter](body, s, fetched)
    if adapter in ('tabdeal_depth', 'dollargold'):
        from .new_sources import tabdeal_depth, dollargold_posts
        return (tabdeal_depth if adapter=='tabdeal_depth' else dollargold_posts)(body,s,fetched)
    if adapter=='channel':
        soup=BeautifulSoup(body,'html.parser')
        messages=soup.select('.tgme_widget_message_text') or soup.select('div[class*="Text_text"]')
        found=[]
        for message in messages:
            text=message.get_text('\n',strip=True)
            ts=message_time(text) or pubdate(text,fetched)
            if not ts:
                parent=message.find_parent(class_='tgme_widget_message')
                time=parent.select_one('time[datetime]') if parent else None
                ts=time.get('datetime') if time else pubdate(text,fetched)
            found.extend(tgju_message(text,s,fetched,ts) if s.get('group')=='tgju' else parse_lines(text,s,fetched,ts))
        if not messages:return channel(body,s,fetched)
        latest={}
        dated=[]
        for q in found:
            try:
                if q.published_at: iso(q.published_at)
                dated.append(q)
            except (ValueError, TypeError):
                continue
        for q in sorted(dated,key=lambda x:iso(x.published_at) if x.published_at else datetime.min.replace(tzinfo=timezone.utc)):
            latest[q.asset]=q
        return list(latest.values())
    if adapter=='json_mapping':return json_mapping(body,s,fetched)
    if adapter=='milli':
        d=json.loads(body)['data'];dt=datetime.fromisoformat(d['date'])
        ts=dt.replace(tzinfo=TEHRAN).isoformat() if dt.tzinfo is None else dt.isoformat()
        # External price18 is rial/milligram; 1000 mg per gram and 10 rial per toman.
        return [make(s,'gold_18k',number(d['price18'])*100,fetched,ts)]
    if adapter=='navasan':
        d=json.loads(body);out=[]
        for key,asset in {'18ayar':'gold_18k','sekkeh':'coin_emami','bahar':'coin_bahar','nim':'coin_half','rob':'coin_quarter','gerami':'coin_gram','usd_xau':'gold_ounce','usdt':'usdt'}.items():
            if key not in d:continue
            row=d[key];ts=datetime.fromtimestamp(float(row['date']),timezone.utc).isoformat()
            out.append(make(s,asset,number(row['value']),fetched,ts))
        return out
    if adapter in ('nobitex','wallex','bitpin'):
        d=json.loads(body)
        if adapter=='nobitex':
            if d.get('status') != 'ok':
                raise ValueError('unsuccessful orderbook response')
            bid=number(d['bids'][0][0]);ask=number(d['asks'][0][0])
            if bid > ask:
                raise ValueError('crossed orderbook')
            value=(bid+ask)/Decimal(20)  # IRR midpoint -> IRT; never mixed with last trade or ask/bid.
            ts=datetime.fromtimestamp(float(d['lastUpdate'])/1000,timezone.utc).isoformat() if d.get('lastUpdate') else None
        elif adapter=='wallex':
            row=d['result']['symbols']['USDTTMN']['stats'];value=number(row['lastPrice']);ts=None
        else:
            rows=d['results'] if isinstance(d,dict) else d
            row=next(x for x in rows if x.get('code')=='USDT_IRT')
            value=number(row['price']);ts=None
        return [make(s,'usdt',value,fetched,ts)]
    soup=BeautifulSoup(body,'html.parser');out=[]
    if adapter=='ontala':
        marker=soup.select_one('.bb-last strong')
        ts=None
        if marker:
            match=re.fullmatch(r'(14\d{2})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})',marker.get_text(' ',strip=True).translate(DIGITS))
            if match:
                try:
                    y,m,d=jalali_to_date(int(match[1]),int(match[2]),int(match[3]))
                    ts=datetime(y,m,d,int(match[4]),int(match[5]),tzinfo=TEHRAN).isoformat()
                except ValueError:
                    pass
        return parse_lines(soup.get_text('\n',strip=True),s,fetched,ts)
    if adapter=='tgju':
        if '/profile/' in s['url']:
            slug=s['url'].rstrip('/').split('/')[-1]
            asset=TGJU.get(slug)
            el=soup.select_one('[data-col="info.last_trade.PDrCotVal"]')
            if asset and el:
                return [make(s,asset,number(el.get_text(strip=True))/(1 if asset=='gold_ounce' else 10),fetched,tgju_time(soup,profile=True))]
        for slug,asset in TGJU.items():
            if asset not in s.get('assets',TGJU.values()):continue
            row=soup.select_one('tr[data-market-row="'+slug+'"]') or soup.select_one('[data-market-row="'+slug+'"]')
            if not row:continue
            el=row.select_one('.market-price-irr') if asset=='usdt' else row.select_one('[data-market-name="p"]')
            raw=(el.get_text(strip=True) if el else row.get('data-price'))
            if not raw:continue
            ts=tgju_time(row)
            try:out.append(make(s,asset,number(raw)/(1 if asset=='gold_ounce' else 10),fetched,ts))
            except ValueError:continue
        return out
    if adapter=='estjt':
        text=soup.get_text(' ',strip=True)
        updated=re.search(r'آخرین\s*بروزرسانی\s*قیمت\s*:\s*(.+)',text)
        ts=pubdate(updated[1],fetched) if updated else None
        for field,asset in ESTJT.items():
            row=soup.select_one('[data-field="'+field+'"]')
            if row:
                try:out.append(make(s,asset,number(row.get_text(strip=True).replace('$','')),fetched,ts))
                except ValueError:pass
        return out
    if adapter=='chartix':
        # Exact IRT quote from visible snapshot; ignore arbitrary numbers in SEO content.
        text=soup.get_text(' ',strip=True)
        m=re.search(r'([\d,]+(?:\.\d+)?)\s+IRT',text)
        return [make(s,'gold_18k',number(m[1]),fetched)] if m else []
    text=soup.get_text('\n',strip=True)
    if s['id']=='wallgold':
        for script in soup.select('script[type="application/ld+json"]'):
            raw=script.get_text()
            if 'FAQPage' not in raw:continue
            d=json.loads(raw)
            for item in d.get('mainEntity',[]):
                answer=item.get('acceptedAnswer',{}).get('text','')
                if 'یک گرم' not in answer:continue
                m=re.search(r'([۰-۹0-9٬,]+)\s*تومان',answer)
                if m:return [make(s,'gold_18k',number(m[1]),fetched,pubdate(answer,fetched))]
    if adapter=='kifpool':
        mapping={'18k Gold':'gold_18k','Silver':'silver_999','Imami Coin':'coin_emami','Bahar Coin':'coin_bahar','Half Coin':'coin_half','Quarter Coin':'coin_quarter','Gram Coin':'coin_gram'}
        for label,asset in mapping.items():
            if asset=='silver_999':continue # displayed silver purity is unspecified
            m=re.search(re.escape(label)+r'\s*(?:خرید[^\n]*\s*)*([\d,]+)\s*تومان',text)
            if m:out.append(make(s,asset,number(m[1]),fetched))
        return out
    # Generic adapter intentionally requires a label followed directly by price + unit.
    # Page-wide dates may belong to news or a clock, not to the extracted rate.
    ts=None
    for q in parse_lines(text,s,fetched,ts):
        out.append(make(s,q.asset,q.price,fetched,q.published_at))
    return out


def extract(body, source, fetched):
    quotes = _extract(body, source, fetched)
    return [q for q in quotes if q.asset in source.get('assets', ASSETS)]
