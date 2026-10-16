import unittest
from decimal import Decimal

from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler.fingerprint import MAX_DEPTH, canonical, digest_bytes, run_fingerprint


def nested(levels):
    value = []
    for _ in range(levels - 1):
        value = [value]
    return value


class FingerprintTest(unittest.TestCase):
    def test_key_order_does_not_matter(self):
        self.assertEqual(canonical({"b": 1, "a": "中"}), '{"a":"中","b":1}'.encode())
        self.assertEqual(run_fingerprint("video", {"a": 1, "b": 2}, {}, 7),
                         run_fingerprint("video", {"b": 2, "a": 1}, {}, 7))

    def test_every_input_changes_the_fingerprint(self):
        base = run_fingerprint("video", {"prompt": "cat"}, {"first_frame": "aa"}, 1)
        for other in (run_fingerprint("image", {"prompt": "cat"}, {"first_frame": "aa"}, 1),
                      run_fingerprint("video", {"prompt": "dog"}, {"first_frame": "aa"}, 1),
                      run_fingerprint("video", {"prompt": "cat"}, {"first_frame": "bb"}, 1),
                      run_fingerprint("video", {"prompt": "cat"}, {"first_frame": "aa"}, 2)):
            self.assertNotEqual(base, other)

    def test_is_a_sha256_hex_digest(self):
        self.assertRegex(run_fingerprint("music", {"prompt": "x"}, {}, 0), r"^[0-9a-f]{64}$")
        self.assertEqual(digest_bytes(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_refuses_values_json_cannot_hold(self):
        with self.assertRaises(CuddlerError):
            canonical({"x": float("nan")})

    def test_every_value_json_cannot_hold_is_an_invalid_request(self):
        loop = []
        loop.append(loop)
        cases = {
            "infinity": float("inf"),
            "nested far too deep": nested(10_000),
            "cycle": loop,
            "lone surrogate in a value": {"prompt": "\ud800"},
            "lone surrogate in a key": {"\ud800": 1},
            "keys that cannot be ordered": {1: "a", "1": "b"},
            "key that is not a scalar": {(1, 2): 1},
            "bytes": b"frame",
            "set": {1, 2},
            "decimal": Decimal("1.5"),
        }
        for name, value in cases.items():
            with self.subTest(name):
                with self.assertRaises(CuddlerError) as caught:
                    canonical(value)
                self.assertEqual(caught.exception.code, "invalid_request")

    def test_the_nesting_limit_does_not_depend_on_the_interpreter(self):
        self.assertTrue(canonical(nested(MAX_DEPTH)))
        with self.assertRaises(CuddlerError):
            canonical(nested(MAX_DEPTH + 1))

    def test_numbers_json_writes_alike_are_one_request(self):
        # JSON has one number type, so the API cannot tell these pairs apart.
        for a, b in ((4, 4.0), (0, -0.0), (10**16, 1e16)):
            with self.subTest(a=a, b=b):
                self.assertEqual(canonical({"duration": a}), canonical({"duration": b}))
        # Values that really differ stay different.
        for a, b in ((4, 4.5), (1, True), (0, False), (0, None), (1, "1"), (0.1, 0.2)):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(canonical({"v": a}), canonical({"v": b}))
        self.assertEqual(canonical([0.5, 1e-7]), b"[0.5,1e-07]")

    def test_text_is_hashed_as_given(self):
        # The API receives different bytes for these two spellings of "café", so they are different requests.
        self.assertNotEqual(run_fingerprint("image", {"prompt": "caf\u00e9"}, {}, 1),  # NFC
                            run_fingerprint("image", {"prompt": "cafe\u0301"}, {}, 1))  # NFD

    def test_the_fingerprint_format_is_frozen(self):
        # Stored fingerprints are looked up again after an upgrade. If the encoding changes they stop
        # matching and a paid job runs a second time: bump VERSION and update these values on purpose.
        body = {"model": "seedance-2.5", "prompt": "an orange ball 中", "duration": 4, "ratio": 0.5,
                "tags": ["a", "b"]}
        self.assertEqual(run_fingerprint("video", body, {"first_frame": digest_bytes(b"frame")}, 2**64 - 1),
                         "369cef0cd9e96b00afb55d97d926b469d5a39e7f77f0a26e2356f1205c05a99b")
        self.assertEqual(run_fingerprint("music", {"prompt": "x", "len": 30.5},
                                         {"refs": [digest_bytes(b"a"), digest_bytes(b"b")]}, 0),
                         "b979f304112e5bb892a589e9d3ac546fff7f9234706b785427c3cab551cb2bff")


if __name__ == "__main__":
    unittest.main()
