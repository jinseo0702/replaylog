"""Persistent event identity and rejection identity in a single database."""

from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    service TEXT NOT NULL CHECK(length(service)>0),
    event_id TEXT NOT NULL CHECK(length(event_id)>0),
    event_time TEXT NOT NULL,
    query TEXT NOT NULL CHECK(length(query)>0),
    status_code INTEGER NOT NULL CHECK(typeof(status_code)='integer' AND status_code BETWEEN 100 AND 599),
    PRIMARY KEY(service,event_id)
);
CREATE TABLE IF NOT EXISTS rejections (
    input_sha256 TEXT NOT NULL,
    line_no INTEGER NOT NULL CHECK(line_no>0),
    reason TEXT NOT NULL CHECK(reason IN ('malformed_json','invalid_schema','invalid_value','id_conflict')),
    detail TEXT NOT NULL,
    raw_line TEXT NOT NULL,
    PRIMARY KEY(input_sha256,line_no)
);
"""


def writer(path):
    con = sqlite3.connect(path, isolation_level=None, timeout=5)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA synchronous=FULL")
        con.executescript(SCHEMA)
    except BaseException:
        con.close()
        raise
    return con


def reader(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError("database does not exist: " + str(path))
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def reject(con, source, line_no, reason, detail, raw_line):
    con.execute(
        "INSERT INTO rejections(input_sha256,line_no,reason,detail,raw_line) VALUES(?,?,?,?,?) "
        "ON CONFLICT(input_sha256,line_no) DO NOTHING",
        (source, line_no, reason, detail, raw_line),
    )


def save_event(con, value):
    key = (value["service"], value["event_id"])
    content = (value["event_time"], value["query"], value["status_code"])
    existing = con.execute(
        "SELECT event_time,query,status_code FROM events WHERE service=? AND event_id=?", key
    ).fetchone()
    if existing is not None:
        return "duplicates" if tuple(existing) == content else "conflicts"
    con.execute(
        "INSERT INTO events(service,event_id,event_time,query,status_code) VALUES(?,?,?,?,?)",
        key + content,
    )
    return "inserted"
