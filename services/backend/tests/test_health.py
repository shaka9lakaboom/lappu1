import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema

from app import __version__

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "schemas"
    / "health-response.schema.json"
)


def test_health_returns_200(client) -> None:
    assert client.get("/health").status_code == 200


def test_health_matches_shared_contract(client) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    body = client.get("/health").json()
    jsonschema.validate(body, schema)


def test_health_reports_configuration(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "skillmirror-backend"
    assert body["environment"] == "test"
    assert body["version"] == __version__


def test_health_timestamp_is_current_utc(client) -> None:
    timestamp = datetime.fromisoformat(client.get("/health").json()["timestamp"])
    assert timestamp.tzinfo is not None
    assert abs(datetime.now(UTC) - timestamp) < timedelta(seconds=10)


def test_no_unversioned_alias_under_v1(client) -> None:
    # /health is intentionally unversioned (architecture section 13).
    assert client.get("/v1/health").status_code == 404
