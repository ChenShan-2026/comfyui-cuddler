import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from cuddler_plugin.cuddler.files import ensure_private_dir, write_private


class PrivateFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "private"

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_and_replaces_whole_files(self):
        target = self.root / "a.json"
        write_private(target, b"one")
        write_private(target, b"two")
        self.assertEqual(target.read_bytes(), b"two")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["a.json"])  # no temp files left

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_only_the_owner_can_read(self):
        write_private(self.root / "a.json", b"secret")
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "a.json").stat().st_mode), 0o600)

    def test_ensure_private_dir_is_idempotent(self):
        self.assertEqual(ensure_private_dir(self.root), self.root)
        self.assertEqual(ensure_private_dir(self.root), self.root)
        self.assertTrue(self.root.is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_a_new_directory_is_private_from_the_moment_it_exists(self):
        previous = os.umask(0)
        try:
            with mock.patch("cuddler_plugin.cuddler.files.os.chmod"):  # mkdir's own mode must do it
                ensure_private_dir(self.root)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_tightens_a_loose_directory_and_file_that_already_exist(self):
        self.root.mkdir()
        os.chmod(self.root, 0o755)
        target = self.root / "a.json"
        target.write_bytes(b"old")
        os.chmod(target, 0o644)
        write_private(target, b"secret")
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        os.chmod(self.root, 0o777)
        ensure_private_dir(self.root)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(self):
        target = self.root / "a.json"
        write_private(target, b"old")
        for failure in (OSError("disk gone"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch("cuddler_plugin.cuddler.files.os.replace", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        write_private(target, b"new")
                self.assertEqual(target.read_bytes(), b"old")
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["a.json"])


if __name__ == "__main__":
    unittest.main()
