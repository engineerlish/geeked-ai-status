import http.server
import threading
import unittest

from collector.http import FetchError, get_json, get_text


class Handler(http.server.BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def do_GET(self):  # noqa: N802 - http.server API
        Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
        if self.path == "/ok":
            body, code = b'{"fine": true}', 200
        elif self.path == "/text":
            body, code = b"plain", 200
        elif self.path == "/missing":
            body, code = b"nope", 404
        elif self.path == "/flaky":
            body, code = (b"{}", 200) if Handler.hits[self.path] > 1 else (b"busy", 503)
        elif self.path == "/bad-json":
            body, code = b"<html>", 200
        else:
            body, code = b"?", 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.hits.clear()

    def test_reads_json_and_text(self):
        self.assertEqual(get_json(f"{self.base}/ok"), {"fine": True})
        self.assertEqual(get_text(f"{self.base}/text"), "plain")

    def test_client_errors_fail_at_once_without_retrying(self):
        with self.assertRaises(FetchError) as caught:
            get_json(f"{self.base}/missing")
        self.assertIn("HTTP 404", str(caught.exception))
        self.assertEqual(Handler.hits["/missing"], 1)

    def test_server_errors_are_retried(self):
        # Make the retry back-off instant for the test.
        import collector.http as module

        original = module.time.sleep
        module.time.sleep = lambda _s: None
        try:
            self.assertEqual(get_json(f"{self.base}/flaky"), {})
            self.assertEqual(Handler.hits["/flaky"], 2)
            with self.assertRaises(FetchError):
                get_json(f"{self.base}/always-500")
            self.assertEqual(Handler.hits["/always-500"], 3)
        finally:
            module.time.sleep = original

    def test_invalid_json_is_a_fetch_error_and_never_echoes_the_query(self):
        with self.assertRaises(FetchError) as caught:
            get_json(f"{self.base}/bad-json?key=SECRET")
        self.assertNotIn("SECRET", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
