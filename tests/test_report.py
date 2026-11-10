import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from marketfeed.core import Quote
from marketfeed.report import build_report
from marketfeed.adapters import extract
NOW=datetime(2026,11,10,12,tzinfo=timezone.utc)
def quote(source,price=100,**kw):
 return replace(Quote(source,source,'gold_18k',Decimal(str(price)),NOW.isoformat(),NOW.isoformat(),'IRT','gram',purity='750',time_basis='source'),**kw)
def config():
 return {'primary_assets':['gold_18k'],'tolerances':{'gold_18k':1000},'policy':{'min_groups':3,'max_age_seconds':5400,'future_tolerance_seconds':120,'max_spread_ratio':.02},'sources':[{'id':s,'group':s,'url':'https://example.com','reference_eligible':True} for s in ('a','b','c')]}
class ReportTests(unittest.TestCase):
 def test_difference_sign_and_rial(self):
  cfg=config();r=build_report(cfg,[quote('a',100),quote('b',101),quote('c',102)],[],NOW)
  self.assertEqual(r['prices']['gold_18k']['price'],101)
  d=r['sources']['a']['prices']['gold_18k'];self.assertEqual(d['difference'],-1);self.assertEqual(d['difference_rial'],-10);self.assertTrue(d['is_close'])
 def test_boundary(self):
  cfg=config();cfg['tolerances']['gold_18k']=1
  r=build_report(cfg,[quote('a',100),quote('b',101),quote('c',103)],[],NOW)
  self.assertTrue(r['sources']['a']['prices']['gold_18k']['is_close']);self.assertFalse(r['sources']['c']['prices']['gold_18k']['is_close'])
 def test_missing(self):
  r=build_report(config(),[],[],NOW);d=r['sources']['a']['prices']['gold_18k']
  self.assertFalse(d['is_success']);self.assertIsNone(d['difference']);self.assertIsNone(d['is_close']);self.assertIsNone(r['prices']['gold_18k']['price'])
 def test_stale_success_but_not_used(self):
  r=build_report(config(),[quote('a',published_at=(NOW-timedelta(days=1)).isoformat())],[],NOW)
  d=r['sources']['a']['prices']['gold_18k'];self.assertTrue(d['is_success']);self.assertFalse(d['is_usable']);self.assertFalse(d['used_in_reference']);self.assertEqual(d['reason'],'stale')
 def test_platform_not_reference(self):
  cfg=config();cfg['sources'][0]['reference_eligible']=False
  r=build_report(cfg,[quote('a',100),quote('b',110)],[],NOW)
  self.assertEqual(r['prices']['gold_18k']['price'],110);self.assertFalse(r['sources']['a']['prices']['gold_18k']['used_in_reference'])
 def test_duplicate_group(self):
  cfg=config();cfg['sources'][1]['group']='a'
  r=build_report(cfg,[quote('a'),quote('b',102,group='a')],[],NOW)
  self.assertEqual(r['prices']['gold_18k']['source_count'],1)
 def test_disputed_still_numeric(self):
  r=build_report(config(),[quote('a',100),quote('b',101),quote('c',150)],[],NOW)
  self.assertEqual(r['prices']['gold_18k']['status'],'disputed');self.assertEqual(r['prices']['gold_18k']['price'],101)
 def test_observed_time_honesty(self):
  r=build_report(config(),[quote('a',time_basis='observed_only')],[],NOW)
  self.assertIsNone(r['prices']['gold_18k']['published_at']);self.assertIsNone(r['prices']['gold_18k']['price']);self.assertFalse(r['prices']['gold_18k']['uses_observed_time'])
 def test_milli_scale(self):
  s={'id':'m','group':'m','url':'https://milli.gold','adapter':'milli','kind':'platform'}
  r=extract('{"data":{"date":"2026-11-10T14:22:00","price18":250720}}',s,NOW.isoformat())
  self.assertEqual(r[0].price,25072000);self.assertEqual(r[0].kind,'platform')
 def test_estjt_scale(self):
  s={'id':'e','group':'e','url':'https://www.estjt.ir/tv/','adapter':'estjt','allow_observed_time':True}
  r=extract('<span data-field="IRG18">25,151,300</span>',s,NOW.isoformat())
  self.assertEqual(r[0].price,25151300);self.assertEqual(r[0].time_basis,'unknown')
 def test_tgju_scale(self):
  s={'id':'t','group':'t','url':'https://www.tgju.org/','adapter':'tgju','allow_observed_time':True}
  r=extract('<div data-market-row="geram18"><span data-market-name="p">255,446,000</span></div>',s,NOW.isoformat())
  self.assertEqual(r[0].price,25544600)
 def test_source_registry_unique(self):
  cfg=json.loads(Path('sources.json').read_text(encoding='utf-8'))
  self.assertEqual(len({s['id'] for s in cfg['sources']}),len(cfg['sources']))
  self.assertEqual(len({s['url'] for s in cfg['sources']}),len(cfg['sources']))
 def test_json_serializable(self):
  json.dumps(build_report(config(),[quote('a')],[],NOW),allow_nan=False)
if __name__=='__main__':unittest.main()

class ExtraAdapters(unittest.TestCase):
 def test_nobitex_rial_conversion(self):
  s={'id':'n','group':'n','url':'https://apiv2.nobitex.ir','adapter':'nobitex','allow_observed_time':True}
  r=extract('{"status":"ok","bids":[["2618000","10"]],"asks":[["2620000","10"]]}',s,NOW.isoformat())
  self.assertEqual(r[0].price,261900)
 def test_profile(self):
  s={'id':'t','group':'t','url':'https://www.tgju.org/profile/geram18','adapter':'tgju','allow_observed_time':True}
  r=extract('<span data-col="info.last_trade.PDrCotVal">256,127,000</span>',s,NOW.isoformat())
  self.assertEqual(r[0].price,25612700)
 def test_kifpool_repeated_links(self):
  s={'id':'k','group':'k','url':'https://kifpool.me/live','adapter':'kifpool','allow_observed_time':True}
  r=extract('<p>18k Gold</p><p>خرید طلا</p><p>خرید طلا</p><p>25,201,131</p><p>تومان</p>',s,NOW.isoformat())
  self.assertEqual(r[0].price,25201131)

class FreshnessTests(unittest.TestCase):
 def test_undated_mirror_cannot_refresh_old_publisher(self):
  cfg=config()
  old=quote('a',100,published_at=(NOW-timedelta(days=1)).isoformat())
  mirror=quote('b',102,group='a',time_basis='observed_only')
  r=build_report(cfg,[old,mirror],[],NOW)
  self.assertIsNone(r['prices']['gold_18k']['price'])
  self.assertEqual(r['sources']['b']['prices']['gold_18k']['reason'],'unknown_time')
