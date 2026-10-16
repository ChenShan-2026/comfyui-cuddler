from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import errno
import io
import os
import socket
import threading
import unittest
from unittest import mock

from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler.transport import HttpTransport, Request, checked_https_url


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/ok":
            self._send(200, b'{"ok":true}', {"X-Request-Id": "req_1"})
        elif self.path == "/big":
            self._send(200, b"x" * 2048)
        elif self.path == "/short":
            # Promises 2048 bytes, sends 1000, then hangs up.
            self.send_response(200)
            self.send_header("Content-Length", "2048")
            self.end_headers()
            self.wfile.write(b"x" * 1000)
        elif self.path == "/redirect":
            self._send(302, b"", {"Location": "/ok"})
        elif self.path == "/error":
            self._send(500, b'{"code":"internal_error"}')
        elif self.path == "/ua":
            self._send(200, self.headers.get("User-Agent", "").encode())
        else:
            self._send(404, b"")

    def _send(self, status, body, headers=None):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ProxyHandler(BaseHTTPRequestHandler):
    """All a proxy sees of an https request is the CONNECT. It records the target and any Authorization."""

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.server.seen.append((self.path, self.headers.get("Authorization")))
        self.send_error(502)


class HttpTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # Local test server: make sure a developer's proxy settings do not intercept it.
        patcher = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transport = HttpTransport("cuddler-test/1")

    def _proxy(self):
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        proxy.seen = []
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        self.addCleanup(proxy.server_close)
        self.addCleanup(proxy.shutdown)
        return proxy

    def _use_proxy(self, proxy):
        url = f"http://127.0.0.1:{proxy.server_address[1]}"
        patcher = mock.patch.dict(os.environ, {"HTTPS_PROXY": url, "https_proxy": url})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_status_lowercase_headers_and_body(self):
        response = self.transport.send(Request("GET", self.base + "/ok"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["x-request-id"], "req_1")
        self.assertEqual(response.body, b'{"ok":true}')

    def test_sets_its_own_user_agent(self):
        response = self.transport.send(Request("GET", self.base + "/ua", {"User-Agent": "spoof"}))
        self.assertEqual(response.body, b"cuddler-test/1")

    def test_never_follows_redirects(self):
        self.assertEqual(self.transport.send(Request("GET", self.base + "/redirect")).status, 302)

    def test_error_statuses_keep_their_body(self):
        response = self.transport.send(Request("GET", self.base + "/error"))
        self.assertEqual((response.status, response.body), (500, b'{"code":"internal_error"}'))

    def test_bounds_the_body(self):
        with self.assertRaises(CuddlerError) as caught:
            self.transport.send(Request("GET", self.base + "/big", limit=1024))
        self.assertEqual(caught.exception.code, "response_too_large")

    def test_stream_writes_to_a_sink_within_the_limit(self):
        sink = io.BytesIO()
        response = self.transport.stream(Request("GET", self.base + "/big", limit=4096), sink)
        self.assertEqual((response.status, len(sink.getvalue())), (200, 2048))
        with self.assertRaises(CuddlerError) as caught:
            self.transport.stream(Request("GET", self.base + "/big", limit=1024), io.BytesIO())
        self.assertEqual(caught.exception.code, "response_too_large")

    def test_unreachable_hosts_are_network_errors(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        with self.assertRaises(CuddlerError) as caught:
            self.transport.send(Request("GET", f"http://127.0.0.1:{closed_port}/", timeout=2))
        self.assertEqual(caught.exception.code, "network_error")

    def test_a_body_cut_short_is_a_network_error_not_a_complete_response(self):
        with self.assertRaises(CuddlerError) as caught:
            self.transport.send(Request("GET", self.base + "/short"))
        self.assertEqual(caught.exception.code, "network_error")
        with self.assertRaises(CuddlerError) as caught:
            self.transport.stream(Request("GET", self.base + "/short", limit=4096), io.BytesIO())
        self.assertEqual(caught.exception.code, "network_error")

    def test_sink_failures_are_not_reported_as_network_errors(self):
        class FullDisk:
            def write(self, chunk):
                raise OSError(errno.ENOSPC, "No space left on device")

        with self.assertRaises(OSError):
            self.transport.stream(Request("GET", self.base + "/big", limit=4096), FullDisk())

    def test_follows_a_proxy_switched_after_construction_and_keeps_the_key_from_it(self):
        first, second = self._proxy(), self._proxy()
        self._use_proxy(first)
        transport = HttpTransport("cuddler-test/1")
        self._use_proxy(second)  # the user switches proxy while ComfyUI keeps running
        with self.assertRaises(CuddlerError):
            transport.send(Request("GET", "https://api.cuddler.ai/v1/balance", {"Authorization": "Bearer key"}))
        self.assertEqual((first.seen, second.seen), ([], [("api.cuddler.ai:443", None)]))

    def test_refuses_other_schemes_and_credentials_in_clear_text(self):
        for url, headers in (("file:///etc/hosts", {}), ("ftp://example.com/x", {}), ("data:text/plain,hi", {}),
                             ("not a url", {}), (self.base + "/ok", {"Authorization": "Bearer key"}),
                             (self.base + "/ok", {"authorization": "Bearer key"})):
            with self.subTest(url=url, headers=headers), self.assertRaises(CuddlerError) as caught:
                self.transport.send(Request("GET", url, headers))
            self.assertEqual(caught.exception.code, "unsafe_url")


class CheckedUrlTest(unittest.TestCase):
    def test_accepts_public_https(self):
        self.assertEqual(checked_https_url("https://api.cuddler.ai/v1/x"), "https://api.cuddler.ai/v1/x")
        url = "https://acct.r2.cloudflarestorage.com/b/k?X-Amz-Signature=1"
        self.assertEqual(checked_https_url(url, media=True), url)

    def test_refuses_unsafe_urls(self):
        for url in ("http://api.cuddler.ai/", "https://user:pw@api.cuddler.ai/", "https://127.0.0.1/",
                    "https://[::1]/", "https://localhost/", "https://printer.local/", "https://api.cuddler.ai:8443/",
                    "https://api.cuddler.ai/#frag", "ftp://x", None):
            with self.subTest(url=url), self.assertRaises(CuddlerError) as caught:
                checked_https_url(url)
            self.assertEqual(caught.exception.code, "unsafe_url")

    def test_refuses_local_hosts_in_disguise(self):
        # The OS resolves every one of these to loopback, or they are read differently by urllib than by the check.
        for url, media in (("https://127.1/", False), ("https://2130706433/", False), ("https://0x7f.1/", False),
                           ("https://127.0.0.1./", False), ("https://localhost./", False),
                           ("https://ｌｏｃａｌｈｏｓｔ/", False), ("https://127。0。0。1/", False),
                           ("https://@api.cuddler.ai/", False), ("https://:@api.cuddler.ai/", False),
                           ("https://x%2f.cuddler.ai/", True), ("https://127.0.0.1\t.cuddler.ai/x", True)):
            with self.subTest(url=url), self.assertRaises(CuddlerError) as caught:
                checked_https_url(url, media=media)
            self.assertEqual(caught.exception.code, "unsafe_url")

    def test_media_must_live_on_platform_storage(self):
        with self.assertRaises(CuddlerError) as caught:
            checked_https_url("https://example.com/x.mp4", media=True)
        self.assertEqual(caught.exception.code, "media_host_not_allowed")
        self.assertTrue(checked_https_url("https://media.cuddler.ai/x.mp4", media=True))


if __name__ == "__main__":
    unittest.main()
