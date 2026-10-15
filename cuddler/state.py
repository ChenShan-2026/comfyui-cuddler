"""Job and upload records in SQLite.

A job record holds a run fingerprint, the idempotency key used to create the
job, the job id and its last known status. Nothing else: no key, prompt or media.

Every failure surfaces as CuddlerError("state_unavailable"): a locked, read-only
or unopenable database, a full disk. The one exception is a file that is not a
usable database at all (damaged, or not SQLite): it is moved aside once, kept as
state.sqlite3.damaged, and replaced by an empty database, so a hidden file can
never keep the plugin from working. The records in it are already unreadable;
only a job that was in flight at that moment is forgotten.
"""
from contextlib import closing, contextmanager
import logging
import os
import sqlite3
import time

from .errors import CuddlerError
from .files import ensure_private_dir

log = logging.getLogger("cuddler")

JOB_TTL = 7 * 86400              # the Platform keeps a result for 7 days after the job finishes
JOB_GRACE = 86400                # a row still 'submitting' cannot show when that was: keep it a day longer
UPLOAD_TTL = 29 * 86400          # ready uploads are kept 30 days; reuse them one day less
UNFINISHED_UPLOAD_TTL = 3600     # a PUT must happen within 15 minutes

_SIDECARS = ("-journal", "-wal", "-shm")
_DAMAGED = (11, 26)              # SQLITE_CORRUPT, SQLITE_NOTADB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  fingerprint TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  job_id TEXT,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
  digest TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL,
  upload_id TEXT,
  status TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""


@contextmanager
def _coded_failures():
    """Turn a database or file-system failure into the one error the plugin explains."""
    try:
        yield
    except sqlite3.Error as error:
        raise CuddlerError("state_unavailable", status=500, detail={"reason": str(error)}) from error
    except OSError as error:
        raise CuddlerError("state_unavailable", status=500,
                           detail={"reason": error.strerror or type(error).__name__}) from error


def _is_damaged(error):
    """True when the file is not a readable database (as opposed to busy, read-only or full)."""
    code = getattr(error, "sqlite_errorcode", None)  # Python 3.11+
    if code is not None:
        return (code & 0xFF) in _DAMAGED
    return type(error) is sqlite3.DatabaseError and any(
        text in str(error) for text in ("not a database", "malformed"))


class StateDB:
    def __init__(self, directory, clock=time.time, *, timeout=10):
        self._clock = clock
        self._timeout = timeout
        self.reset_from = None  # where a damaged database was moved to, when this open replaced one
        with _coded_failures():
            self.path = ensure_private_dir(directory) / "state.sqlite3"
            try:
                self._prepare()
            except sqlite3.DatabaseError as error:
                if not _is_damaged(error):
                    raise
                self.reset_from = self._set_aside()
                self._prepare()

    def _prepare(self):
        with closing(self._connect()) as db:
            if db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("database disk image is malformed")
            db.executescript(_SCHEMA)
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    def _set_aside(self):
        """Move the damaged database, and the journal it left, out of the way; return its new path.

        A journal that stayed behind would be replayed into the new database.
        """
        aside = self.path.with_name(self.path.name + ".damaged")
        for suffix in _SIDECARS + ("",):  # the database last: a crash in between simply retries
            source = self.path.with_name(self.path.name + suffix)
            if source.exists():
                os.replace(source, aside.with_name(aside.name + suffix))
        log.warning("cuddler: the job records could not be read and were reset; the old file is kept as %s",
                    aside.name)
        return aside

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=self._timeout)
        db.row_factory = sqlite3.Row
        return db

    def _one(self, sql, args):
        with _coded_failures(), closing(self._connect()) as db:
            row = db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _write(self, sql, args=()):
        with _coded_failures(), closing(self._connect()) as db, db:
            db.execute(sql, args)

    # Jobs.
    def job(self, fingerprint):
        return self._one("SELECT * FROM jobs WHERE fingerprint = ?", (fingerprint,))

    def begin_job(self, fingerprint, kind, idempotency_key):
        """Record a new attempt, replacing whatever the run had: the caller has checked it is dead or absent."""
        now = self._clock()
        self._write("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, NULL, 'submitting', ?, ?)",
                    (fingerprint, kind, idempotency_key, now, now))

    def update_job(self, fingerprint, *, status, job_id=None):
        self._write("UPDATE jobs SET status = ?, job_id = COALESCE(?, job_id), updated_at = ? "
                    "WHERE fingerprint = ?", (status, job_id, self._clock(), fingerprint))

    # Uploads.
    def upload(self, digest):
        return self._one("SELECT * FROM uploads WHERE digest = ?", (digest,))

    def begin_upload(self, digest, idempotency_key):
        self._write("INSERT OR REPLACE INTO uploads VALUES (?, ?, NULL, 'creating', ?)",
                    (digest, idempotency_key, self._clock()))

    def finish_upload(self, digest, upload_id):
        self._write("UPDATE uploads SET upload_id = ?, status = 'ready', created_at = ? WHERE digest = ?",
                    (upload_id, self._clock(), digest))

    def drop_upload(self, digest):
        self._write("DELETE FROM uploads WHERE digest = ?", (digest,))

    def cleanup(self):
        """Forget what the Platform has forgotten. A job is kept from its last update, not its start, and a
        day longer: its result's seven days run from when it finished, and keeping a row too long costs one
        GET, while dropping it too soon costs a second payment."""
        now = self._clock()
        with _coded_failures(), closing(self._connect()) as db, db:
            db.execute("DELETE FROM jobs WHERE updated_at < ?", (now - JOB_TTL - JOB_GRACE,))
            db.execute("DELETE FROM uploads WHERE (status = 'ready' AND created_at < ?) "
                       "OR (status != 'ready' AND created_at < ?)",
                       (now - UPLOAD_TTL, now - UNFINISHED_UPLOAD_TTL))
