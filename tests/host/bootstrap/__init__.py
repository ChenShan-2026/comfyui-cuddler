"""Host-test bootstrap (tests only, never shipped): points the Cuddler plugin at a fake Platform.

Install it next to the plugin as custom_nodes/zz_cuddler_host_test. ComfyUI does
not load custom nodes in a fixed order, so the fake is installed when the web
app starts, after every custom node has loaded.
"""
import json
import os
from pathlib import Path
import sys

from server import PromptServer

NODE_CLASS_MAPPINGS = {}
API = "https://api.cuddler.ai/v1"
BALANCE = {"object": "balance", "currency": "usd", "total_usd": "12.340000", "held_usd": "0.000000",
           "available_usd": "12.340000", "daily_limit_usd": "300.000000", "spent_today_usd": "0.000000",
           "expires_at": None}
ESTIMATE = {"object": "estimate", "usd": "0.653877", "hold_usd": "0.666955", "price_version": "fake"}


def _plugin():
    suffix = os.path.join("custom_nodes", "cuddler", "__init__.py")
    for name, module in list(sys.modules.items()):
        if (getattr(module, "__file__", None) or "").endswith(suffix):
            return name, module
    raise RuntimeError("the cuddler plugin is not loaded")


class FakePlatform:
    def __init__(self, response_class, catalog):
        self._response = response_class
        self._catalog = catalog

    def _json(self, status, body):
        return self._response(status, {"content-type": "application/json"}, json.dumps(body).encode())

    def send(self, request):
        path = request.url[len(API):] if request.url.startswith(API) else request.url
        if (request.method, path) == ("GET", "/catalog"):
            return self._json(200, self._catalog)
        if not request.headers.get("Authorization", "").startswith("Bearer cud_sk_"):
            return self._json(401, {"code": "unauthorized", "message": "bad key", "request_id": "req_fake"})
        if (request.method, path) == ("GET", "/balance"):
            return self._json(200, BALANCE)
        if (request.method, path) == ("POST", "/videos/estimate"):
            return self._json(200, ESTIMATE)
        return self._json(404, {"code": "not_found", "message": "not faked", "request_id": "req_fake"})

    def stream(self, request, sink):
        return self.send(request)


async def _install(app):
    name, module = _plugin()
    root = Path(module.__file__).resolve().parent
    catalog = json.loads((root / "data" / "catalog_snapshot.json").read_text(encoding="utf-8"))
    fake = FakePlatform(sys.modules[name + ".cuddler.transport"].Response, catalog)
    sys.modules[name + ".cuddler_host.runtime"].configure_transport(fake)
    print("cuddler host test: fake Platform installed", flush=True)


PromptServer.instance.app.on_startup.append(_install)
