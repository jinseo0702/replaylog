"""Small CLI with separate collection, metrics and rejection exports."""

import argparse
import json
import sqlite3
import sys

from .pipeline import ingest
from .reporting import export_report, export_rejections


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(prog="replaylog", description="Replay-safe search log ingestion")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("ingest", help="validate and store an immutable JSONL file")
    collect.add_argument("source")
    collect.add_argument("--db", required=True)
    collect.add_argument("--batch-size", type=positive_integer, default=100)
    collect.add_argument("--test-pause", choices=["before_commit", "after_commit"], help=argparse.SUPPRESS)
    collect.add_argument("--test-batch", type=positive_integer, default=2, help=argparse.SUPPRESS)
    for name in ["report", "rejects"]:
        command = commands.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            summary = ingest(args.source, args.db, args.batch_size, args.test_pause, args.test_batch)
            print(json.dumps(summary, sort_keys=True), flush=True)
        elif args.command == "report":
            export_report(args.db, args.out)
        else:
            export_rejections(args.db, args.out)
    except (OSError, UnicodeError, sqlite3.Error, ValueError, RuntimeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
