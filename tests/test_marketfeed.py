import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from marketfeed.core import Quote, number
from marketfeed.report import build_report
from marketfeed.parsers import channel, json_mapping, message_time, jalali_to_date, parse_lines
from marketfeed.__main__ import atomic_json, load_config
NOW=datetime(2026,11,10,9,0,tzinfo=timezone.utc)
POLICY={'min_groups':3,'max_age_seconds':900,'future_tolerance_seconds':60,'max_spread_ratio':0.02}
SOURCE={'id':'eco','group':'ecogold','url':'https://example.org','assets':['gold_18k','silver_999','coin_emami','gold_ounce']}
def q(group,price=100,**kw):
 return replace(Quote(group,group,'gold_18k',Decimal(str(price)),NOW.isoformat(),NOW.isoformat(),'IRT','gram',purity='750',time_basis='source'),**kw)
def result(quotes,**changes):
 cfg={'primary_assets':['gold_18k'],'tolerances':{'gold_18k':1000},'policy':{**POLICY,**changes},'sources':[{'id':x.source,'group':x.group,'url':'https://example.org'} for x in {q.source:q for q in quotes}.values()]}
 report=build_report(cfg,quotes,[],NOW)
 value=report['prices']['gold_18k']
 return {**value,'group_count':value['source_count'],'rejected':[{'source':s,'reason':d['prices']['gold_18k']['reason']} for s,d in report['sources'].items() if not d['prices']['gold_18k']['is_usable']]}
class CoreTests(unittest.TestCase):
 def test_digits(self): self.assertEqual(number('۲۵٬۳۹۸٬۰۰۰'),Decimal('25398000'))
 def test_arabic(self): self.assertEqual(number('١٢٣٫٥'),Decimal('123.5'))
 def test_invalid(self):
  for v in ['0','-1','NaN','Infinity','1,2oops','']:
   with self.assertRaises(ValueError): number(v)
 def test_median(self): self.assertEqual(result([q('a',100),q('b',101),q('c',102),q('d',140)],max_spread_ratio=.5)['price'],101.5)
 def test_disagreement(self):
  r=result([q('a',100),q('b',101),q('c',140)]);self.assertEqual(r['status'],'disputed');self.assertEqual(r['price'],101)
 def test_one_vote(self):
  r=result([q('a'),q('a',101,source='telegram'),q('b')]);self.assertEqual(r['group_count'],2);self.assertEqual(r['status'],'provisional')
 def test_newest(self): self.assertEqual(result([q('a'),q('a',120,source='old',published_at=(NOW-timedelta(seconds=60)).isoformat()),q('b'),q('c')])['price'],100)
 def test_stale(self): self.assertEqual(result([q('a',published_at=(NOW-timedelta(hours=1)).isoformat())])['rejected'][0]['reason'],'stale')
 def test_missing_time(self): self.assertIsNone(result([q('a',published_at=None)])['price'])
 def test_future(self): self.assertEqual(result([q('a',published_at=(NOW+timedelta(hours=1)).isoformat())])['rejected'][0]['reason'],'future_timestamp')
 def test_incompatible(self):
  for change in [{'currency':'IRR'},{'purity':'740'},{'unit':'milligram'},{'kind':'ask'},{'market':'Saveh'}]: self.assertEqual(result([q('a',**change)])['group_count'],0)
 def test_empty(self): self.assertEqual(result([])['status'],'unavailable')
 def test_zero(self): self.assertEqual(result([q('a',0)])['group_count'],0)
class ParserTests(unittest.TestCase):
 def test_channel(self):
  body='<div>طلای ۱۸ عیار: ۲۵٬۳۹۸٬۰۰۰ تومان<br>نقره ۹۹۹: 452,000 تومان<br>اونس طلا: 4,121$<br>ساعت: ۱۲:۰۰<br>تاریخ: ۱۴۰۵/۰۸/۱۹</div>'
  quotes=channel(body,SOURCE,NOW.isoformat());self.assertEqual(len(quotes),3);self.assertEqual(quotes[0].price,Decimal('25398000'));self.assertEqual(quotes[0].published_at,'2026-11-10T12:00:00+03:30')
 def test_order(self):
  body=''.join(f'<div>طلای ۱۸ عیار: {price} تومان<br>ساعت: ۱۲:۰۰<br>تاریخ: ۱۴۰۵/۰۸/{day}</div>' for day,price in [(19,100),(18,99)])
  self.assertEqual(channel(body,SOURCE,NOW.isoformat())[0].price,100)
 def test_ad(self): self.assertEqual(channel('<div>تبلیغات</div>',SOURCE,NOW.isoformat()),[])
 def test_unknown_silver(self): self.assertEqual(parse_lines('نقره: 452000 تومان',SOURCE,NOW.isoformat()),[])
 def test_rial(self): self.assertEqual(parse_lines('طلای ۱۸ عیار: 1000 ریال',SOURCE,NOW.isoformat())[0].price,100)
 def test_currency(self): self.assertEqual(parse_lines('طلای ۱۸ عیار: 100$',SOURCE,NOW.isoformat()),[])
 def test_no_date(self): self.assertIsNone(message_time('طلای ۱۸ عیار: 100 تومان'))
 def test_jalali(self):
  for j,g in [((1405,8,19),(2026,11,10)),((1403,1,1),(2024,3,20)),((1403,12,30),(2025,3,20)),((1404,1,1),(2025,3,21))]: self.assertEqual(jalali_to_date(*j),g)
 def test_mapping(self):
  s={**SOURCE,'mapping':[{'asset':'gold_18k','price_path':'data.price','time_path':'data.time','multiplier':100}]}
  self.assertEqual(json_mapping(json.dumps({'data':{'price':10,'time':NOW.isoformat()}}),s,NOW.isoformat())[0].price,1000)
 def test_mapping_missing_time(self):
  s={**SOURCE,'mapping':[{'asset':'gold_18k','price_path':'value'}]};self.assertEqual(result(json_mapping('{"value":100}',s,NOW.isoformat()))['group_count'],0)
 def test_schema_drift(self):
  s={**SOURCE,'mapping':[{'asset':'gold_18k','price_path':'missing'}]};self.assertEqual(json_mapping('{}',s,NOW.isoformat()),[])
 def test_epoch(self):
  s={**SOURCE,'mapping':[{'asset':'gold_18k','price_path':'value','time_path':'ts','time_format':'unix'}]}
  self.assertEqual(json_mapping(json.dumps({'value':100,'ts':NOW.timestamp()}),s,NOW.isoformat())[0].published_at,NOW.isoformat())
 def test_atomic(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'data.json';atomic_json(p,{'price':'100'});self.assertEqual(json.loads(p.read_text()),{'price':'100'})
if __name__=='__main__': unittest.main()
