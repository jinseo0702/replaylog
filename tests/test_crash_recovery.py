"""Kill actual processes at deterministic transaction boundaries on POSIX."""

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import sys
import unittest

from tests.support import CliCase, FIXTURES, ROOT, child_env, events_in, rejections_in


@unittest.skipUnless(os.name == "posix", "requires POSIX SIGKILL; run on Linux/WSL")
class CrashRecoveryTests(CliCase):
    def crash(self, db, phase):
        proc = subprocess.Popen(
            [sys.executable, "-m", "replaylog", "ingest", str(FIXTURES / "golden-12.jsonl"),
             "--db", str(db), "--batch-size", "4",
             "--test-pause", phase, "--test-batch", "2"],
            cwd=ROOT, env=child_env(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        self.addCleanup(self.cleanup_child, proc)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stderr, selectors.EVENT_READ)
            self.assertTrue(selector.select(10), "did not reach transaction boundary")
            marker = proc.stderr.readline()
        self.assertEqual(marker.strip(), "REPLAYLOG_PAUSED " + phase + " 2")
        os.kill(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        self.assertEqual(stdout, "", "a killed run must not report successful completion")
        return proc.returncode

    @staticmethod
    def cleanup_child(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)

    def verify_recovery(self, phase, expected_events, expected_rejections):
        baseline = self.work / "baseline.db"
        self.ingest(db=baseline, batch=4)
        expected = (events_in(baseline), rejections_in(baseline), self.report(db=baseline))
        self.assertEqual(expected[2], self.golden_report())
        for repeat in range(3):
            with self.subTest(phase=phase, repeat=repeat):
                db = self.work / (phase + "-" + str(repeat) + ".db")
                killed_status = self.crash(db, phase)
                self.assertEqual(len(events_in(db)), expected_events)
                self.assertEqual(len(rejections_in(db)), expected_rejections)
                self.ingest(db=db, batch=4)
                actual = (events_in(db), rejections_in(db), self.report(db=db))
                self.assertEqual(actual, expected)
                with closing(sqlite3.connect(db)) as con:
                    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                    self.assertEqual(integrity, "ok")
                    journal = con.execute("PRAGMA journal_mode").fetchone()[0]
                    synchronous = con.execute("PRAGMA synchronous").fetchone()[0]
                evidence = {
                    "phase": phase, "repeat": repeat + 1, "batch_size": 4,
                    "target_batch": 2, "kill_exit_code": killed_status,
                    "committed_events_after_kill": expected_events,
                    "committed_rejections_after_kill": expected_rejections,
                    "recovered_events": len(actual[0]), "recovered_rejections": len(actual[1]),
                    "matches_baseline": actual == expected, "integrity_check": integrity,
                    "journal_mode": journal, "synchronous": synchronous,
                    "input_sha256": hashlib.sha256((FIXTURES / "golden-12.jsonl").read_bytes()).hexdigest(),
                }
                print("CRASH_EVIDENCE " + json.dumps(evidence, sort_keys=True), file=sys.stderr, flush=True)

    def test_kill_before_commit_then_replay(self):
        self.verify_recovery("before_commit", 4, 0)

    def test_kill_after_commit_then_replay(self):
        self.verify_recovery("after_commit", 5, 1)
