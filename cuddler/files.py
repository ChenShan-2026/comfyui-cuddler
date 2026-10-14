"""Private files: a directory only this account can read, written atomically.

Works on Windows, macOS and Linux. POSIX systems get 0700 directories and 0600
files. On Windows a directory created here is limited to the current user and
administrators on Python versions that honour os.mkdir(mode=0o700); otherwise,
and for a directory that already exists, it keeps the permissions it inherits.
"""
import contextlib
import os
from pathlib import Path
import tempfile


def ensure_private_dir(path):
    """Create `path` owner-only; an existing directory is tightened too.

    Pass only a directory this package owns, never a shared one such as the current
    directory: SQLite creates its files 0644, so this directory's mode is what keeps
    them private.
    """
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def write_private(path, data):
    """Replace `path` with `data` in one step: readers see the old file or the new one."""
    path = Path(path)
    ensure_private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)  # created 0600
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    if os.name == "posix":
        os.chmod(path, 0o600)
