"""HTTP checks against a running ComfyUI with the plugin and the test bootstrap installed.

    python tests/host/check_host.py --base http://127.0.0.1:8188 --user-dir ComfyUI/user
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.request

KEY = "cud_sk_live_" + "A" * 24


def call(base, method, path, body=None, *, origin=None, site=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if origin:
        headers["Origin"] = origin
    if site:
        headers["Sec-Fetch-Site"] = site
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # always the local server
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--user-dir", required=True)
    args = parser.parse_args()
    base = args.base.rstrip("/")
    page = {"origin": base, "site": "same-origin"}
    results = []

    def check(name, ok, detail=b""):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail[:300]!r}"))

    status, body = call(base, "GET", "/extensions")
    check("the web extension is served",
          status == 200 and any(path.endswith("/cuddler/cuddler.js") for path in json.loads(body)), body)

    status, body = call(base, "GET", "/api/cuddler/status", **page)
    info = json.loads(body) if status == 200 else {}
    check("status answers, no key yet", info.get("key", {}).get("source") == "none", body)
    check("the model list is loaded", info.get("catalog", {}).get("models", 0) > 0, body)

    status, body = call(base, "POST", "/api/cuddler/key", {"key": KEY}, **page)
    check("a key saves and only its last four characters come back",
          status == 200 and KEY.encode() not in body and json.loads(body)["key"]["last4"] == "AAAA", body)
    key_file = Path(args.user_dir) / "__cuddler" / "credentials.json"
    check("the key file is in ComfyUI's system-user directory", key_file.is_file())
    if os.name == "posix":
        check("the key file is private (0600)", stat.S_IMODE(key_file.stat().st_mode) == 0o600)
    for path in ("/api/userdata/credentials.json", "/api/userdata/..%2F__cuddler%2Fcredentials.json",
                 "/api/userdata/%2E%2E%2F__cuddler%2Fcredentials.json", "/api/userdata?dir=..%2F__cuddler"):
        status, body = call(base, "GET", path)
        check(f"the key is not readable through {path}", status != 200 and KEY.encode() not in body,
              str(status).encode() + b" " + body)

    status, body = call(base, "GET", "/api/cuddler/status?balance=1", **page)
    check("the balance comes through",
          status == 200 and json.loads(body).get("balance", {}).get("available_display") == "$12.34", body)
    status, body = call(base, "POST", "/api/cuddler/estimate",
                        {"kind": "video", "body": {"model": "seedance-2.5", "duration": 4, "resolution": "720p"}},
                        **page)
    check("a price estimate comes through", status == 200 and json.loads(body).get("display") == "$0.66", body)

    status, body = call(base, "POST", "/api/cuddler/key", {"key": KEY}, origin="http://evil.example",
                        site="cross-site")
    check("another site cannot set the key", status == 403, str(status).encode())

    status, body = call(base, "DELETE", "/api/cuddler/key", **page)
    check("the key can be removed",
          status == 200 and json.loads(body)["key"]["source"] == "none" and not key_file.exists(), body)

    print(f"{sum(results)}/{len(results)} host checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
