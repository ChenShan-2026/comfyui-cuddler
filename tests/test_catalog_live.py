"""Daily drift check against the live catalog (CUDDLER_LIVE_CATALOG=1). Read-only, no key."""
import os
import unittest

from cuddler_plugin.cuddler import CLIENT_TAG
from cuddler_plugin.cuddler.catalog import parse
from cuddler_plugin.cuddler.client import PlatformClient
from cuddler_plugin.cuddler.transport import HttpTransport


@unittest.skipUnless(os.environ.get("CUDDLER_LIVE_CATALOG") == "1", "set CUDDLER_LIVE_CATALOG=1 to check the live catalog")
class LiveCatalogTest(unittest.TestCase):
    def test_this_version_can_show_every_live_model(self):
        document = PlatformClient(HttpTransport(CLIENT_TAG + " (drift check)")).catalog(timeout=30)
        catalog = parse(document)
        self.assertEqual(catalog.problems(), [], "the live catalog needs a plugin update")
        self.assertGreater(len(catalog.models), 0)


if __name__ == "__main__":
    unittest.main()
