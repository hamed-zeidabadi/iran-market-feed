from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from urllib.error import HTTPError

from marketfeed.__main__ import read_digikala


class ChallengeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/cross':
            self.send_response(307)
            self.send_header('Location', 'http://example.org/other')
            self.end_headers()
        elif 'challenge=ok' not in self.headers.get('Cookie', ''):
            self.send_response(307)
            self.send_header('Set-Cookie', 'challenge=ok; Path=/')
            self.send_header('Location', self.path)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"gold18":{"price":247015},"silver999":{"price":4278}}')

    def log_message(self, *args):
        pass


class DigikalaAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), ChallengeHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_cookie_challenge_retries_same_origin(self):
        body, status = read_digikala(self.base + '/prices', 5)
        self.assertEqual(status, 200)
        self.assertIn(b'gold18', body)

    def test_cross_origin_redirect_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            read_digikala(self.base + '/cross', 5)
        self.assertEqual(error.exception.code, 307)


if __name__ == '__main__':
    unittest.main()
