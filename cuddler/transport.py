"""HTTP with bounded bodies and no redirects.

Honours the system proxy settings: many ComfyUI users can only reach the
internet through one.
"""
from dataclasses import dataclass, field
import http.client
import re
from urllib import request as urllib_request
from urllib.error import HTTPError
from urllib.parse import urlsplit

from .errors import CuddlerError

# Signed upload and download links point at the Platform's storage.
MEDIA_HOST_SUFFIXES = (".r2.cloudflarestorage.com", ".cuddler.ai")
_CHUNK = 64 * 1024
# A public DNS name: letters, digits and hyphens, at least two labels, and a last label that starts with a
# letter. That refuses IP literals in every spelling (127.1, 2130706433, 0x7f.1), trailing dots, percent
# escapes and look-alike Unicode (the OS resolves "ｌｏｃａｌｈｏｓｔ" and "127。0。0。1" to loopback).
_PUBLIC_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
# Never sent over plain http.
_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


@dataclass
class Request:
    method: str
    url: str = field(repr=False)
    headers: dict = field(default_factory=dict, repr=False)
    body: bytes = field(default=None, repr=False)
    limit: int = 1024 * 1024
    timeout: float = 30.0


@dataclass
class Response:
    status: int
    headers: dict = field(repr=False)  # lower-case names
    body: bytes = field(repr=False)


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_https_url(url, *, media=False):
    """Refuse anything but a public https URL (and, for media, the Platform's storage hosts)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except (TypeError, ValueError, AttributeError):
        raise CuddlerError("unsafe_url") from None
    # urlsplit silently drops tabs and newlines and urllib decodes %xx in the host, so what is checked
    # must be plain: no whitespace or control characters, and a host that is only letters, digits, dots and hyphens.
    if (parts.scheme != "https" or "@" in parts.netloc or parts.fragment or port not in (None, 443)
            or any(c <= " " or c == "\x7f" for c in url)
            or len(host) > 253 or not _PUBLIC_HOST.fullmatch(host)
            or host.endswith((".local", ".localhost", ".internal"))):
        raise CuddlerError("unsafe_url")
    if media and not host.endswith(MEDIA_HOST_SUFFIXES):
        raise CuddlerError("media_host_not_allowed")
    return url


class HttpTransport:
    def __init__(self, user_agent):
        self.user_agent = user_agent

    def _open(self, request):
        try:
            scheme = urlsplit(request.url).scheme.lower()
        except (TypeError, ValueError, AttributeError):
            raise CuddlerError("unsafe_url") from None
        if scheme not in ("http", "https") or (
                scheme == "http" and any(str(name).lower() in _CREDENTIAL_HEADERS for name in request.headers)):
            raise CuddlerError("unsafe_url")  # no file:/ftp:/data: URLs, and never a credential in clear text
        headers = dict(request.headers)
        headers["User-Agent"] = self.user_agent
        req = urllib_request.Request(request.url, data=request.body, headers=headers, method=request.method)
        try:
            # A fresh opener per request: the system proxy settings are read when it is built, and a
            # ComfyUI that keeps running must follow the user switching a proxy on or off.
            return urllib_request.build_opener(_NoRedirect()).open(req, timeout=request.timeout)
        except HTTPError as error:
            return error  # 3xx (redirects are never followed), 4xx and 5xx carry a body too
        except (OSError, http.client.HTTPException, ValueError):
            raise CuddlerError("network_error") from None

    @staticmethod
    def _status(stream):
        return stream.code if isinstance(stream, HTTPError) else stream.status

    @staticmethod
    def _headers(stream):
        return {name.lower(): value for name, value in (stream.headers or {}).items()}

    @staticmethod
    def _read(stream, size):
        try:
            return stream.read(size)
        except (OSError, http.client.HTTPException, ValueError):
            raise CuddlerError("network_error") from None

    @staticmethod
    def _declared_length(request, stream, status):
        """The Content-Length of a body the server owes us, or None (chunked, HEAD, 1xx/204/304, no header)."""
        headers = stream.headers or {}
        if (request.method.upper() == "HEAD" or status < 200 or status in (204, 304)
                or "chunked" in headers.get("Transfer-Encoding", "").lower()):
            return None
        value = headers.get("Content-Length", "").strip()
        return int(value) if value.isascii() and value.isdigit() else None

    def send(self, request):
        stream = self._open(request)
        status = self._status(stream)
        with stream:
            data, expected = b"", None
            if stream.fp is not None:
                data = self._read(stream, request.limit + 1)
                expected = self._declared_length(request, stream, status)
        if len(data) > request.limit:
            raise CuddlerError("response_too_large")
        if expected is not None and len(data) != expected:
            raise CuddlerError("network_error")  # the connection dropped mid-body; http.client does not notice
        return Response(status, self._headers(stream), data)

    def stream(self, request, sink):
        """GET a large body into `sink` (a binary file) without holding it in memory.

        Only the connection's failures become `network_error`. What `sink.write` raises (disk full, closed
        file) is the caller's own and propagates unchanged. After any error the sink may hold a partial body.
        """
        stream = self._open(request)
        status, total = self._status(stream), 0
        with stream:
            if status != 200:
                return Response(status, self._headers(stream), b"")
            expected = self._declared_length(request, stream, status)
            while True:
                chunk = self._read(stream, _CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > request.limit:
                    raise CuddlerError("response_too_large")
                sink.write(chunk)
        if expected is not None and total != expected:
            raise CuddlerError("network_error")  # cut short: never report a partial file as a download
        return Response(status, self._headers(stream), b"")
