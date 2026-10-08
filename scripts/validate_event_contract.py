#!/usr/bin/env python3
"""Validate the checked-in event contracts without requiring protoc locally."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELD_PATTERN = re.compile(
    r"(?m)^\s*(repeated\s+)?(map<[^>]+>|[\w.<>]+)\s+"
    r"([a-zA-Z_]\w*)\s*=\s*(\d+)\s*;"
)
ENUM_VALUE_PATTERN = re.compile(r"(?m)^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*;")


@dataclass(frozen=True)
class Contract:
    version: str
    messages: tuple[str, ...]
    enums: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        return ROOT / "schemas" / "events" / self.version

    @property
    def proto_path(self) -> Path:
        return self.directory / "events.proto"

    @property
    def baseline_path(self) -> Path:
        return self.directory / "baseline.proto"

    @property
    def compatibility_path(self) -> Path:
        return self.directory / "compatibility.json"


V1 = Contract(
    "v1",
    ("EventEnvelope", "FlowEvent", "FeatureVector", "Detection", "CaptureObject"),
)
V2 = Contract(
    "v2",
    (
        "Envelope", "FlowEvent", "FeatureUpdate", "HorizonEvidence",
        "DetectionTransition", "HealthEvent",
    ),
    (
        "Protocol", "Direction", "AttemptOutcome", "ThreatFamily",
        "TransitionStatus", "Severity", "HealthKind",
    ),
)
CONTRACTS = (V1, V2)

# v1 names kept for existing callers.
PROTO_PATH = V1.proto_path
COMPATIBILITY_PATH = V1.compatibility_path
BASELINE_PATH = V1.baseline_path
MESSAGES = V1.messages


def _body(source: str, name: str, kind: str = "message") -> str:
    match = re.search(rf"{kind}\s+{re.escape(name)}\s*\{{", source)
    if not match:
        raise ValueError(f"{kind} {name} is missing")

    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise ValueError(f"{kind} {name} is not closed")


def _numbered(items, owner: str) -> dict[str, int]:
    numbered: dict[str, int] = {}
    for name, number in items:
        number = int(number)
        if name in numbered:
            raise ValueError(f"duplicate field {owner}.{name}")
        if number in numbered.values():
            raise ValueError(f"duplicate field number {owner}={number}")
        numbered[name] = number
    if not numbered:
        raise ValueError(f"{owner} has no fields")
    return numbered


def parse_contract(
    source: str, messages: tuple[str, ...] = MESSAGES, enums: tuple[str, ...] = (),
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for message in messages:
        fields = ((name, number) for _, _, name, number in FIELD_PATTERN.findall(_body(source, message)))
        result[message] = _numbered(fields, message)
    for enum in enums:
        values = ENUM_VALUE_PATTERN.findall(_body(source, enum, "enum"))
        result[enum] = _numbered(values, enum)
        if result[enum].get(next(iter(result[enum]))) != 0:
            raise ValueError(f"enum {enum} must start with a zero value")
    return result


def _signatures(source: str, message: str) -> dict[str, tuple[str, str]]:
    return {
        name: (label or "", field_type)
        for label, field_type, name, _ in FIELD_PATTERN.findall(_body(source, message))
    }


def validate_compatibility(
    source: str, baseline: str, expected: dict, contract: Contract = V1,
) -> None:
    actual = parse_contract(source, contract.messages, contract.enums)
    if parse_contract(baseline, contract.messages, contract.enums) != expected:
        raise ValueError("frozen baseline.proto does not match compatibility.json")

    for message in contract.messages:
        old_signatures = _signatures(baseline, message)
        new_signatures = _signatures(source, message)
        for name, number in expected[message].items():
            if actual[message].get(name) != number or new_signatures.get(name) != old_signatures[name]:
                raise ValueError(f"incompatible change to {message}.{name} (field {number})")
    for enum in contract.enums:
        for name, number in expected[enum].items():
            if actual[enum].get(name) != number:
                raise ValueError(f"incompatible change to {enum}.{name} (value {number})")


def validate(contract: Contract | None = None) -> None:
    for item in (contract,) if contract else CONTRACTS:
        validate_compatibility(
            item.proto_path.read_text(),
            item.baseline_path.read_text(),
            json.loads(item.compatibility_path.read_text()),
            item,
        )


if __name__ == "__main__":
    try:
        validate()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"event contract validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    for item in CONTRACTS:
        print(f"validated {item.proto_path.relative_to(ROOT)}")
