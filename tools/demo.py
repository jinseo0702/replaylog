"""Produce real example outputs and check them against the fixed oracle."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-directory", type=Path, default=ROOT / "work" / "demo")
    args = parser.parse_args()
    out = args.out_directory.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

    def run(*command):
        result = subprocess.run([sys.executable, "-m", "replaylog", *map(str, command)],
                                cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", check=True)
        return result.stdout

    def save(name, value):
        with (out / name).open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")

    with tempfile.TemporaryDirectory(prefix="replaylog-demo-") as directory:
        db = Path(directory) / "events.db"
        source = ROOT / "fixtures" / "golden-12.jsonl"
        first = json.loads(run("ingest", source, "--db", db, "--batch-size", 4))
        run("report", "--db", db, "--out", out / "report.json")
        before = json.loads((out / "report.json").read_text(encoding="utf-8"))
        second = json.loads(run("ingest", source, "--db", db, "--batch-size", 4))
        run("report", "--db", db, "--out", out / "report.json")
        after = json.loads((out / "report.json").read_text(encoding="utf-8"))
        expected = json.loads((ROOT / "fixtures" / "expected-report.json").read_text(encoding="utf-8"))
        if before != expected or after != expected:
            raise RuntimeError("actual report differs from fixed expected result")
        run("rejects", "--db", db, "--out", out / "rejections.jsonl")
        save("first-run-summary.json", first)
        save("replay-summary.json", second)
        save("provenance.json", {
            "platform": platform.platform(), "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "batch_size": 4, "replay_report_equals_first_report": before == after,
            "actual_report_equals_fixed_oracle": after == expected,
            "application_sha256": {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted((ROOT / "replaylog").glob("*.py"))},
        })
    print(json.dumps({"first_run": first, "replay": second, "report_unchanged": before == after}, ensure_ascii=False))


if __name__ == "__main__":
    main()
