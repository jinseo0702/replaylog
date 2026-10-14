"""Validate the fixed v1 contract without repairing input values."""

from datetime import datetime
import json
import re

FIELDS = {"service", "event_id", "event_time", "query", "status_code"}
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class InvalidEvent(ValueError):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidEvent("invalid_schema", "duplicate field: " + key)
        value[key] = item
    return value


def non_json_constant(value):
    raise InvalidEvent("malformed_json", "non-standard JSON constant: " + value)


def parse_event(raw_line):
    try:
        value = json.loads(raw_line, object_pairs_hook=unique_fields,
                           parse_constant=non_json_constant)
    except json.JSONDecodeError as error:
        raise InvalidEvent("malformed_json", "invalid JSON at column " + str(error.colno)) from error
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise InvalidEvent("invalid_schema", "expected exactly: " + ", ".join(sorted(FIELDS)))
    for name in ["service", "event_id", "event_time", "query"]:
        if not isinstance(value[name], str) or not value[name]:
            raise InvalidEvent("invalid_value", name + " must be a non-empty string")
    for name in ["service", "event_id"]:
        if value[name] != value[name].strip():
            raise InvalidEvent("invalid_value", name + " must not have outer whitespace")
    code = value["status_code"]
    if type(code) is not int or not 100 <= code <= 599:
        raise InvalidEvent("invalid_value", "status_code must be an integer from 100 to 599")
    time = value["event_time"]
    if UTC_TIME.fullmatch(time) is None:
        raise InvalidEvent("invalid_value", "event_time must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(time, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise InvalidEvent("invalid_value", "event_time is not a valid calendar timestamp") from error
    return value
