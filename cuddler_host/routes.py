"""HTTP routes behind the plugin's settings page and price labels.

They read and change the user's API key, so they answer only pages served by this ComfyUI:

* a request the browser marks cross-site or same-site (Sec-Fetch-Site) is refused;
* a request carrying an Origin must carry this server's own (Origin == Host);
* the Host must be a name no outside web page can point at this machine: localhost, an IP address, a
  single-label name or a private-use suffix (.local, .lan, .internal, ...), or a name listed in
  CUDDLER_ALLOWED_HOSTS (a domain name or tunnel in front of ComfyUI). Without this check a page at
  evil.example whose DNS answer is switched to 127.0.0.1 ("DNS rebinding") is same-origin for the browser
  and passes the first two checks.

A request with none of those headers (a local script) is allowed, as it is by ComfyUI itself: whoever can
reach the port can already queue paid jobs, so these checks guard the browser path, not the network.
"""
import asyncio
import ipaddress
import json
import os
import re
import time
from urllib.parse import urlsplit

from aiohttp import web

from ..cuddler.client import JOB_PATHS
from ..cuddler.errors import CuddlerError
from ..cuddler.fingerprint import canonical, digest_bytes
from ..cuddler.i18n import describe
from ..cuddler.money import display

MAX_BODY = 64 * 1024
ESTIMATE_TTL = 60.0     # seconds a price stays cached
ESTIMATE_RATE = 4       # Platform estimate calls per second, for the whole process
MAX_IN_FLIGHT = 4       # Platform calls waiting for an answer at once; each holds a worker thread
BALANCE_TTL = 5.0       # seconds a balance stays cached
CACHE_SIZE = 256
BALANCE_FIELDS = ("available_usd", "held_usd", "daily_limit_usd", "spent_today_usd")
ALLOWED_HOSTS_ENV = "CUDDLER_ALLOWED_HOSTS"
PRIVATE_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa")
_AUTHORITY = re.compile(r"(?:\[(?P<ip6>[0-9a-f:.]+)\]|(?P<name>[a-z0-9._-]+))(?::[0-9]{1,5})?")


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def parse_allowed_hosts(text):
    """The names in CUDDLER_ALLOWED_HOSTS (comma separated). `example.com` is that name, `.example.com` or
    `*.example.com` is every name below it, `*` is every name."""
    entries = []
    for item in (text or "").split(","):
        item = item.strip().lower()
        if item.startswith("*."):
            item = item[1:]
        if item:
            entries.append(item)
    return tuple(entries)


def host_allowed(host, listed=()):
    """Is this Host header value a name of this machine that no outside page can claim? (`listed`: names the
    operator added.) Anything that is not a plain host[:port] is refused."""
    match = _AUTHORITY.fullmatch(host.lower())
    if match is None:
        return False
    if match.group("ip6") is not None:
        return _is_ip(match.group("ip6"))
    name = match.group("name").rstrip(".")
    if not name:
        return False
    if _is_ip(name) or "." not in name or name.endswith(PRIVATE_SUFFIXES):
        return True
    return any(entry == "*" or name == entry or (entry.startswith(".") and name.endswith(entry))
               for entry in listed)


def same_origin(request, listed=()):
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site not in ("same-origin", "none"):
        return False
    host = request.headers.get("Host", "").lower()
    if host and not host_allowed(host, listed):
        return False
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # not a browser page: a local script
    try:
        netloc = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    return bool(host) and netloc == host


async def read_json(request):
    """The JSON object in the body, or None for anything else: another content type, a body over MAX_BODY,
    bad JSON, or JSON that is not an object. The body is read to its end: one `read(n)` returns only what
    has arrived so far, which would cut a body that comes in several pieces."""
    if request.content_type != "application/json":
        return None
    if request.content_length is not None and request.content_length > MAX_BODY:
        return None
    raw = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        raw += chunk
        if len(raw) > MAX_BODY:
            return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):  # RecursionError: nested thousands deep
        return None
    return value if isinstance(value, dict) else None


def _status_for(error):
    if error.code == "network_error":
        return 502
    if error.status and 400 <= error.status < 600:
        return error.status
    return 400


class Handlers:
    def __init__(self, runtime, clock=time.monotonic, environ=None):
        self._runtime = runtime  # a callable returning the Runtime
        self._clock = clock
        self._listed = parse_allowed_hosts((os.environ if environ is None else environ).get(ALLOWED_HOSTS_ENV))
        self._cache = {}        # estimate digest -> (expires, answer)
        self._recent = []       # when the estimate calls of the last second started
        self._balance = None    # (expires, answer)
        self._in_flight = 0
        self._generation = 0    # changes with the key: an answer fetched under an older key is not kept

    def _language(self):
        """The language for an error message. The runtime itself may be what failed (an unusable user
        folder); then English is better than a second exception."""
        try:
            return self._runtime().language()
        except CuddlerError:
            return "en"

    def _fail(self, error, status=None):
        message = describe(error, self._language())
        return web.json_response({"code": error.code, "message": message},
                                 status=status or _status_for(error))

    def _forbidden(self):
        return self._fail(CuddlerError("forbidden_origin"), 403)

    def _key_changed(self):
        self._generation += 1
        self._cache.clear()
        self._balance = None

    async def _platform(self, call):
        """Run a blocking Platform call in a worker thread, with at most MAX_IN_FLIGHT waiting at once: a
        Platform that does not answer must not use up the threads ComfyUI shares."""
        if self._in_flight >= MAX_IN_FLIGHT:
            raise CuddlerError("rate_limited", status=429)
        self._in_flight += 1
        try:
            return await asyncio.to_thread(call)
        finally:
            self._in_flight -= 1

    async def _balance_for(self, runtime, language):
        if self._balance and self._balance[0] > self._clock():
            return {"balance": self._balance[1]}
        generation = self._generation
        try:
            balance = await self._platform(lambda: runtime.client().balance())
        except CuddlerError as error:
            return {"balance_error": {"code": error.code, "message": describe(error, language)}}
        shown = {name: balance[name] for name in BALANCE_FIELDS}
        shown["available_display"] = display(balance["available_usd"], down=True)  # never more than there is
        if generation == self._generation:
            self._balance = (self._clock() + BALANCE_TTL, shown)
        return {"balance": shown}

    async def status(self, request):
        if not same_origin(request, self._listed):
            return self._forbidden()
        try:
            runtime = self._runtime()
        except CuddlerError as error:
            return self._fail(error)
        language = runtime.language()
        key = runtime.keys.status()
        if key["error"]:
            key["message"] = describe(CuddlerError(key["error"]), language)
        catalog = runtime.catalog.current()
        body = {"key": key, "language": language,
                "catalog": {"source": catalog.source, "models": len(catalog.models)}}
        if request.query.get("balance") == "1" and key["mode"] is not None:
            body.update(await self._balance_for(runtime, language))
        return web.json_response(body)

    async def save_key(self, request):
        if not same_origin(request, self._listed):
            return self._forbidden()
        payload = await read_json(request)
        try:
            key = self._runtime().keys.save(payload.get("key") if payload else None)
        except CuddlerError as error:
            return self._fail(error)
        self._key_changed()
        return web.json_response({"key": key})

    async def clear_key(self, request):
        if not same_origin(request, self._listed):
            return self._forbidden()
        try:
            key = self._runtime().keys.clear()
        except CuddlerError as error:
            return self._fail(error)
        self._key_changed()
        return web.json_response({"key": key})

    async def estimate(self, request):
        if not same_origin(request, self._listed):
            return self._forbidden()
        payload = await read_json(request)
        kind = payload.get("kind") if payload else None
        body = payload.get("body") if payload else None
        if not isinstance(kind, str) or kind not in JOB_PATHS or not isinstance(body, dict):
            return self._fail(CuddlerError("invalid_request"), 400)
        try:
            runtime = self._runtime()
        except CuddlerError as error:
            return self._fail(error)
        try:
            cache_key = digest_bytes(canonical([kind, body]))
        except CuddlerError as error:
            return self._fail(error, 400)
        now = self._clock()
        hit = self._cache.get(cache_key)
        if hit and hit[0] > now:
            return web.json_response(hit[1])
        self._recent = [moment for moment in self._recent if now - moment < 1.0]
        if len(self._recent) >= ESTIMATE_RATE or self._in_flight >= MAX_IN_FLIGHT:
            return self._fail(CuddlerError("rate_limited"), 429)
        self._recent.append(now)
        generation = self._generation
        try:
            quote = await self._platform(lambda: runtime.client().estimate(kind, body))
        except CuddlerError as error:
            return self._fail(error)
        result = {"usd": quote["usd"], "hold_usd": quote["hold_usd"], "display": display(quote["usd"])}
        if generation == self._generation:
            if len(self._cache) >= CACHE_SIZE:
                self._cache.clear()
            self._cache[cache_key] = (now + ESTIMATE_TTL, result)
        return web.json_response(result)


def install(route_table, runtime, environ=None):
    """Register the routes on ComfyUI's route table (PromptServer.instance.routes)."""
    handlers = Handlers(runtime, environ=environ)
    route_table.get("/cuddler/status")(handlers.status)
    route_table.post("/cuddler/key")(handlers.save_key)
    route_table.delete("/cuddler/key")(handlers.clear_key)
    route_table.post("/cuddler/estimate")(handlers.estimate)
    return handlers
