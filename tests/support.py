"""Black-box helpers; expected results never call implementation code."""

import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def child_env():
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def event(**changes):
    value = {
        "service": "web", "event_id": "e001",
        "event_time": "2026-10-14T00:00:00Z",
        "query": "날씨", "status_code": 200,
    }
    value.update(changes)
    return value


def events_in(db):
    with closing(sqlite3.connect(db)) as con:
        return con.execute(
            "SELECT service,event_id,event_time,query,status_code "
            "FROM events ORDER BY service,event_id"
        ).fetchall()


def rejections_in(db):
    with closing(sqlite3.connect(db)) as con:
        return con.execute(
            "SELECT input_sha256,line_no,reason,detail,raw_line "
            "FROM rejections ORDER BY input_sha256,line_no"
        ).fetchall()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.db = self.work / "events.db"

    def cli(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "replaylog", *map(str, args)],
            cwd=ROOT, env=child_env(), capture_output=True,
            text=True, encoding="utf-8", timeout=15,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def raw_input(self, text, name="input.jsonl"):
        path = self.work / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        return path

    def input(self, rows, name="input.jsonl"):
        return self.raw_input(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), name
        )

    def ingest(self, path=None, db=None, batch=100):
        result = self.cli(
            "ingest", path or FIXTURES / "golden-12.jsonl",
            "--db", db or self.db, "--batch-size", batch,
        )
        return json.loads(result.stdout)

    def report(self, db=None, name="report.json"):
        out = self.work / name
        self.cli("report", "--db", db or self.db, "--out", out)
        return json.loads(out.read_text(encoding="utf-8"))

    def golden_report(self):
        return json.loads((FIXTURES / "expected-report.json").read_text(encoding="utf-8"))
