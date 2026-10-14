"""Business metrics come from committed events; exports are replaceable."""

import json
import os
from pathlib import Path
import tempfile

from .storage import reader


def business_report(database_path):
    con = reader(database_path)
    try:
        con.execute("BEGIN")
        count, errors = con.execute(
            "SELECT count(*),coalesce(sum(status_code BETWEEN 500 AND 599),0) FROM events"
        ).fetchone()
        hours = con.execute(
            "SELECT substr(event_time,1,13)||':00:00Z' AS hour_utc,count(*) AS event_count,"
            "sum(status_code BETWEEN 500 AND 599) AS server_error_count "
            "FROM events GROUP BY hour_utc ORDER BY hour_utc"
        ).fetchall()
        services = con.execute(
            "SELECT service,count(*) AS event_count,"
            "sum(status_code BETWEEN 500 AND 599) AS server_error_count "
            "FROM events GROUP BY service ORDER BY service"
        ).fetchall()
        result = {
            "event_count": count, "server_error_count": errors,
            "server_error_rate_percent": round(errors / count * 100, 6) if count else 0.0,
            "hourly": [dict(row) for row in hours],
            "services": [dict(row) for row in services],
        }
        con.execute("COMMIT")
        return result
    finally:
        con.close()


def atomic_output(path, write_content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".replaylog-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            write_content(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def export_report(database_path, output_path):
    result = business_report(database_path)

    def write(stream):
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")

    atomic_output(output_path, write)


def export_rejections(database_path, output_path):
    con = reader(database_path)
    try:
        con.execute("BEGIN")

        def write(stream):
            for row in con.execute("SELECT * FROM rejections ORDER BY input_sha256,line_no"):
                stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")

        atomic_output(output_path, write)
        con.execute("COMMIT")
    finally:
        con.close()
