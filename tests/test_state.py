import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import unittest
from unittest import mock

from cuddler_plugin.cuddler import state
from cuddler_plugin.cuddler.errors import CuddlerError
from cuddler_plugin.cuddler.state import JOB_GRACE, JOB_TTL, UNFINISHED_UPLOAD_TTL, UPLOAD_TTL, StateDB


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1_000_000.0
        self.db = StateDB(Path(self.tmp.name), clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def test_job_lifecycle(self):
        self.assertIsNone(self.db.job("fp"))
        self.db.begin_job("fp", "video", "job-key-1")
        self.assertEqual(self.db.job("fp")["status"], "submitting")
        self.db.update_job("fp", status="queued", job_id="vid_x")
        self.db.update_job("fp", status="succeeded")
        row = self.db.job("fp")
        self.assertEqual((row["status"], row["job_id"], row["idempotency_key"]), ("succeeded", "vid_x", "job-key-1"))

    def test_begin_job_replaces_an_old_attempt(self):
        self.db.begin_job("fp", "video", "job-key-1")
        self.db.update_job("fp", status="failed", job_id="vid_x")
        self.db.begin_job("fp", "video", "job-key-2")
        row = self.db.job("fp")
        self.assertEqual((row["idempotency_key"], row["job_id"], row["status"]), ("job-key-2", None, "submitting"))

    def test_upload_lifecycle(self):
        self.db.begin_upload("d:image", "upl-key")
        self.assertEqual(self.db.upload("d:image")["status"], "creating")
        self.db.finish_upload("d:image", "upl_x")
        self.assertEqual(self.db.upload("d:image")["upload_id"], "upl_x")
        self.db.drop_upload("d:image")
        self.assertIsNone(self.db.upload("d:image"))

    def test_cleanup_honours_platform_retention(self):
        self.db.begin_job("old", "video", "k1")
        self.db.begin_upload("ready-old", "k2")
        self.db.finish_upload("ready-old", "upl_a")
        self.db.begin_upload("stuck", "k3")
        self.now += UNFINISHED_UPLOAD_TTL + 1
        self.db.begin_job("new", "video", "k4")
        self.db.cleanup()
        self.assertIsNone(self.db.upload("stuck"))
        self.assertIsNotNone(self.db.upload("ready-old"))
        self.now += max(JOB_TTL, UPLOAD_TTL)
        self.db.cleanup()
        self.assertIsNone(self.db.job("old"))
        self.assertIsNone(self.db.upload("ready-old"))

    def test_survives_reopening(self):
        self.db.begin_job("fp", "image", "k")
        again = StateDB(Path(self.tmp.name), clock=lambda: self.now)
        self.assertEqual(again.job("fp")["kind"], "image")


class StateFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.now = 1_000_000.0

    def open(self, **kwargs):
        return StateDB(self.dir, clock=lambda: self.now, **kwargs)

    def test_a_file_that_is_not_a_database_is_set_aside_and_replaced(self):
        garbage = b"this is not a database " * 40
        (self.dir / "state.sqlite3").write_bytes(garbage)
        with self.assertLogs("cuddler", "WARNING") as logs:
            db = self.open()
        self.assertIn("state.sqlite3.damaged", logs.output[0])
        self.assertEqual(db.reset_from.read_bytes(), garbage)
        db.begin_job("fp", "video", "k")
        self.assertEqual(db.job("fp")["status"], "submitting")
        self.assertIsNone(self.open().reset_from)  # a healthy file is left alone

    def test_a_journal_left_by_a_damaged_file_goes_with_it(self):
        for name in ("state.sqlite3", "state.sqlite3-journal"):
            (self.dir / name).write_bytes(b"left over " + name.encode())
        real, attempts = StateDB._prepare, []

        def damaged_the_first_time(db):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.DatabaseError("file is not a database")
            return real(db)

        with mock.patch.object(StateDB, "_prepare", damaged_the_first_time), self.assertLogs("cuddler", "WARNING"):
            db = self.open()
        self.assertEqual(sorted(os.listdir(self.dir)),
                         ["state.sqlite3", "state.sqlite3.damaged", "state.sqlite3.damaged-journal"])
        self.assertEqual((self.dir / "state.sqlite3.damaged-journal").read_bytes(), b"left over state.sqlite3-journal")
        db.begin_job("fp", "video", "k")  # the journal did not follow the new file
        self.assertEqual(db.job("fp")["status"], "submitting")

    def test_only_an_unreadable_file_counts_as_damaged(self):
        for error in (sqlite3.DatabaseError("file is not a database"),
                      sqlite3.DatabaseError("database disk image is malformed"),
                      sqlite3.DatabaseError("malformed database schema (jobs)")):
            self.assertTrue(state._is_damaged(error), error)
        for error in (sqlite3.OperationalError("database is locked"),
                      sqlite3.OperationalError("unable to open database file"),
                      sqlite3.OperationalError("attempt to write a readonly database"),
                      sqlite3.OperationalError("database or disk is full"),
                      sqlite3.DatabaseError("something else")):
            self.assertFalse(state._is_damaged(error), error)

    def test_a_damaged_table_page_is_found_when_opening(self):
        db = self.open()
        db.begin_job("fp", "video", "k")
        raw = bytearray(db.path.read_bytes())
        size = int.from_bytes(raw[16:18], "big")
        size = 65536 if size == 1 else size
        raw[size:2 * size] = b"\xff" * size  # page 2 holds the jobs table
        db.path.write_bytes(bytes(raw))
        with self.assertLogs("cuddler", "WARNING"):
            again = self.open()
        self.assertIsNotNone(again.reset_from)
        self.assertIsNone(again.job("fp"))
        again.begin_job("fp", "video", "k2")
        self.assertEqual(again.job("fp")["idempotency_key"], "k2")

    def test_a_locked_database_is_reported_and_left_alone(self):
        db = self.open(timeout=0.05)
        db.begin_job("fp", "video", "k")
        holder = sqlite3.connect(db.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        calls = (lambda: db.job("fp"), lambda: db.begin_job("other", "video", "k2"),
                 lambda: db.update_job("fp", status="queued"), db.cleanup,
                 lambda: StateDB(self.dir, timeout=0.05))
        for call in calls:
            with self.assertRaises(CuddlerError) as caught:
                call()
            self.assertEqual((caught.exception.code, caught.exception.status), ("state_unavailable", 500))
            self.assertEqual(caught.exception.detail["reason"], "database is locked")
        holder.execute("ROLLBACK")
        self.assertEqual(db.job("fp")["idempotency_key"], "k")  # nothing was lost, nothing moved
        self.assertEqual(os.listdir(self.dir), ["state.sqlite3"])

    def test_a_path_that_cannot_be_opened_is_reported_not_replaced(self):
        (self.dir / "state.sqlite3").mkdir()
        with self.assertRaises(CuddlerError) as caught:
            self.open()
        self.assertEqual(caught.exception.code, "state_unavailable")
        self.assertTrue((self.dir / "state.sqlite3").is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_the_database_and_its_journal_are_private(self):
        db = self.open()
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(db.path).st_mode), 0o600)
        connection = sqlite3.connect(db.path)
        self.addCleanup(connection.close)
        connection.execute("INSERT INTO jobs VALUES ('a', 'video', 'k', NULL, 'submitting', 1, 1)")
        journal = db.path.with_name("state.sqlite3-journal")  # what a crash would leave behind
        self.assertEqual(stat.S_IMODE(os.stat(journal).st_mode), 0o600)
        connection.rollback()

    def test_threads_share_one_database(self):
        db = self.open()
        failures = []

        def work(number):
            try:
                for turn in range(25):
                    fingerprint = f"fp-{number}-{turn % 3}"
                    db.begin_job(fingerprint, "video", f"k-{number}-{turn}")
                    db.update_job(fingerprint, status="queued", job_id=f"vid_{number}")
                    self.assertEqual(db.job(fingerprint)["job_id"], f"vid_{number}")
                    db.cleanup()
            except BaseException as error:  # collected here: an exception in a thread would otherwise vanish
                failures.append(error)

        threads = [threading.Thread(target=work, args=(number,)) for number in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])

    def test_cleanup_keeps_each_record_for_its_whole_retention(self):
        db = self.open()
        start = self.now
        db.begin_job("submitting", "video", "k1")
        db.begin_job("finished", "video", "k2")
        db.begin_upload("ready", "k3")
        db.finish_upload("ready", "upl_1")
        db.begin_upload("pending", "k4")
        self.now = start + 2400
        db.update_job("finished", status="succeeded", job_id="vid_1")  # the result exists from here on
        for moment, expect in (
                (UNFINISHED_UPLOAD_TTL - 1, dict(pending=True, ready=True, submitting=True, finished=True)),
                (UNFINISHED_UPLOAD_TTL + 1, dict(pending=False, ready=True, submitting=True, finished=True)),
                (JOB_TTL + 60, dict(pending=False, ready=True, submitting=True, finished=True)),
                (JOB_TTL + JOB_GRACE - 1, dict(pending=False, ready=True, submitting=True, finished=True)),
                (JOB_TTL + JOB_GRACE + 60, dict(pending=False, ready=True, submitting=False, finished=True)),
                (2400 + JOB_TTL + JOB_GRACE + 1, dict(pending=False, ready=True, submitting=False, finished=False)),
                (UPLOAD_TTL - 1, dict(pending=False, ready=True, submitting=False, finished=False)),
                (UPLOAD_TTL + 1, dict(pending=False, ready=False, submitting=False, finished=False))):
            self.now = start + moment
            db.cleanup()
            kept = dict(pending=db.upload("pending") is not None, ready=db.upload("ready") is not None,
                        submitting=db.job("submitting") is not None, finished=db.job("finished") is not None)
            self.assertEqual(kept, expect, f"{moment} seconds after the start")


if __name__ == "__main__":
    unittest.main()
