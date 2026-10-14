from pathlib import Path
import re
import unittest

from cuddler_plugin.cuddler import CLIENT_TAG, __version__

ROOT = Path(__file__).resolve().parents[1]


class VersionTest(unittest.TestCase):
    def test_package_and_registry_versions_match(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE).group(1)
        self.assertEqual(declared, __version__)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")  # the Registry wants X.Y.Z

    def test_client_tag_names_this_version(self):
        self.assertEqual(CLIENT_TAG, "comfyui-cuddler/" + __version__)


if __name__ == "__main__":
    unittest.main()
