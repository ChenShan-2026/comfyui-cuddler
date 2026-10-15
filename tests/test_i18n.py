import ast
from pathlib import Path
import string
import tempfile
import unittest

from cuddler_plugin.cuddler.client import ERROR_CODES
from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler.i18n import (
    ALIASES, MAX_SETTINGS_BYTES, MESSAGES, describe, language_from_settings)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("cuddler", "cuddler_host")
# Codes that reach CuddlerError through a variable, so the scan below cannot see them. Each must still be a
# string literal in the named file (test_codes_raised_through_variables...), so this list cannot go stale.
DYNAMIC_CODES = {
    "credentials.py": ("key_invalid_format", "key_unreadable"),  # KeyStore._lookup() -> CuddlerError(error)
    "client.py": ("platform_unavailable", "unauthorized", "rate_limited", "invalid_response"),  # _error() fallbacks
}


def fields(template):
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def raise_sites():
    """(where, code, detail keys) for each CuddlerError("code", ...) in the package source.

    The keys are None when `detail` is built somewhere the scan cannot read.
    """
    sites = []
    for directory in SOURCE_DIRS:
        for path in sorted((PACKAGE_ROOT / directory).glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CuddlerError"):
                    continue
                if not (node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    continue
                detail = next((keyword.value for keyword in node.keywords if keyword.arg == "detail"), None)
                keys = set() if detail is None else None
                if isinstance(detail, ast.Dict) and all(isinstance(key, ast.Constant) for key in detail.keys):
                    keys = {key.value for key in detail.keys}
                sites.append((f"{path.name}:{node.lineno}", node.args[0].value, keys))
    return sites


class DescribeTest(unittest.TestCase):
    def test_every_platform_error_code_has_both_languages(self):
        for code in ERROR_CODES:
            key = ALIASES.get(code, code)
            self.assertIn(key, MESSAGES, code)
        for code, (english, chinese) in MESSAGES.items():
            self.assertTrue(english and chinese, code)

    def test_fills_details_and_request_ids(self):
        error = CuddlerError("over_cap", request_id="req_7", detail={"hold": "$3.10", "cap": "$2.00"})
        self.assertEqual(describe(error, "en"),
                         "This run could cost up to $3.10, above this node's limit of $2.00."
                         " Raise the limit or lower the settings. (request req_7)")
        self.assertEqual(describe(error, "zh"),
                         "这次运行最多可能花费 $3.10，高于这个节点的上限 $2.00。请调高上限，或调低相关参数。（请求编号 req_7）")

    def test_missing_details_and_unknown_codes_stay_readable(self):
        self.assertEqual(describe(CuddlerError("job_failed"), "en"), "The job failed: ?")
        self.assertEqual(describe(CuddlerError("brand_new_code"), "zh"), "出错了（brand_new_code）。")
        self.assertEqual(describe(CuddlerError("upstream_error"), "en"), MESSAGES["platform_unavailable"][0])

    def test_detail_values_and_ids_are_printed_as_they_are(self):
        # Only the message is a template. A value with braces, a format spec or a lookalike field is not read again.
        text = "{0} {reason} {x.y} {{}} {code!r}"
        error = CuddlerError("job_failed", request_id="r{1}", detail={"reason": text})
        self.assertEqual(describe(error, "en"), f"The job failed: {text} (request r{{1}})")
        odd = CuddlerError("over_cap", detail={"hold": 3.1, "cap": None, "unused": object()})
        self.assertIn("up to 3.1, above this node's limit of None", describe(odd, "en"))
        self.assertEqual(describe(CuddlerError("rate_limited"), "fr"), MESSAGES["rate_limited"][0])

    def test_stopping_a_run_says_whether_the_job_was_canceled(self):
        self.assertNotEqual(MESSAGES["interrupted"], MESSAGES["interrupted_canceled"])
        self.assertIn("not canceled", MESSAGES["interrupted"][0])
        self.assertIn("was canceled", MESSAGES["interrupted_canceled"][0])


class CompletenessTest(unittest.TestCase):
    """Every code the package can raise has a message, and its raise sites supply what the message fills in."""

    def test_every_code_the_package_raises_has_a_message(self):
        raised = {code for _, code, _ in raise_sites()}
        self.assertTrue(raised, "the scan found no CuddlerError(...) raise sites: did the package move?")
        for code in sorted(raised):
            self.assertIn(ALIASES.get(code, code), MESSAGES, f'CuddlerError("{code}") has no message')

    def test_codes_raised_through_variables_have_messages_and_are_still_in_the_source(self):
        for filename, codes in DYNAMIC_CODES.items():
            source = (PACKAGE_ROOT / "cuddler" / filename).read_text(encoding="utf-8")
            for code in codes:
                self.assertIn(f'"{code}"', source, f"{code} is gone from {filename}: update DYNAMIC_CODES")
                self.assertIn(ALIASES.get(code, code), MESSAGES, code)

    def test_raise_sites_pass_every_detail_their_message_uses(self):
        for where, code, keys in raise_sites():
            needed = set()
            for template in MESSAGES.get(ALIASES.get(code, code), ()):
                needed |= fields(template)
            if keys is not None:
                self.assertLessEqual(needed, keys, f'{where}: CuddlerError("{code}") lacks {sorted(needed - keys)}')

    def test_both_languages_use_the_same_plain_placeholders(self):
        for code, templates in MESSAGES.items():
            self.assertEqual(fields(templates[0]), fields(templates[1]), code)
            for template in templates:
                for _, name, spec, conversion in string.Formatter().parse(template):
                    if name is not None:
                        self.assertTrue(name.isidentifier() and not spec and not conversion, (code, name))

    def test_aliases_point_at_messages_and_do_not_shadow_one(self):
        for alias, target in ALIASES.items():
            self.assertIn(target, MESSAGES, alias)
            self.assertNotIn(alias, MESSAGES, alias)


class LanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "comfy.settings.json"

    def test_reads_comfyui_locale(self):
        self.assertEqual(language_from_settings(self.path), "en")
        self.path.write_text('{"Comfy.Locale": "zh-TW"}', encoding="utf-8")
        self.assertEqual(language_from_settings(self.path), "zh")
        self.assertEqual(language_from_settings(str(self.path)), "zh")
        self.path.write_text('{"Comfy.Locale": "ja"}', encoding="utf-8")
        self.assertEqual(language_from_settings(self.path), "en")
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(language_from_settings(self.path), "en")

    def test_a_byte_order_mark_does_not_hide_the_locale(self):
        self.path.write_bytes(b'\xef\xbb\xbf{"Comfy.Locale": "zh"}')
        self.assertEqual(language_from_settings(self.path), "zh")

    def test_odd_settings_files_mean_english_and_never_raise(self):
        cases = {
            "a list": b"[1, 2]",
            "a string": b'"zh"',
            "null": b"null",
            "a list for a locale": b'{"Comfy.Locale": ["zh"]}',
            "a number for a locale": b'{"Comfy.Locale": 5}',
            "nested thousands deep": b"[" * 200_000,
            "not UTF-8": '{"Comfy.Locale": "zh"}'.encode("utf-16"),
            "bigger than the limit": b'{"Comfy.Locale": "zh", "pad": "' + b"x" * MAX_SETTINGS_BYTES + b'"}',
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.path.write_bytes(content)
                self.assertEqual(language_from_settings(self.path), "en")
        self.assertEqual(language_from_settings(self.tmp.name), "en")  # a folder, not a file
        self.assertEqual(language_from_settings("settings\0.json"), "en")


if __name__ == "__main__":
    unittest.main()
