"""The event schemas in docs/events, the source of truth for every event in the system (docs/events.md)."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "events"


def load_event_schemas() -> dict[str, Draft202012Validator]:
    """One validator per routing key, taken from each schema's eventType enum."""
    validators: dict[str, Draft202012Validator] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for event_type in schema["properties"]["eventType"]["enum"]:
            assert event_type not in validators, f"{event_type} is in two schemas"
            validators[event_type] = validator
    assert validators, f"no schemas found in {SCHEMA_DIR}"
    return validators
