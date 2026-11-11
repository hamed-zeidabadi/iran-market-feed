from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from marketfeed.adapters import extract
from marketfeed.__main__ import load_config, read_source
from marketfeed.core import ASSETS, PARSIAN_ASSETS
from marketfeed.report import build_report
from test_report import NOW


def source(adapter, assets, **kwargs):
    return dict(id='sample', group='sample', url='https://example.org/', adapter=adapter,
                enabled=True, reference_eligible=True, assets=assets, **kwargs)


def report(sources, quotes):
    cfg = load_config('sources.json')
    cfg['sources'] = sources
    return build_report(cfg, quotes, [], NOW)


class TgjuWebTests(unittest.TestCase):
    def setUp(self):
        self.source = source('tgju_snapshot', ['gold_18k', 'silver_999', 'gold_ounce', 'usdt'])
        self.row = {'p':'250,000,000', 'ts':'2026-11-10 15:29:00', 't_en':'15:29:00'}

    def parse(self, rows):
        return extract(json.dumps({'current':rows}), self.source, NOW.isoformat())

    def test_exact_asset_currency_and_full_publisher_timestamp(self):
        result = self.parse({'geram18':self.row, 'ons':{**self.row,'p':'4,000'},
                             'silver':self.row, 'silver_999':{**self.row,'p':'4,500,000'},
                             'geram24':self.row})
        self.assertEqual({q.asset:q.price for q in result},
                         {'gold_18k':25000000,'gold_ounce':4000,'silver_999':450000})
        self.assertEqual(result[0].published_at,'2026-11-10T15:29:00+03:30')

    def test_date_only_midnight_sentinel_is_not_trade_time(self):
        quotes=self.parse({'geram18':{**self.row,'ts':'2026-11-10 00:00:00','t_en':'10 Nov'}})
        item=report([self.source],quotes)['sources']['sample']['prices']['gold_18k']
        self.assertIsNone(item['published_at'])
        self.assertEqual(item['reason'],'unknown_time')
        self.assertFalse(item['used_in_reference'])

    def test_mismatched_display_clock_invalid_date_and_missing_time(self):
        for changes in [{'t_en':'12:00:00'},{'ts':'2026-02-30 15:29:00'}, {'ts':''}]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.parse({'geram18':{**self.row,**changes}})[0].published_at)

    def test_stale_source_time_cannot_be_refreshed_by_fetch(self):
        quotes=self.parse({'geram18':{**self.row,'ts':'2026-11-09 15:29:00'}})
        self.assertEqual(report([self.source],quotes)['sources']['sample']['prices']['gold_18k']['reason'],'stale')

    def test_usdt_uses_irr_variant_and_indicative_is_not_midpoint(self):
        quotes=self.parse({'crypto-tether':{**self.row,'p':'1.0'},
                           'crypto-tether-irr':{**self.row,'p':'2600000'}})
        self.assertEqual([(q.asset,q.price,q.kind) for q in quotes],[('usdt',Decimal(260000),'indicative')])
        self.assertIsNone(report([self.source],quotes)['prices']['usdt']['price'])

    def test_website_and_channel_share_one_publisher_vote(self):
        web={**self.source,'group':'tgju'}
        other={**web,'id':'channel'}
        quotes=self.parse({'geram18':self.row})
        from dataclasses import replace
        quotes=[replace(quotes[0],group='tgju'),replace(quotes[0],source='channel',group='tgju',price=Decimal(25001000))]
        result=report([web,other],quotes)
        self.assertEqual(result['prices']['gold_18k']['source_count'],1)

    def test_reader_uses_the_homepages_public_snapshot(self):
        with patch('marketfeed.__main__.read_https',return_value=(b'{}',200)) as read:
            read_source({**self.source,'url':'https://call2.tgju.org/ajax.json'},10)
        self.assertTrue(read.call_args.args[0].startswith('https://call2.tgju.org/ajax.json?rev='))


PARSIAN_HTML='''<span id="priceLastTime">15:29 1405/08/19</span>
<h1>سکه پارسیان با عیار 750</h1><p>تمامی نرخ ها به تومان می باشد</p>
<p>بدون لحاظ شدن مالیات بر ارزش افزوده</p>
<div class="rateBox"><h2>پارسیان 100 سوت</h2><h3>۲,۶۰۰,۰۰۰</h3></div>
<div class="rateBox"><h2>پارسیان 1گرم</h2><h3>۲۶,۷۰۰,۰۰۰</h3></div>
<div class="rateBox"><h2>پارسیان 1.1گرم</h2><h3>۲۹,۰۰۰,۰۰۰</h3></div>
<div class="rateBox"><h2>پارسیان شمش1گرم</h2><h3>۳۵,۰۰۰,۰۰۰</h3></div>'''


class ParsianTests(unittest.TestCase):
    def setUp(self):
        self.source=source('tala_parsian',list(PARSIAN_ASSETS))

    def parse(self,body=PARSIAN_HTML):
        return extract(body,self.source,NOW.isoformat())

    def test_exact_weight_purity_and_ex_vat_price_no_scaling(self):
        result=self.parse()
        self.assertEqual({q.asset:q.price for q in result},
                         {'parsian_100mg':2600000,'parsian_1000mg':26700000,'parsian_1100mg':29000000})
        self.assertTrue(all((q.purity,q.unit,q.currency,q.kind)==('750','coin','IRT','indicative_ex_vat') for q in result))
        self.assertTrue(all(q.published_at=='2026-11-10T15:29:00+03:30' for q in result))

    def test_unknown_currency_purity_or_tax_basis_not_accepted(self):
        for old,new in [('750','995'),('تومان','واحد'),('بدون لحاظ شدن مالیات','با لحاظ شدن مالیات')]:
            with self.subTest(old=old):self.assertEqual(self.parse(PARSIAN_HTML.replace(old,new)),[])

    def test_no_date_from_news_and_no_freshening_unknown_time(self):
        result=self.parse(PARSIAN_HTML.replace('id="priceLastTime"','class="page-clock"'))
        output=report([self.source],result)
        self.assertIsNone(output['parsian']['parsian_1000mg']['price'])
        self.assertIsNone(output['sources']['sample']['parsian']['parsian_1000mg']['published_at'])

    def test_negative_zero_nonfinite_and_wrong_weight_are_rejected(self):
        for value in ['-10','0','NaN']:
            html=PARSIAN_HTML.replace('۲۶,۷۰۰,۰۰۰',value)
            self.assertNotIn('parsian_1000mg',[q.asset for q in self.parse(html)])
        self.assertNotIn('parsian_1100mg',[q.asset for q in self.parse(PARSIAN_HTML.replace('1.1گرم','1.1001گرم'))])

    def test_same_aggregation_contract_and_stable_weight_catalog(self):
        result=report([self.source],self.parse())
        self.assertEqual(set(result['prices']),set(load_config('sources.json')['primary_assets']))
        self.assertEqual(set(result['parsian']),set(PARSIAN_ASSETS))
        self.assertEqual(result['parsian']['parsian_1000mg']['price'],26700000)
        self.assertEqual(result['parsian']['parsian_1000mg']['weight_grams'],1)
        self.assertEqual(result['parsian']['parsian_1000mg']['status'],'provisional')
        self.assertIsNone(result['parsian']['parsian_50mg']['price'])
        schema=json.loads(Path('docs/prices.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema).validate(result)
        empty=report([self.source],[])
        self.assertEqual(empty['sources']['sample']['parsian']['parsian_1000mg'].keys(),
                         result['sources']['sample']['parsian']['parsian_1000mg'].keys())

    def test_raw_gold_value_never_votes_as_packaged_product(self):
        taban=source('tabangohar_parsian',list(PARSIAN_ASSETS))
        body=json.dumps({'date':'تاریخ ۱۹ آبان ۱۴۰۵','time':'15:29','x30':1200000,'x44':24000000,'x71':39000000,'x77':72000000})
        quotes=extract(body,taban,NOW.isoformat())
        self.assertEqual({q.asset:q.price for q in quotes},
                         {'parsian_50mg':1200000,'parsian_1000mg':24000000,'parsian_1600mg':39000000,'parsian_3000mg':72000000})
        self.assertTrue(all(q.kind=='metal_value' and q.published_at for q in quotes))
        self.assertIsNone(report([taban],quotes)['parsian']['parsian_1000mg']['price'])

    def test_public_taban_reader_matches_actual_form_request(self):
        taban={**self.source,'adapter':'tabangohar_parsian'}
        with patch('marketfeed.__main__.read_https',return_value=(b'{}',200)) as read:
            read_source(taban,10)
        self.assertEqual(read.call_args.kwargs['data'],b'item=all')

    def test_duplicate_and_unknown_weight_config_rejected(self):
        import tempfile
        for assets in [['parsian_1000mg']*2,['parsian_999mg']]:
            config=load_config('sources.json');config['parsian_assets']=assets
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'config.json';path.write_text(json.dumps(config),encoding='utf-8')
                with self.assertRaises(ValueError):load_config(path)


class PublisherPageTests(unittest.TestCase):
    def test_tala_last_price_not_daily_high_and_explicit_currency(self):
        src=source('tala_price',['gold_18k'],currency='IRT');src['url']='https://www.tala.ir/price/18k'
        html='<title>طلا 18 عیار</title><span id="priceLastTime">15:29 1405/08/19</span><div class="rateBox"><h2>بالاترین قیمت امروز</h2><h3>999</h3></div><div class="rateBox"><h2>آخرین قیمت</h2><h3>۲۵,۰۰۰,۰۰۰</h3></div>'
        result=extract(html,src,NOW.isoformat())
        self.assertEqual(result[0].price,25000000)
        self.assertEqual(extract(html,{**src,'currency':'IRR'},NOW.isoformat()),[])
        self.assertEqual(extract(html.replace('۲۵,۰۰۰,۰۰۰','-177000000'),src,NOW.isoformat()),[])

    def test_talaclinic_uses_embedded_date_time_not_telegram_delivery(self):
        src=source('talaclinic',['gold_18k','gold_ounce','coin_emami','silver_999'])
        html='''<div class="tgme_widget_message"><time datetime="2026-11-10T12:00:00Z"></time>
        <div class="tgme_widget_message_text">تابان گوهر آخرین بروزرسانی: ۱۵:۲۹ 🕰 ۱۹ آبان ۱۴۰۵
        گرم ۱۸ تهران: ۲۵,۰۰۰,۰۰۰ تومان<br>انس طلا: ۴,۰۰۰ دلار<br>
        سکه طرح جدید: ۲۵۱,۰۰۰ تومان<br>انس نقره: ۵۰ دلار</div></div>'''
        result=extract(html,src,NOW.isoformat())
        self.assertEqual([(q.asset,q.price) for q in result],[('gold_18k',25000000),('gold_ounce',4000)])
        self.assertTrue(all(q.published_at=='2026-11-10T15:29:00+03:30' for q in result))
        self.assertEqual(extract(html.replace('آخرین بروزرسانی:','خبر:'),src,NOW.isoformat()),[])
        self.assertEqual(extract(html.replace('<time','<span class="tgme_widget_message_forwarded_from"></span><time'),src,NOW.isoformat()),[])

    def test_wallex_query_refresh_not_price_time_or_usdt_ounce_as_usd(self):
        src=source('wallex_gold',['gold_18k','gold_ounce'])
        payload={'props':{'pageProps':{'currentDateTime':NOW.isoformat(),'dehydratedState':{'queries':[
            {'queryKey':['getGoldPrice',[]],'state':{'dataUpdatedAt':int(NOW.timestamp()*1000),
             'data':{'success':True,'result':{'carats_18':'25000000','last_usdt_price':'4000'}}}}]}}}}
        quotes=extract('<script id="__NEXT_DATA__">'+json.dumps(payload)+'</script>',src,NOW.isoformat())
        self.assertEqual([(q.asset,q.price,q.kind,q.published_at) for q in quotes],[('gold_18k',25000000,'synthetic',None)])
        self.assertIsNone(report([src],quotes)['prices']['gold_18k']['price'])

    def test_alanchand_ignores_bubbles_and_page_update_clock(self):
        src=source('alanchand',['gold_18k'])
        html=f'<div class="c-updated"><time datetime="{NOW.isoformat()}"></time></div><span data-live="g.18ayar" data-f="s">۲۵,۰۰۰,۰۰۰</span><span>حباب 100</span>'
        quotes=extract(html,src,NOW.isoformat())
        self.assertEqual(quotes[0].price,25000000)
        self.assertIsNone(quotes[0].published_at)

    def test_taline_day_only_and_article_timestamp_are_not_price_time(self):
        src=source('taline',['parsian_1000mg']);src['url']='https://taline.ir/goldprice/parsian/'
        html=f'<meta property="og:updated_time" content="{NOW.isoformat()}"><p>قیمت سکه پارسیان 1گرمی: ۲۷,۰۰۰,۰۰۰</p><p>قیمت امروز ۱۹ آبان ۱۴۰۵ تومان</p>'
        quotes=extract(html,src,NOW.isoformat())
        self.assertEqual(quotes[0].price,27000000)
        self.assertEqual(quotes[0].kind,'retail_unspecified_tax')
        self.assertIsNone(quotes[0].published_at)

    def test_chartix_exact_ticker_ignores_bubbles_and_other_exchanges(self):
        src=source('chartix_snapshot',['gold_18k'])
        data=[{'ticker':1,'last_price':2,'exchange':3},'GOLD18TABLO',25000000,'IRAN',
              {'ticker':5,'last_price':2,'exchange':3},'BUBBLE',
              {'ticker':1,'last_price':2,'exchange':7},'OTHER']
        quotes=extract('<script id="__NUXT_DATA__">'+json.dumps(data)+'</script>',src,NOW.isoformat())
        self.assertEqual([(q.asset,q.price,q.published_at) for q in quotes],[('gold_18k',25000000,None)])

    def test_unverified_sources_remain_disabled_and_same_publisher_is_grouped(self):
        config=load_config('sources.json');sources={s['id']:s for s in config['sources']}
        for sid in ['taline_gold','taline_parsian','wallex_gold','chartix_gold','alanchand','tabangohar_gold','tala_coin_emami']:
            self.assertFalse(sources[sid]['enabled'])
            self.assertFalse(sources[sid]['reference_eligible'])
            self.assertTrue(sources[sid]['disabled_reason'])
        self.assertEqual(sources['tgju_web']['group'],sources['telegram_tgjugold']['group'])
        self.assertEqual(sources['tabangohar_parsian']['group'],sources['telegram_talaclinic1']['group'])


if __name__=='__main__':unittest.main()
