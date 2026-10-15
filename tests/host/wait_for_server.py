"""Wait until a ComfyUI server answers /system_stats.   python tests/host/wait_for_server.py URL SECONDS"""
import sys
import time
import urllib.request


def main():
    base, deadline = sys.argv[1].rstrip("/"), time.monotonic() + float(sys.argv[2])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(base + "/system_stats", timeout=5) as response:
                if response.status == 200:
                    print("ComfyUI is up")
                    return 0
        except OSError:
            pass
        time.sleep(2)
    print("ComfyUI did not start in time")
    return 1


if __name__ == "__main__":
    sys.exit(main())
