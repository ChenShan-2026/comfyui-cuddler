"""The organization's API key: from CUDDLER_API_KEY, otherwise from a private file.

The key is never logged, never sent anywhere but api.cuddler.ai, and only its
last four characters are ever shown.
"""
import json
import os
from pathlib import Path
import re

from .errors import CuddlerError
from .files import write_private

ENV_NAME = "CUDDLER_API_KEY"
KEY = re.compile(r"cud_sk_(live|test)_[A-Za-z0-9]{20,128}")
FILE_NAME = "credentials.json"


class KeyStore:
    def __init__(self, directory, environ=None):
        self._path = Path(directory) / FILE_NAME
        self._environ = os.environ if environ is None else environ

    def _from_environment(self):
        """The variable's value without stray whitespace; unset, empty and blank all mean "not provided".

        Whitespace gets in easily (Windows `set NAME=value ` keeps the trailing space, a CRLF env file,
        a Kubernetes secret made with `echo`), and it cannot be fixed from the settings page because a
        key that comes from the environment is read-only there. save() trims its input the same way.
        """
        return (self._environ.get(ENV_NAME) or "").strip() or None

    def _lookup(self):
        """Look once at the environment and, when it holds no key, once at the file: (source, key, error).

        It never raises, so resolve() and status() cannot disagree and a failing disk is reported as an
        error code instead of an exception. `key` is set only when `error` is None, and `source` is "none"
        only when there is nothing to read.
        """
        value = self._from_environment()
        if value is not None:
            if KEY.fullmatch(value):
                return "env", value, None
            return "env", None, "key_invalid_format"
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return "none", None, None
        except OSError:
            return "file", None, "key_unreadable"
        try:
            value = json.loads(raw.decode("utf-8")).get("key")
        except (UnicodeDecodeError, ValueError, AttributeError, RecursionError):
            return "file", None, "key_unreadable"
        if isinstance(value, str) and KEY.fullmatch(value):
            return "file", value, None
        return "file", None, "key_unreadable"

    def resolve(self):
        """The key to use, or None when there is none. Raises when the configured key is unusable."""
        _, key, error = self._lookup()
        if error:
            raise CuddlerError(error)
        return key

    def status(self):
        """What the settings page may show: where the key comes from, its mode and last four characters."""
        source, key, error = self._lookup()
        if key is None:
            return {"source": source, "mode": None, "last4": None, "error": error}
        return {"source": source, "mode": KEY.fullmatch(key).group(1), "last4": key[-4:], "error": None}

    def save(self, key):
        if self._from_environment() is not None:
            raise CuddlerError("key_from_environment")
        if not isinstance(key, str) or not KEY.fullmatch(key.strip()):
            raise CuddlerError("key_invalid_format")
        try:
            write_private(self._path, json.dumps({"version": 1, "key": key.strip()}).encode("utf-8"))
        except OSError:  # read-only folder, full disk, or (on Windows) a file another program has open
            raise CuddlerError("key_unwritable", status=500) from None
        return self.status()

    def clear(self):
        if self._from_environment() is not None:
            raise CuddlerError("key_from_environment")
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError:  # on Windows: another program (an antivirus scan, a sync client) has the file open
            raise CuddlerError("key_unwritable", status=500) from None
        return self.status()
