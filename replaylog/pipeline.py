"""Explicit batch transactions; replay the immutable source after failure."""

import hashlib
import sys

from .storage import reject, save_event, writer
from .validation import InvalidEvent, parse_event


def pause_for_test(phase, batch, test_phase, test_batch):
    if phase == test_phase and batch == test_batch:
        print("REPLAYLOG_PAUSED " + phase + " " + str(batch), file=sys.stderr, flush=True)
        if not sys.stdin.readline():
            raise RuntimeError("test pause requires an open control pipe")


def ingest(source_path, database_path, batch_size=100, test_phase=None, test_batch=2):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    summary = dict(input_lines=0, inserted=0, duplicates=0, invalid=0, conflicts=0)
    with open(source_path, "rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        source_id = digest.hexdigest()
        source.seek(0)
        con = writer(database_path)
        batch_number = 0
        batch_lines = 0

        def commit_batch():
            pause_for_test("before_commit", batch_number, test_phase, test_batch)
            con.execute("COMMIT")
            pause_for_test("after_commit", batch_number, test_phase, test_batch)

        try:
            for line_no, raw_bytes in enumerate(source, 1):
                if batch_lines == 0:
                    con.execute("BEGIN IMMEDIATE")
                    batch_number += 1
                # Decode one physical line at a time; an invalid later line must
                # not prevent an earlier completed batch from being committed.
                raw = raw_bytes.decode("utf-8").removesuffix("\n").removesuffix("\r")
                summary["input_lines"] += 1
                try:
                    value = parse_event(raw)
                except InvalidEvent as error:
                    reject(con, source_id, line_no, error.reason, error.detail, raw)
                    summary["invalid"] += 1
                else:
                    outcome = save_event(con, value)
                    summary[outcome] += 1
                    if outcome == "conflicts":
                        detail = "different content for " + value["service"] + "/" + value["event_id"]
                        reject(con, source_id, line_no, "id_conflict", detail, raw)
                batch_lines += 1
                if batch_lines == batch_size:
                    commit_batch()
                    batch_lines = 0
            if batch_lines:
                commit_batch()
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()
    return summary
