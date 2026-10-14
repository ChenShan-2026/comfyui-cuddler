"""A scripted stand-in for the Platform API, plus sample payloads."""
import json

from cuddler_plugin.cuddler.transport import Response

API = "https://api.cuddler.ai/v1"
LIVE_KEY = "cud_sk_live_" + "a1B2c3D4e5F6g7H8i9J0"
TEST_KEY = "cud_sk_test_" + "Z9y8X7w6V5u4T3s2R1q0"
VIDEO_ID = "vid_" + "A" * 20
IMAGE_ID = "img_" + "I" * 20
SPEECH_ID = "aud_" + "S" * 20
MUSIC_ID = "mus_" + "M" * 20
JOB_IDS = {"video": VIDEO_ID, "image": IMAGE_ID, "speech": SPEECH_ID, "music": MUSIC_ID}
UPLOAD_ID = "upl_" + "U" * 20
PUT_URL = "https://acct.r2.cloudflarestorage.com/cuddler-media-private/in/x?X-Amz-Signature=abc"
OUTPUT_URL = "https://acct.r2.cloudflarestorage.com/cuddler-media-private/out/x.mp4?X-Amz-Signature=def"


class FakeTransport:
    """Answers each (method, url) with queued responses; the last one repeats. Records every request."""

    def __init__(self):
        self.routes = {}
        self.requests = []

    def add(self, method, url, status=200, body=None, headers=None):
        if not url.startswith("https://"):
            url = API + url
        self.routes.setdefault((method, url), []).append((status, body, headers or {}))
        return self

    def fail(self, method, url, error):
        if not url.startswith("https://"):
            url = API + url
        self.routes.setdefault((method, url), []).append(error)
        return self

    def calls(self, method, url):
        if not url.startswith("https://"):
            url = API + url
        return [request for request in self.requests if (request.method, request.url) == (method, url)]

    def send(self, request):
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url))
        if not queue:
            return Response(404, {}, json.dumps({"code": "not_found", "message": "no such route",
                                                 "request_id": "req_test"}).encode())
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body, headers = item
        data = body if isinstance(body, bytes) else (b"" if body is None else json.dumps(body).encode())
        return Response(status, {name.lower(): value for name, value in headers.items()}, data)

    def stream(self, request, sink):
        response = self.send(request)
        if response.status == 200:
            sink.write(response.body)
        return Response(response.status, response.headers, b"")


def estimate(kind="video", usd="0.653877", hold="0.666955"):
    objects = {"video": "estimate", "image": "image_estimate", "speech": "speech_estimate",
               "music": "music_estimate"}
    return {"object": objects[kind], "usd": usd, "hold_usd": hold, "price_version": "2026-10-11-official"}


def video_job(status="queued", job_id=VIDEO_ID, **extra):
    job = {"object": "video", "id": job_id, "status": status, "model": "seedance-2.5",
           "estimate": {"usd": "0.653877", "hold_usd": "0.666955"}, "usage": None, "output": None,
           "error": None, "queue_position": 3 if status == "queued" else None, "metadata": {}}
    if status == "succeeded":
        job["output"] = {"url": OUTPUT_URL, "url_expires_at": "2026-10-14T01:00:00Z",
                         "retained_until": "2026-10-21T00:00:00Z"}
    if status == "failed":
        job["error"] = {"code": "content_policy", "message": "The output was blocked."}
    job.update(extra)
    return job


def job(kind, status="queued", **extra):
    """A job of any kind, shaped like GET /v1/{videos,images,speech,music}/{id} in the 2026-10-13 OpenAPI."""
    if kind == "video":
        return video_job(status, **extra)
    audio = kind in ("speech", "music")
    value = {"object": kind, "id": JOB_IDS[kind], "status": status, "model": "model-1",
             "estimate": ({"meter": "character", "units": 40, "usd": "0.001000", "hold_usd": "0.002000"} if audio
                          else {"usd": "0.040000", "hold_usd": "0.041000"}),
             "usage": None, "output": None, "error": None,
             "queue_position": 1 if status == "queued" else None, "metadata": {}}
    if status == "succeeded":
        value["output"] = {"url": OUTPUT_URL, "url_expires_at": "2026-10-14T01:00:00Z",
                           "retained_until": "2026-10-21T00:00:00Z",
                           **({"content_type": "audio/mpeg", "duration": 3.5} if audio
                              else {"content_type": "image/png", "width": 1024, "height": 1024})}
    if status == "failed":
        value["error"] = {"code": "upstream_error", "message": "The provider failed."}
    value.update(extra)
    return value


def upload(status="pending", upload_id=UPLOAD_ID, **extra):
    value = {"object": "upload", "id": upload_id, "kind": "image", "status": status,
             "content_type": "image/png", "bytes": 4, "expires_at": "2026-10-14T00:15:00Z"}
    if status == "pending":
        value.update(upload_url=PUT_URL, upload_headers={"Content-Type": "image/png"})
    value.update(extra)
    return value


def balance(available="12.340000"):
    return {"object": "balance", "currency": "usd", "total_usd": available, "held_usd": "0.000000",
            "available_usd": available, "daily_limit_usd": "300.000000", "spent_today_usd": "0.000000",
            "expires_at": None}


def error(code, status, request_id="req_123"):
    return {"code": code, "message": code.replace("_", " "), "request_id": request_id}
