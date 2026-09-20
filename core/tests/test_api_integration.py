"""Интеграционные тесты API (§58, §75).

Проверяется сквозной путь целиком, на настоящем приложении и настоящей SQLite:

```text
POST raw event → raw событие сохранено → нормализовано → GET events отдаёт его
```

Это тот же маршрут, который описан в §75 как критерий готовности 0.0.1.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chronoscope import __version__
from chronoscope.domain.events.event_type import SUPPORTED_SCHEMA_VERSION
from chronoscope.infrastructure.config.settings import CoreSettings
from chronoscope.main import create_app
from tests.conftest import load_fixture

API = "/api/v1"

EXPLORER_RAW: dict[str, Any] = {
    "schema_version": 1,
    "raw_event_id": "raw_01K5R8Z9M0Z9Y8X7W6V5T4S3R2",
    "collector": "windows.process",
    "collector_version": "0.0.1",
    "observed_at": "2026-09-18T10:00:00.500Z",
    "source_timestamp": "2026-09-18T10:00:00.000Z",
    "host_id": "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9",
    "boot_id": "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HA",
    "payload_type": "process_start",
    "payload": {
        "pid": 4312,
        "name": "explorer.exe",
        "path": "C:\\Windows\\explorer.exe",
        "process_started_at": "2026-09-18T10:00:00.000Z",
    },
}

EXPLORER_INSTANCE_ID = "proc_01M2SZAT80WBG97HT3GY4XRGRK"
NOTEPAD_INSTANCE_ID = "proc_01M2T1R61M8W1EMS4WBMRDKSTZ"


@pytest.fixture
def client(migrated_database: CoreSettings):  # noqa: ANN201
    """Приложение на временной базе с применёнными миграциями.

    Core не применяет миграции автоматически (инвариант 11), поэтому тест
    делает это так же, как разработчик по инструкции из README.
    """
    with TestClient(create_app(migrated_database)) as test_client:
        yield test_client


@pytest.fixture
def unmigrated_client(temp_settings: CoreSettings):  # noqa: ANN201
    """Приложение на пустой базе без схемы — для проверки диагностики."""
    with TestClient(create_app(temp_settings)) as test_client:
        yield test_client


def post_events(client: TestClient, events: list[dict[str, Any]], *, schema_version: int = 1):  # noqa: ANN201
    return client.post(f"{API}/ingest/raw-events", json={"schema_version": schema_version, "events": events})


def ingest_fixture_batch(client: TestClient):  # noqa: ANN201
    batch = load_fixture("ingest", "ingest_batch_001.json")
    return client.post(f"{API}/ingest/raw-events", json=batch)


class TestSchemaDiagnostics:
    """Core не применяет миграции сам — он обязан внятно об этом сообщать."""

    def test_health_reports_missing_schema(self, unmigrated_client: TestClient) -> None:
        """Без отдельной проверки схемы Core отрапортовал бы «ok» при нерабочем API."""
        body = unmigrated_client.get(f"{API}/health").json()

        assert body["status"] == "degraded"
        assert body["database"] == "schema_missing"

    def test_status_fails_clearly_without_schema(self, unmigrated_client: TestClient) -> None:
        response = unmigrated_client.get(f"{API}/status")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "storage_failure"
        assert "no such table" in response.json()["error"]["message"]


class TestHealthAndStatus:
    def test_health(self, client: TestClient) -> None:
        response = client.get(f"{API}/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": __version__, "database": "ok"}

    def test_status_on_empty_database(self, client: TestClient) -> None:
        body = client.get(f"{API}/status").json()
        assert body["core"] == "running"
        assert body["database"] == "ok"
        assert body["events"] == {"total": 0, "last_minute": 0}
        assert body["raw_events"] == {"total": 0}
        assert body["database_size_bytes"] >= 0

    def test_status_reports_agent_as_null(self, client: TestClient) -> None:
        """Core не отслеживает Agent в 0.0.1 и не выдумывает его состояние."""
        assert client.get(f"{API}/status").json()["agent"] is None


class TestIngestHappyPath:
    def test_batch_fixture_is_accepted(self, client: TestClient) -> None:
        body = ingest_fixture_batch(client).json()
        assert body["accepted"] == 2
        assert body["duplicates"] == 0
        assert body["normalization_failed"] == 0
        assert body["rejected"] == []

    def test_raw_events_are_stored(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        assert client.get(f"{API}/status").json()["raw_events"]["total"] == 2

    def test_normalized_events_are_stored(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        body = client.get(f"{API}/events").json()
        assert body["count"] == 2
        assert {event["type"] for event in body["events"]} == {"process.started", "process.exited"}

    def test_events_are_returned_newest_first(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        events = client.get(f"{API}/events").json()["events"]
        assert [event["type"] for event in events] == ["process.exited", "process.started"]

    def test_start_and_exit_share_process_instance(self, client: TestClient) -> None:
        """§75: завершение относится к тому же экземпляру процесса, что и запуск."""
        ingest_fixture_batch(client)
        events = client.get(f"{API}/events").json()["events"]

        subjects = {event["subject"]["id"] for event in events}
        assert subjects == {NOTEPAD_INSTANCE_ID}

    def test_response_matches_event_contract(self, client: TestClient) -> None:
        """Ответ API должен проходить shared/schemas/event.schema.json."""
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        from tests.conftest import load_schema

        schemes = {name: load_schema(f"{name}") for name in ("raw-event.schema.json", "event.schema.json")}
        registry = Registry()
        for contents in schemes.values():
            registry = registry.with_resource(contents["$id"], Resource.from_contents(contents))

        validator = Draft202012Validator(schemes["event.schema.json"], registry=registry)

        ingest_fixture_batch(client)
        for event in client.get(f"{API}/events").json()["events"]:
            assert list(validator.iter_errors(event)) == []


class TestIdempotency:
    def test_repeat_delivery_creates_no_duplicates(self, client: TestClient) -> None:
        """§34 и §75: повторная отправка того же RawEvent не создаёт дубликат."""
        first = ingest_fixture_batch(client).json()
        second = ingest_fixture_batch(client).json()

        assert first["accepted"] == 2
        assert second["accepted"] == 0
        assert second["duplicates"] == 2

        assert client.get(f"{API}/status").json()["events"]["total"] == 2

    def test_duplicate_delivery_does_not_touch_raw_storage(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        ingest_fixture_batch(client)
        assert client.get(f"{API}/status").json()["raw_events"]["total"] == 2


class TestParentResolution:
    def test_actor_is_resolved_when_parent_was_observed(self, client: TestClient) -> None:
        """Полный путь: сначала наблюдается родитель, затем его потомок.

        Идентификатор родителя совпадает с закоммиченной фикстурой
        shared/fixtures/events/process_started_001.json — то есть контракт
        подтверждается не только на бумаге.
        """
        post_events(client, [EXPLORER_RAW])
        start_raw = load_fixture("windows", "process_start_001.json")
        post_events(client, [start_raw])

        events = client.get(f"{API}/events", params={"type": "process.started"}).json()["events"]
        notepad = next(event for event in events if event["subject"]["name"] == "notepad.exe")

        assert notepad["actor"] is not None
        assert notepad["actor"]["id"] == EXPLORER_INSTANCE_ID
        # Имя родителя взято из его собственного события старта: в payload
        # потомка приходит только parent_pid, поэтому без этого чтения UI не
        # смог бы показать цепочку explorer.exe → notepad.exe (§20).
        assert notepad["actor"]["name"] == "explorer.exe"
        assert notepad["attributes"]["parent_resolved"] is True

    def test_actor_is_empty_when_parent_unobserved(self, client: TestClient) -> None:
        """Реальный случай 0.0.1: explorer.exe стартовал до начала наблюдения."""
        post_events(client, [load_fixture("windows", "process_start_001.json")])

        events = client.get(f"{API}/events").json()["events"]
        assert events[0]["actor"] is None
        assert events[0]["attributes"]["parent_resolved"] is False
        assert events[0]["attributes"]["parent_pid"] == 4312


class TestIngestRobustness:
    def test_one_bad_event_does_not_stop_the_batch(self, client: TestClient) -> None:
        """§60: одно плохое событие не останавливает pipeline."""
        good = load_fixture("windows", "process_start_001.json")
        response = post_events(client, [good, {"broken": True}, good])

        assert response.status_code == 200
        body = response.json()
        assert body["rejected"] == [
            {
                "index": 1,
                "code": "invalid_input",
                "message": body["rejected"][0]["message"],
            }
        ]
        # Первое событие сохранено, третье — дубликат первого.
        assert body["accepted"] == 1
        assert body["duplicates"] == 1

    def test_all_events_invalid_returns_422(self, client: TestClient) -> None:
        response = post_events(client, [{"broken": True}, {"also": "broken"}])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"

    def test_malformed_fixture_is_rejected(self, client: TestClient) -> None:
        response = post_events(client, [load_fixture("windows", "malformed_event.json")])
        assert response.status_code == 422

    def test_unsupported_event_schema_version_is_named(self, client: TestClient) -> None:
        """§52: неподдерживаемая версия схемы отвергается явно и отличается от прочих ошибок.

        Пакет из одного такого события отвергается целиком, и код ошибки обязан
        совпасть с кодом отвергнутого элемента: иначе один и тот же дефект
        данных описывался бы по-разному в зависимости от состава пакета.
        """
        event = {**load_fixture("windows", "process_start_001.json"), "schema_version": 99}
        response = post_events(client, [event])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_schema_version"
        assert "99" in response.json()["error"]["message"]

    def test_unsupported_schema_in_mixed_batch_names_the_rejected_item(self, client: TestClient) -> None:
        """Событие с чужой версией отвергается поштучно и не мешает остальным (§60)."""
        good = load_fixture("windows", "process_start_001.json")
        bad = {**load_fixture("windows", "process_exit_001.json"), "schema_version": 99}

        body = post_events(client, [good, bad]).json()

        assert body["accepted"] == 1
        assert body["rejected"] == [
            {
                "index": 1,
                "code": "unsupported_schema_version",
                "message": body["rejected"][0]["message"],
            }
        ]

    def test_mixed_rejection_reasons_fall_back_to_invalid_input(self, client: TestClient) -> None:
        """Когда причины отказа разные, обобщающий код честнее любой одной из них."""
        bad_version = {**load_fixture("windows", "process_start_001.json"), "schema_version": 99}
        response = post_events(client, [bad_version, {"broken": True}])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"

    def test_unsupported_batch_schema_version_returns_422(self, client: TestClient) -> None:
        response = post_events(client, [load_fixture("windows", "process_start_001.json")], schema_version=99)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_schema_version"

    def test_unknown_payload_is_kept_as_raw(self, client: TestClient) -> None:
        """§60: если нормализатор не понял payload, raw событие сохраняется."""
        event = {
            **load_fixture("windows", "process_start_001.json"),
            "collector": "plugin.steam",
            "payload_type": "game_started",
        }
        body = post_events(client, [event]).json()

        assert body["accepted"] == 1
        assert body["normalization_failed"] == 1

        status = client.get(f"{API}/status").json()
        assert status["raw_events"]["total"] == 1
        assert status["events"]["total"] == 0

    def test_empty_batch_is_rejected(self, client: TestClient) -> None:
        assert post_events(client, []).status_code == 422

    def test_extra_envelope_field_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            f"{API}/ingest/raw-events",
            json={"schema_version": 1, "events": [load_fixture("windows", "process_start_001.json")], "x": 1},
        )
        assert response.status_code == 422

    def test_request_size_limit(self, client: TestClient) -> None:
        """§33: лимит размера запроса задаётся конфигурацией."""
        oversized = "x" * (6 * 1024 * 1024)
        response = client.post(
            f"{API}/ingest/raw-events",
            content=oversized,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"


class TestEventQueries:
    def test_filter_by_type(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        body = client.get(f"{API}/events", params={"type": "process.started"}).json()
        assert body["count"] == 1
        assert body["events"][0]["type"] == "process.started"

    def test_filter_by_multiple_types(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        body = client.get(f"{API}/events", params={"type": "process.started,process.exited"}).json()
        assert body["count"] == 2

    def test_filter_by_subject(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        body = client.get(f"{API}/events", params={"subject_id": NOTEPAD_INSTANCE_ID}).json()
        assert body["count"] == 2

    def test_filter_by_source(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        assert client.get(f"{API}/events", params={"source": "windows.process"}).json()["count"] == 2
        assert client.get(f"{API}/events", params={"source": "plugin.steam"}).json()["count"] == 0

    def test_filter_by_time_range(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        body = client.get(
            f"{API}/events",
            params={"from": "2026-09-18T10:43:00Z", "to": "2026-09-18T10:45:00Z"},
        ).json()
        assert body["count"] == 1
        assert body["events"][0]["type"] == "process.exited"

    def test_time_range_accepts_offset_and_converts(self, client: TestClient) -> None:
        """Смещение в параметре допустимо и пересчитывается в UTC без потери смысла."""
        ingest_fixture_batch(client)
        body = client.get(
            f"{API}/events",
            params={"from": "2026-09-18T15:43:00+05:00", "to": "2026-09-18T15:45:00+05:00"},
        ).json()
        assert body["count"] == 1

    def test_naive_time_parameter_is_rejected(self, client: TestClient) -> None:
        response = client.get(f"{API}/events", params={"from": "2026-09-18T10:00:00"})
        assert response.status_code == 422
        assert "timezone" in response.json()["error"]["message"]

    def test_from_after_to_is_rejected(self, client: TestClient) -> None:
        response = client.get(
            f"{API}/events",
            params={"from": "2026-09-18T12:00:00Z", "to": "2026-09-18T10:00:00Z"},
        )
        assert response.status_code == 422

    def test_limit_bounds(self, client: TestClient) -> None:
        assert client.get(f"{API}/events", params={"limit": 0}).status_code == 422
        assert client.get(f"{API}/events", params={"limit": 100000}).status_code == 422

    def test_pagination(self, client: TestClient) -> None:
        explorer = EXPLORER_RAW
        start_raw = load_fixture("windows", "process_start_001.json")
        exit_raw = load_fixture("windows", "process_exit_001.json")
        post_events(client, [explorer, start_raw, exit_raw])

        first = client.get(f"{API}/events", params={"limit": 2}).json()
        assert first["count"] == 2
        assert first["next_cursor"] is not None

        second = client.get(f"{API}/events", params={"limit": 2, "cursor": first["next_cursor"]}).json()
        assert second["count"] == 1
        assert second["next_cursor"] is None

        ids = [event["id"] for event in first["events"] + second["events"]]
        assert len(ids) == len(set(ids)) == 3

    def test_invalid_cursor_is_rejected(self, client: TestClient) -> None:
        response = client.get(f"{API}/events", params={"cursor": "not-a-cursor"})
        assert response.status_code == 422


class TestEventDetail:
    def test_get_by_id(self, client: TestClient) -> None:
        ingest_fixture_batch(client)
        event_id = client.get(f"{API}/events").json()["events"][0]["id"]

        body = client.get(f"{API}/events/{event_id}").json()
        assert body["id"] == event_id

    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        response = client.get(f"{API}/events/evt_01K5R8Z9M8R1X6Y0Z5D7F9H1K3")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestPersistenceAcrossRestart:
    def test_history_survives_restart(self, migrated_database: CoreSettings) -> None:
        """§75: после перезапуска Core история остаётся в SQLite."""
        with TestClient(create_app(migrated_database)) as first_client:
            ingest_fixture_batch(first_client)
            assert first_client.get(f"{API}/status").json()["events"]["total"] == 2

        with TestClient(create_app(migrated_database)) as second_client:
            assert second_client.get(f"{API}/status").json()["events"]["total"] == 2
            assert second_client.get(f"{API}/events").json()["count"] == 2

    def test_restart_preserves_duplicate_detection(self, migrated_database: CoreSettings) -> None:
        """Идемпотентность не должна зависеть от того, что Core не перезапускался."""
        with TestClient(create_app(migrated_database)) as first_client:
            ingest_fixture_batch(first_client)

        with TestClient(create_app(migrated_database)) as second_client:
            body = ingest_fixture_batch(second_client).json()
            assert body["accepted"] == 0
            assert body["duplicates"] == 2


class TestOpenApi:
    def test_schema_is_available(self, client: TestClient) -> None:
        document = client.get("/openapi.json").json()
        assert document["info"]["version"] == __version__
        for path in (
            f"{API}/health",
            f"{API}/status",
            f"{API}/ingest/raw-events",
            f"{API}/events",
            f"{API}/events/{{event_id}}",
        ):
            assert path in document["paths"], path

    def test_version_matches_supported_schema(self) -> None:
        assert SUPPORTED_SCHEMA_VERSION == 1


class TestErrorEnvelope:
    def test_envelope_shape(self, client: TestClient) -> None:
        body = client.get(f"{API}/events", params={"limit": 0}).json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details"}
        assert json.dumps(body)  # сериализуемо
