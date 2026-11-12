import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from marketfeed.__main__ import read_source


class ProxyCollectorTests(unittest.TestCase):
    source = {'id':'ramzinex_usdt','url':'https://publicapi.ramzinex.com/test','adapter':'ramzinex_orderbook'}

    def test_only_403_uses_proxy_and_preserves_raw_body(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"data":{}}'
        response.status = 200
        opener = MagicMock()
        opener.open.return_value = response
        with patch.dict(os.environ, {'MARKET_PROXY_URL':'https://private.test','MARKET_PROXY_SECRET':'secret'}), \
             patch('marketfeed.__main__.read_https', side_effect=HTTPError(self.source['url'],403,'Forbidden',{},None)), \
             patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(read_source(self.source,15),(b'{"data":{}}',200))
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url,'https://private.test/ramzinex_usdt')
        self.assertEqual(req.get_header('X-market-secret'),'secret')
        self.assertTrue(req.get_header('User-agent').startswith('IranMarketFeed/'))

    def test_429_is_not_routed_through_proxy(self):
        with patch('marketfeed.__main__.read_https', side_effect=HTTPError(self.source['url'],429,'Limited',{},None)), \
             patch('urllib.request.build_opener') as opener:
            with self.assertRaises(HTTPError):read_source(self.source,15)
            opener.assert_not_called()

    def test_proxy_origin_cannot_contain_credentials_or_query(self):
        for origin in ('http://private.test','https://user:pass@private.test','https://private.test?secret=x','https://private.test/path'):
            with self.subTest(origin=origin), \
                 patch.dict(os.environ, {'MARKET_PROXY_URL':origin,'MARKET_PROXY_SECRET':'secret'}), \
                 patch('marketfeed.__main__.read_https', side_effect=HTTPError(self.source['url'],403,'Forbidden',{},None)):
                with self.assertRaises(ValueError):read_source(self.source,15)
