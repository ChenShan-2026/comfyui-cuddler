import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from cuddler_plugin.cuddler.credentials import FILE_NAME, KeyStore
from cuddler_plugin.cuddler.errors import CuddlerError
from tests.fakes import LIVE_KEY, TEST_KEY


class KeyStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = {}
        self.store = KeyStore(self.dir, self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty(self):
        self.assertIsNone(self.store.resolve())
        self.assertEqual(self.store.status(), {"source": "none", "mode": None, "last4": None, "error": None})

    def test_saves_trimmed_keys_and_shows_only_the_last_four(self):
        status = self.store.save("  " + LIVE_KEY + "\n")
        self.assertEqual(status, {"source": "file", "mode": "live", "last4": LIVE_KEY[-4:], "error": None})
        self.assertEqual(self.store.resolve(), LIVE_KEY)
        self.assertEqual(json.loads((self.dir / FILE_NAME).read_text())["key"], LIVE_KEY)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_key_file_is_private(self):
        self.store.save(LIVE_KEY)
        self.assertEqual(stat.S_IMODE((self.dir / FILE_NAME).stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_save_creates_a_missing_directory_owner_only(self):
        store = KeyStore(self.dir / "user" / "__cuddler", self.env)
        store.save(LIVE_KEY)
        self.assertEqual(stat.S_IMODE((self.dir / "user" / "__cuddler").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.dir / "user" / "__cuddler" / FILE_NAME).stat().st_mode), 0o600)

    def test_refuses_anything_that_is_not_a_key(self):
        for value in ("", "sk-ant-123", "cud_sk_live_short", None, 42):
            with self.subTest(value=value), self.assertRaises(CuddlerError) as caught:
                self.store.save(value)
            self.assertEqual(caught.exception.code, "key_invalid_format")
        self.assertFalse((self.dir / FILE_NAME).exists())

    def test_environment_variable_wins_and_locks_the_file(self):
        self.store.save(LIVE_KEY)
        self.env["CUDDLER_API_KEY"] = TEST_KEY
        self.assertEqual(self.store.resolve(), TEST_KEY)
        self.assertEqual(self.store.status()["source"], "env")
        self.assertEqual(self.store.status()["mode"], "test")
        for action in (lambda: self.store.save(LIVE_KEY), self.store.clear):
            with self.assertRaises(CuddlerError) as caught:
                action()
            self.assertEqual(caught.exception.code, "key_from_environment")

    def test_empty_environment_variable_counts_as_unset(self):
        self.env["CUDDLER_API_KEY"] = ""
        self.assertIsNone(self.store.resolve())

    def test_environment_value_is_trimmed_like_a_saved_one(self):
        # Windows `set NAME=value ` keeps the trailing space; CRLF env files and `echo | base64` secrets add line breaks.
        for value in (LIVE_KEY + " ", LIVE_KEY + "\n", "  " + LIVE_KEY + "\r\n"):
            with self.subTest(value=value):
                self.env["CUDDLER_API_KEY"] = value
                self.assertEqual(self.store.resolve(), LIVE_KEY)
                self.assertEqual(self.store.status(),
                                 {"source": "env", "mode": "live", "last4": LIVE_KEY[-4:], "error": None})

    def test_blank_environment_variable_counts_as_unset(self):
        self.env["CUDDLER_API_KEY"] = " \r\n"
        self.assertIsNone(self.store.resolve())
        self.assertEqual(self.store.save(LIVE_KEY)["source"], "file")
        self.assertEqual(self.store.resolve(), LIVE_KEY)

    def test_invalid_environment_value_is_reported(self):
        self.env["CUDDLER_API_KEY"] = "not-a-key"
        with self.assertRaises(CuddlerError) as caught:
            self.store.resolve()
        self.assertEqual(caught.exception.code, "key_invalid_format")
        self.assertEqual(self.store.status()["error"], "key_invalid_format")

    def test_corrupt_file_is_reported_not_used(self):
        (self.dir / FILE_NAME).write_text("{not json")
        with self.assertRaises(CuddlerError) as caught:
            self.store.resolve()
        self.assertEqual(caught.exception.code, "key_unreadable")

    def test_unusable_files_are_reported_not_used(self):
        unusable = (b"[]", b"null", b"{}", b'{"key": 5}', b'{"key": "not-a-key"}', b"\xff\xfe\x00", b"[" * 100000)
        for content in unusable:
            with self.subTest(content=content[:20]):
                (self.dir / FILE_NAME).write_bytes(content)
                with self.assertRaises(CuddlerError) as caught:
                    self.store.resolve()
                self.assertEqual(caught.exception.code, "key_unreadable")
                self.assertEqual(self.store.status(),
                                 {"source": "file", "mode": None, "last4": None, "error": "key_unreadable"})

    def test_status_reports_an_unreadable_store_instead_of_raising(self):
        self.store.save(LIVE_KEY)
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(13, "Access is denied")):
            self.assertEqual(self.store.status(),
                             {"source": "file", "mode": None, "last4": None, "error": "key_unreadable"})
            with self.assertRaises(CuddlerError) as caught:
                self.store.resolve()
        self.assertEqual(caught.exception.code, "key_unreadable")

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "needs POSIX permissions that bind this user")
    def test_status_reports_a_directory_it_cannot_enter(self):
        os.chmod(self.dir, 0)
        try:
            self.assertEqual(self.store.status(),
                             {"source": "file", "mode": None, "last4": None, "error": "key_unreadable"})
        finally:
            os.chmod(self.dir, 0o700)

    def test_the_key_never_appears_in_a_status_or_an_error(self):
        shown = []
        self.store.save(LIVE_KEY)
        shown.append(json.dumps(self.store.status()))
        (self.dir / FILE_NAME).write_text('{"version": 1, "key": "%s",}' % LIVE_KEY)  # malformed, yet holds a key
        shown.append(json.dumps(self.store.status()))
        with self.assertRaises(CuddlerError) as caught:
            self.store.resolve()
        shown.extend((str(caught.exception), repr(caught.exception), repr(self.store)))
        self.assertIsNone(caught.exception.__context__)  # nothing reachable from the error still holds the file text
        self.env["CUDDLER_API_KEY"] = TEST_KEY
        status = self.store.status()
        shown.append(json.dumps(status))
        self.assertEqual(set(status), {"source", "mode", "last4", "error"})
        for text in shown:
            self.assertNotIn(LIVE_KEY, text)
            self.assertNotIn(TEST_KEY, text)

    def test_a_failed_save_keeps_the_old_key_and_leaves_no_temporary_file(self):
        self.store.save(LIVE_KEY)
        with mock.patch("os.replace", side_effect=PermissionError(13, "Access is denied")):  # Windows: file in use
            with self.assertRaises(CuddlerError) as caught:
                self.store.save(TEST_KEY)
        self.assertEqual(caught.exception.code, "key_unwritable")
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn(TEST_KEY, str(caught.exception))
        self.assertEqual(self.store.resolve(), LIVE_KEY)
        self.assertEqual([path.name for path in self.dir.iterdir()], [FILE_NAME])

    def test_clear_that_cannot_delete_the_file_says_so_and_keeps_the_key(self):
        self.store.save(LIVE_KEY)
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "The process cannot access the file")):
            with self.assertRaises(CuddlerError) as caught:
                self.store.clear()
        self.assertEqual(caught.exception.code, "key_unwritable")
        self.assertEqual(self.store.resolve(), LIVE_KEY)

    def test_clear_removes_the_file(self):
        self.store.save(LIVE_KEY)
        self.assertEqual(self.store.clear()["source"], "none")
        self.assertFalse((self.dir / FILE_NAME).exists())
        self.assertEqual(self.store.clear()["source"], "none")


if __name__ == "__main__":
    unittest.main()
