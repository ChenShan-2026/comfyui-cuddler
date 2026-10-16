"""Write data/catalog_snapshot.json from the live public catalog. Run before every release.

    python scripts/export_catalog_snapshot.py

Refuses to write a snapshot that holds anything this plugin version cannot show.
"""
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if "cuddler_plugin" not in sys.modules:
    package = types.ModuleType("cuddler_plugin")
    package.__path__ = [str(ROOT)]
    sys.modules["cuddler_plugin"] = package

from cuddler_plugin.cuddler import CLIENT_TAG  # noqa: E402
from cuddler_plugin.cuddler.catalog import parse  # noqa: E402
from cuddler_plugin.cuddler.client import PlatformClient  # noqa: E402
from cuddler_plugin.cuddler.transport import HttpTransport  # noqa: E402


def main():
    document = PlatformClient(HttpTransport(CLIENT_TAG + " (release)")).catalog(timeout=30)
    catalog = parse(document)
    if catalog.problems():
        print("Not writing a snapshot; this version cannot show:\n  " + "\n  ".join(catalog.problems()))
        return 1
    target = ROOT / "data" / "catalog_snapshot.json"
    target.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    kinds = {kind: len(catalog.of_kind(kind)) for kind in ("video", "image", "speech", "music")}
    print(f"Wrote {target.relative_to(ROOT)}: {len(catalog.models)} models {kinds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
