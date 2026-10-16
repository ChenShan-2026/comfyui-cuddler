import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler_host.routes import (
    BALANCE_TTL, ESTIMATE_RATE, MAX_BODY, MAX_IN_FLIGHT, host_allowed, install, parse_allowed_hosts)
from cuddler_plugin.cuddler_host.runtime import Runtime
from tests import fakes

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catalog.json"
ESTIMATE = {"kind": "video", "body": {"model": "seedance-2.5", "duration": 4, "resolution": "720p"}}


class RoutesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transport = fakes.FakeTransport()
        self.runtime = Runtime(Path(self.tmp.name) / "__cuddler", settings_path=Path(self.tmp.name) / "s.json",
                               snapshot_path=FIXTURE, transport=self.transport, environ={})
        routes = web.RouteTableDef()
        self.now = 100.0
        self.handlers = install(routes, lambda: self.runtime)
        self.handlers._clock = lambda: self.now
        app = web.Application()
        app.add_routes(routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def browser(self, origin=None, site="same-origin"):
        return {"Origin": origin or self.origin, "Sec-Fetch-Site": site}

    async def test_status_starts_with_no_key_and_the_snapshot_catalog(self):
        response = await self.client.get("/cuddler/status", headers=self.browser())
        body = await response.json()
        self.assertEqual(body["key"]["source"], "none")
        self.assertEqual(body["catalog"]["source"], "snapshot")
        self.assertGreater(body["catalog"]["models"], 0)

    async def test_saving_a_key_returns_only_its_last_four_characters(self):
        response = await self.client.post("/cuddler/key", json={"key": fakes.LIVE_KEY}, headers=self.browser())
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertNotIn(fakes.LIVE_KEY, text)
        self.assertEqual(json.loads(text)["key"]["last4"], fakes.LIVE_KEY[-4:])
        self.assertEqual(self.runtime.keys.resolve(), fakes.LIVE_KEY)

    async def test_bad_keys_get_a_readable_message(self):
        response = await self.client.post("/cuddler/key", json={"key": "nope"}, headers=self.browser())
        self.assertEqual(response.status, 400)
        body = await response.json()
        self.assertEqual(body["code"], "key_invalid_format")
        self.assertIn("cud_sk_live_", body["message"])

    async def test_an_unusable_saved_key_is_explained(self):
        (Path(self.tmp.name) / "__cuddler" / "credentials.json").write_text("{broken", encoding="utf-8")
        response = await self.client.get("/cuddler/status", headers=self.browser())
        key = (await response.json())["key"]
        self.assertEqual(key["error"], "key_unreadable")
        self.assertIn("Settings", key["message"])

    async def test_clearing_the_key(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        response = await self.client.delete("/cuddler/key", headers=self.browser())
        self.assertEqual((await response.json())["key"]["source"], "none")

    async def test_status_can_include_the_balance(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        self.transport.add("GET", "/balance", body=fakes.balance("12.340000"))
        response = await self.client.get("/cuddler/status?balance=1", headers=self.browser())
        body = await response.json()
        self.assertEqual(body["balance"]["available_display"], "$12.34")

    async def test_balance_errors_are_explained_not_fatal(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        self.transport.add("GET", "/balance", status=401, body=fakes.error("unauthorized", 401))
        response = await self.client.get("/cuddler/status?balance=1", headers=self.browser())
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["balance_error"]["code"], "unauthorized")

    async def test_estimates_are_cached(self):
        self.runtime.keys.save(fakes.TEST_KEY)
        self.transport.add("POST", "/videos/estimate", body=fakes.estimate())
        for _ in range(3):
            response = await self.client.post("/cuddler/estimate", json=ESTIMATE, headers=self.browser())
            self.assertEqual((await response.json())["display"], "$0.66")
        self.assertEqual(len(self.transport.calls("POST", "/videos/estimate")), 1)

    async def test_estimates_are_rate_limited(self):
        self.runtime.keys.save(fakes.TEST_KEY)
        self.transport.add("POST", "/videos/estimate", body=fakes.estimate())
        statuses = []
        for duration in range(ESTIMATE_RATE + 1):
            payload = {"kind": "video", "body": dict(ESTIMATE["body"], duration=4 + duration)}
            response = await self.client.post("/cuddler/estimate", json=payload, headers=self.browser())
            statuses.append(response.status)
        self.assertEqual(statuses, [200] * ESTIMATE_RATE + [429])

    async def test_estimate_without_a_key_explains_what_to_do(self):
        response = await self.client.post("/cuddler/estimate", json=ESTIMATE, headers=self.browser())
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "key_missing")

    async def test_estimate_refuses_unknown_kinds(self):
        response = await self.client.post("/cuddler/estimate", json={"kind": "hologram", "body": {}},
                                          headers=self.browser())
        self.assertEqual(response.status, 400)

    async def test_other_sites_are_refused(self):
        attempts = [
            ("post", "/cuddler/key", {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"}),
            ("post", "/cuddler/key", {"Origin": "http://evil.example"}),
            ("get", "/cuddler/status", {"Sec-Fetch-Site": "cross-site"}),
            ("post", "/cuddler/estimate", {"Origin": "null"}),
        ]
        for method, path, headers in attempts:
            with self.subTest(path=path, headers=headers):
                response = await getattr(self.client, method)(path, json={"key": fakes.LIVE_KEY}, headers=headers)
                self.assertEqual(response.status, 403)
        self.assertIsNone(self.runtime.keys.resolve())

    async def test_local_scripts_without_browser_headers_are_allowed(self):
        response = await self.client.get("/cuddler/status")
        self.assertEqual(response.status, 200)

    async def test_non_json_bodies_are_refused(self):
        response = await self.client.post("/cuddler/key", data="key=" + fakes.LIVE_KEY, headers=self.browser())
        self.assertEqual(response.status, 400)

    def rebound(self):
        """A browser page at evil.example whose DNS now points at this server: same-origin for the browser."""
        host = f"evil.example:{self.client.port}"
        return {"Host": host, "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"}

    async def wait_for(self, condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(0.01)
        self.fail("the condition never came true")

    async def test_a_page_rebound_to_this_machine_is_refused(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        for method, path, payload in (("get", "/cuddler/status?balance=1", None),
                                      ("post", "/cuddler/key", {"key": fakes.TEST_KEY}),
                                      ("delete", "/cuddler/key", None),
                                      ("post", "/cuddler/estimate", ESTIMATE)):
            with self.subTest(method=method, path=path):
                response = await getattr(self.client, method)(path, json=payload, headers=self.rebound())
                self.assertEqual(response.status, 403)
                self.assertEqual((await response.json())["code"], "forbidden_origin")
        self.assertEqual(self.runtime.keys.resolve(), fakes.LIVE_KEY)
        self.assertEqual(self.transport.requests, [])

    async def test_the_names_people_open_comfyui_by_are_allowed(self):
        port = self.client.port
        for host in (f"localhost:{port}", f"LOCALHOST.:{port}", f"127.0.0.1:{port}", f"[::1]:{port}",
                     f"192.168.1.20:{port}", f"gpu-box:{port}", f"gpu-box.local:{port}", f"studio.lan:{port}",
                     f"a.b.localhost:{port}", "[fd00::1]"):
            with self.subTest(host=host):
                headers = {"Host": host, "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"}
                response = await self.client.get("/cuddler/status", headers=headers)
                self.assertEqual(response.status, 200)

    async def test_a_refused_page_is_told_why_in_the_comfyui_language(self):
        evil = {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"}
        response = await self.client.post("/cuddler/key", json={"key": fakes.LIVE_KEY}, headers=evil)
        body = await response.json()
        self.assertEqual((response.status, body["code"]), (403, "forbidden_origin"))
        self.assertIn("CUDDLER_ALLOWED_HOSTS", body["message"])
        (Path(self.tmp.name) / "s.json").write_text(json.dumps({"Comfy.Locale": "zh"}), encoding="utf-8")
        response = await self.client.post("/cuddler/key", json={"key": fakes.LIVE_KEY}, headers=evil)
        self.assertIn("环境变量", (await response.json())["message"])

    async def test_estimate_needs_a_text_kind_and_an_object_body(self):
        for payload in ({"kind": ["video"], "body": {}}, {"kind": {"a": 1}, "body": {}}, {"kind": 5, "body": {}},
                        {"kind": "video", "body": []}, {"kind": "video"}, [ESTIMATE]):
            with self.subTest(payload=payload):
                response = await self.client.post("/cuddler/estimate", json=payload, headers=self.browser())
                self.assertEqual(response.status, 400)
                self.assertEqual((await response.json())["code"], "invalid_request")

    async def test_absurdly_nested_json_is_just_invalid(self):
        raw = ('{"key":' + "[" * 30000 + "]" * 30000 + "}").encode("ascii")
        self.assertLess(len(raw), MAX_BODY)
        headers = {**self.browser(), "Content-Type": "application/json"}
        response = await self.client.post("/cuddler/key", data=raw, headers=headers)
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "key_invalid_format")

    async def test_a_body_that_arrives_in_pieces_is_read_whole(self):
        self.runtime.keys.save(fakes.TEST_KEY)
        self.transport.add("POST", "/videos/estimate", body=fakes.estimate())
        raw = json.dumps({"kind": "video", "body": dict(ESTIMATE["body"], prompt="x" * 30000)}).encode("utf-8")

        async def pieces():
            yield raw[:9000]
            await asyncio.sleep(0.1)
            yield raw[9000:]

        headers = {**self.browser(), "Content-Type": "application/json"}
        response = await self.client.post("/cuddler/estimate", data=pieces(), headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["display"], "$0.66")

    async def test_bodies_over_the_limit_are_refused(self):
        headers = {**self.browser(), "Content-Type": "application/json"}

        async def endless():
            for _ in range(MAX_BODY // 4096 + 4):
                yield b" " * 4096

        declared = await self.client.post("/cuddler/key", data=b" " * (MAX_BODY + 1), headers=headers)
        chunked = await self.client.post("/cuddler/key", data=endless(), headers=headers)
        self.assertEqual((declared.status, chunked.status), (400, 400))

    async def test_the_balance_is_cached_briefly_and_forgotten_with_the_key(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        self.transport.add("GET", "/balance", body=fakes.balance("12.340000"))
        for _ in range(3):
            response = await self.client.get("/cuddler/status?balance=1", headers=self.browser())
            self.assertEqual((await response.json())["balance"]["available_display"], "$12.34")
        self.assertEqual(len(self.transport.calls("GET", "/balance")), 1)
        self.now += BALANCE_TTL + 1
        await self.client.get("/cuddler/status?balance=1", headers=self.browser())
        self.assertEqual(len(self.transport.calls("GET", "/balance")), 2)
        await self.client.post("/cuddler/key", json={"key": fakes.TEST_KEY}, headers=self.browser())
        await self.client.get("/cuddler/status?balance=1", headers=self.browser())
        self.assertEqual(len(self.transport.calls("GET", "/balance")), 3)

    async def test_balance_errors_are_not_cached(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        self.transport.add("GET", "/balance", status=401, body=fakes.error("unauthorized", 401))
        for _ in range(2):
            response = await self.client.get("/cuddler/status?balance=1", headers=self.browser())
            self.assertEqual((await response.json())["balance_error"]["code"], "unauthorized")
        self.assertEqual(len(self.transport.calls("GET", "/balance")), 2)

    async def test_a_price_found_under_the_old_key_is_not_kept_for_the_new_one(self):
        gate = GatedTransport()
        self.runtime.transport = gate
        gate.add("POST", "/videos/estimate", body=fakes.estimate())
        self.runtime.keys.save(fakes.TEST_KEY)
        first = asyncio.ensure_future(self.client.post("/cuddler/estimate", json=ESTIMATE, headers=self.browser()))
        await self.wait_for(lambda: gate.entered == 1)
        await self.client.post("/cuddler/key", json={"key": fakes.LIVE_KEY}, headers=self.browser())
        gate.release.set()
        self.assertEqual((await first).status, 200)
        again = await self.client.post("/cuddler/estimate", json=ESTIMATE, headers=self.browser())
        self.assertEqual(again.status, 200)
        self.assertEqual(len(gate.calls("POST", "/videos/estimate")), 2)

    async def test_a_stalled_platform_cannot_tie_up_every_worker_thread(self):
        gate = GatedTransport()
        self.runtime.transport = gate
        gate.add("POST", "/videos/estimate", body=fakes.estimate())
        self.runtime.keys.save(fakes.TEST_KEY)

        def priced(duration):
            return {"kind": "video", "body": dict(ESTIMATE["body"], duration=duration)}

        waiting = []
        for index in range(MAX_IN_FLIGHT):
            waiting.append(asyncio.ensure_future(
                self.client.post("/cuddler/estimate", json=priced(4 + index), headers=self.browser())))
            await self.wait_for(lambda: gate.entered == index + 1)
            self.now += 2.0  # keeps the per-second limit out of the way: only the waiting calls count
        refused = await self.client.post("/cuddler/estimate", json=priced(99), headers=self.browser())
        self.assertEqual(refused.status, 429)
        self.assertEqual(gate.entered, MAX_IN_FLIGHT)
        gate.release.set()
        for task in waiting:
            self.assertEqual((await task).status, 200)
        recovered = await self.client.post("/cuddler/estimate", json=priced(98), headers=self.browser())
        self.assertEqual(recovered.status, 200)


def broken_runtime():
    raise CuddlerError("state_unavailable", status=500, detail={"reason": "Permission denied"})


class UnusableFolderTest(unittest.IsolatedAsyncioTestCase):
    """When the runtime cannot start (an unusable user folder), the settings calls still explain it as JSON."""

    async def asyncSetUp(self):
        routes = web.RouteTableDef()
        install(routes, broken_runtime)
        app = web.Application()
        app.add_routes(routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {"Origin": f"http://{self.client.host}:{self.client.port}", "Sec-Fetch-Site": "same-origin"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_every_settings_call_explains_an_unusable_folder(self):
        for method, path, payload in (("get", "/cuddler/status", None), ("post", "/cuddler/key", {"key": "x"}),
                                      ("delete", "/cuddler/key", None), ("post", "/cuddler/estimate", ESTIMATE)):
            with self.subTest(method=method, path=path):
                response = await getattr(self.client, method)(path, json=payload, headers=self.headers)
                self.assertEqual(response.status, 500)
                body = await response.json()
                self.assertEqual(body["code"], "state_unavailable")
                self.assertIn("Permission denied", body["message"])


class GatedTransport(fakes.FakeTransport):
    """Holds every Platform call until `release` is set, so a test can act while calls are in flight."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.entered = 0

    def send(self, request):
        self.entered += 1
        self.release.wait(5)
        return super().send(request)


class HostAllowedTest(unittest.TestCase):
    def test_names_that_no_outside_page_can_point_at_this_machine(self):
        for host in ("localhost", "localhost:8188", "LOCALHOST.:8188", "127.0.0.1", "127.0.0.1:8188", "[::1]",
                     "[::1]:8188", "192.168.1.20:8188", "10.0.0.5", "gpu-box", "gpu-box:8188",
                     "gpu-box.local:8188", "studio.lan", "ip-10-0-0-1.ec2.internal", "a.b.localhost",
                     "printer.home.arpa"):
            with self.subTest(host=host):
                self.assertTrue(host_allowed(host))

    def test_public_names_and_malformed_hosts_are_not(self):
        for host in ("evil.example", "evil.example:8188", "evil.example.", "localhost.evil.example",
                     "127.0.0.1.evil.example", "", ":8188", "[:::]", "[::g]", "x@evil.example", "evil.example/",
                     "evil example", "evil.example:8188:1", "evil.example:123456", "localhost:"):
            with self.subTest(host=host):
                self.assertFalse(host_allowed(host))

    def test_names_the_operator_lists(self):
        listed = parse_allowed_hosts("Comfy.Example.com, .tunnel.example.net ,*.pods.example.org,")
        self.assertEqual(listed, ("comfy.example.com", ".tunnel.example.net", ".pods.example.org"))
        for host in ("comfy.example.com", "COMFY.example.com:443", "a.tunnel.example.net",
                     "x.y.pods.example.org:8188"):
            with self.subTest(host=host):
                self.assertTrue(host_allowed(host, listed))
        for host in ("tunnel.example.net", "evil.example", "comfy.example.com.evil.example",
                     "notcomfy.example.com"):
            with self.subTest(host=host):
                self.assertFalse(host_allowed(host, listed))
        self.assertTrue(host_allowed("anything.example", parse_allowed_hosts("*")))


class ListedHostsTest(unittest.IsolatedAsyncioTestCase):
    async def test_names_listed_in_the_environment_may_call_the_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp) / "__cuddler", settings_path=Path(tmp) / "s.json", snapshot_path=FIXTURE,
                              transport=fakes.FakeTransport(), environ={})
            routes = web.RouteTableDef()
            install(routes, lambda: runtime,
                    environ={"CUDDLER_ALLOWED_HOSTS": "comfy.example.com, .tunnel.example.net"})
            app = web.Application()
            app.add_routes(routes)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                for host, expected in (("comfy.example.com", 200), ("a.tunnel.example.net", 200),
                                       ("tunnel.example.net", 403), ("evil.example", 403)):
                    with self.subTest(host=host):
                        headers = {"Host": host, "Origin": f"https://{host}", "Sec-Fetch-Site": "same-origin"}
                        response = await client.get("/cuddler/status", headers=headers)
                        self.assertEqual(response.status, expected)
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()
