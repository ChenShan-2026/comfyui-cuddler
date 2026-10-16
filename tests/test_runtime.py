import json
from pathlib import Path
import platform
import sys
import tempfile
import types
import unittest
from unittest import mock

from cuddler_plugin.cuddler import CLIENT_TAG
from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler.transport import HttpTransport
from cuddler_plugin.cuddler_host import runtime as runtime_module
from cuddler_plugin.cuddler_host.runtime import Runtime
from tests import fakes

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catalog.json"


def folder_paths_stub(user_dir, *, system_users=True):
    stub = types.ModuleType("folder_paths")
    stub.get_user_directory = lambda: str(user_dir)
    if system_users:
        stub.get_system_user_directory = lambda name="system": str(Path(user_dir) / f"__{name}")
    return stub


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.transport = fakes.FakeTransport()
        self.env = {}
        self.runtime = Runtime(self.dir / "__cuddler", settings_path=self.dir / "comfy.settings.json",
                               snapshot_path=FIXTURE, transport=self.transport, environ=self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_client_needs_a_key_and_live_jobs_need_a_live_key(self):
        with self.assertRaises(CuddlerError) as caught:
            self.runtime.client()
        self.assertEqual(caught.exception.code, "key_missing")
        self.runtime.keys.save(fakes.TEST_KEY)
        self.runtime.client()  # prices work with a test key
        with self.assertRaises(CuddlerError) as caught:
            self.runtime.client(live=True)
        self.assertEqual(caught.exception.code, "test_key")

    def test_a_live_key_is_accepted_for_live_jobs(self):
        self.runtime.keys.save(fakes.LIVE_KEY)
        self.runtime.client()
        self.runtime.client(live=True)

    def test_language_follows_comfyui(self):
        self.assertEqual(self.runtime.language(), "en")
        (self.dir / "comfy.settings.json").write_text(json.dumps({"Comfy.Locale": "zh"}), encoding="utf-8")
        self.assertEqual(self.runtime.language(), "zh")

    def test_background_refresh_caches_the_live_catalog(self):
        self.transport.add("GET", "/catalog", body=json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.runtime.refresh_in_background().join(5)
        self.assertTrue((self.dir / "__cuddler" / "catalog.json").exists())

    def test_background_refresh_failures_only_log(self):
        self.transport.fail("GET", "/catalog", CuddlerError("network_error"))
        with self.assertLogs("cuddler", level="WARNING") as logs:
            self.runtime.refresh_in_background().join(5)
        self.assertIn("network_error", logs.output[0])

    def test_a_failed_tidy_does_not_stop_the_model_list_refresh(self):
        cache = self.dir / "__cuddler" / "catalog.json"
        for failure in (CuddlerError("state_unavailable"), RuntimeError("boom")):
            with self.subTest(failure=type(failure).__name__):
                cache.unlink(missing_ok=True)
                self.transport.add("GET", "/catalog", body=json.loads(FIXTURE.read_text(encoding="utf-8")))
                with mock.patch.object(self.runtime.state, "cleanup", side_effect=failure), \
                        self.assertLogs("cuddler", level="WARNING") as logs:
                    self.runtime.refresh_in_background().join(5)
                self.assertTrue(cache.exists())
                self.assertTrue(any("tidying old job records" in line for line in logs.output))
                self.assertFalse(any("model list refresh" in line for line in logs.output))

    def test_a_failed_model_list_refresh_does_not_stop_the_tidy(self):
        self.transport.fail("GET", "/catalog", CuddlerError("network_error"))
        with mock.patch.object(self.runtime.state, "cleanup") as cleanup, \
                self.assertLogs("cuddler", level="WARNING"):
            self.runtime.refresh_in_background().join(5)
        cleanup.assert_called_once_with()

    def test_an_unusable_private_folder_is_state_unavailable(self):
        taken = self.dir / "taken"
        taken.write_text("a file where the folder should be", encoding="utf-8")
        with self.assertRaises(CuddlerError) as caught:
            Runtime(taken, settings_path=self.dir / "comfy.settings.json", snapshot_path=FIXTURE,
                    transport=self.transport, environ=self.env)
        self.assertEqual(caught.exception.code, "state_unavailable")

    def test_the_real_transport_names_the_plugin_and_python_only(self):
        runtime = Runtime(self.dir / "real", settings_path=self.dir / "comfy.settings.json",
                          snapshot_path=FIXTURE, environ=self.env)
        self.assertIsInstance(runtime.transport, HttpTransport)
        self.assertEqual(runtime.transport.user_agent, f"{CLIENT_TAG} (Python {platform.python_version()})")


class SharedRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(runtime_module.configure_transport, None)

    def test_lives_in_the_system_user_directory(self):
        with mock.patch.dict(sys.modules, {"folder_paths": folder_paths_stub(self.tmp.name)}):
            runtime_module.configure_transport(fakes.FakeTransport())
            runtime = runtime_module.get_runtime()
            self.assertIs(runtime, runtime_module.get_runtime())
        self.assertEqual(runtime.directory, Path(self.tmp.name) / "__cuddler")
        self.assertIsInstance(runtime.transport, fakes.FakeTransport)

    def test_older_comfyui_without_system_users_uses_the_same_folder_name(self):
        with mock.patch.dict(sys.modules, {"folder_paths": folder_paths_stub(self.tmp.name, system_users=False)}):
            runtime_module.configure_transport(fakes.FakeTransport())
            runtime = runtime_module.get_runtime()
        self.assertEqual(runtime.directory, Path(self.tmp.name) / "__cuddler")

    def test_a_failed_start_is_not_remembered(self):
        taken = Path(self.tmp.name) / "__cuddler"
        taken.write_text("in the way", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"folder_paths": folder_paths_stub(self.tmp.name)}):
            runtime_module.configure_transport(fakes.FakeTransport())
            with self.assertRaises(CuddlerError) as caught:
                runtime_module.get_runtime()
            self.assertEqual(caught.exception.code, "state_unavailable")
            taken.unlink()  # fixing the folder is enough; no restart
            self.assertIs(runtime_module.get_runtime(), runtime_module.get_runtime())

    def test_language_comes_from_the_default_users_settings(self):
        settings = Path(self.tmp.name) / "default" / "comfy.settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"Comfy.Locale": "zh"}), encoding="utf-8")
        with mock.patch.dict(sys.modules, {"folder_paths": folder_paths_stub(self.tmp.name)}):
            runtime_module.configure_transport(fakes.FakeTransport())
            self.assertEqual(runtime_module.get_runtime().language(), "zh")


if __name__ == "__main__":
    unittest.main()
