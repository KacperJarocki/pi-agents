#!/usr/bin/env python3
"""Validate the checked-in event contract without requiring protoc locally."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTO_PATH = ROOT / "schemas" / "events" / "v1" / "events.proto"
COMPATIBILITY_PATH = ROOT / "schemas" / "events" / "v1" / "compatibility.json"


def _message_body(source: str, name: str) -> str:
    match = re.search(rf"message\s+{re.escape(name)}\s*\{{", source)
    if not match:
        raise ValueError(f"message {name} is missing")

    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise ValueError(f"message {name} is not closed")


def parse_contract(source: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for message in ("EventEnvelope", "FlowEvent", "FeatureVector", "Detection", "CaptureObject"):
        fields: dict[str, int] = {}
        body = _message_body(source, message)
        for field_match in re.finditer(
            r"(?m)^\s*(?:repeated\s+)?(?:map<[^>]+>|[\w.<>]+)\s+"
            r"([a-zA-Z_]\w*)\s*=\s*(\d+)\s*;",
            body,
        ):
            name, number = field_match.groups()
            number = int(number)
            if name in fields:
                raise ValueError(f"duplicate field {message}.{name}")
            if number in fields.values():
                raise ValueError(f"duplicate field number {message}={number}")
            fields[name] = number
        if not fields:
            raise ValueError(f"message {message} has no fields")
        result[message] = fields
    return result


def validate() -> None:
    source = PROTO_PATH.read_text()
    expected = json.loads(COMPATIBILITY_PATH.read_text())
    actual = parse_contract(source)
    if actual != expected:
        raise ValueError(
            "event contract changed without updating compatibility.json: "
            f"actual={actual!r} expected={expected!r}"
        )


if __name__ == "__main__":
    try:
        validate()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"event contract validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"validated {PROTO_PATH.relative_to(ROOT)}")
