"""Контрактные тесты: Core против общих JSON-схем (§51, §52, §58).

§51 требует, чтобы Agent и Core тестировались против **одних и тех же** схем.
У Core есть вторая реализация контракта — транспортные модели Pydantic в
``api/schemas.py``. Дублирование неизбежно, поэтому здесь проверяется, что обе
реализации не разошлись: и схемы, и модели должны одинаково принимать и
отвергать одни и те же данные.

Без этого теста расхождение проявилось бы только в момент подключения Agent —
то есть тогда, когда чинить его дороже всего.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

from chronoscope.api.schemas import EventOut, IngestBatchIn, RawEventIn
from chronoscope.domain.ids import (
    new_boot_id,
    new_event_id,
    new_host_id,
    new_raw_event_id,
    process_instance_id,
)
from chronoscope.domain.events.event import Event
from chronoscope.domain.events.event_type import SUPPORTED_SCHEMA_VERSION
from chronoscope.domain.events.raw_event import RawEvent
from tests.conftest import FIXTURES_DIR, SCHEMAS_DIR, load_fixture, load_schema
from tests.factories import event_from_contract, raw_event_from_contract

SCHEMA_FILES = ("raw-event.schema.json", "event.schema.json", "ingest-batch.schema.json")

RAW_FIXTURES = [
    ("windows", "process_start_001.json"),
    ("windows", "process_exit_001.json"),
    ("windows", "process_start_missing_path.json"),
    ("windows", "malformed_event.json"),
]
EVENT_FIXTURES = [
    ("events", "process_started_001.json"),
    ("events", "process_exited_001.json"),
]


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict[str, Any]]:
    return {name: load_schema(name) for name in SCHEMA_FILES}


@pytest.fixture(scope="module")
def registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    result = Registry()
    for contents in schemas.values():
        result = result.with_resource(contents["$id"], Resource.from_contents(contents))
    return result


def json_schema_errors(schema: dict[str, Any], instance: Any, registry: Registry) -> list[str]:
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    return [error.message for error in validator.iter_errors(instance)]


class TestSchemasAreValid:
    @pytest.mark.parametrize("name", SCHEMA_FILES)
    def test_schema_is_valid_draft_2020_12(self, name: str, schemas: dict[str, Any]) -> None:
        Draft202012Validator.check_schema(schemas[name])

    def test_cross_file_ref_resolves_offline(self, schemas: dict[str, Any], registry: Registry) -> None:
        """ingest-batch ссылается на raw-event по $id — registry должен это разрешить."""
        lookup = registry.resolver(schemas["ingest-batch.schema.json"]["$id"])
        assert lookup.lookup("raw-event.schema.json") is not None

    def test_all_schemas_are_loaded(self, schemas: dict[str, Any]) -> None:
        assert set(schemas) == set(SCHEMA_FILES)
        assert len(list(SCHEMAS_DIR.glob("*.schema.json"))) == len(SCHEMA_FILES)


class TestFixturesAgainstJsonSchema:
    @pytest.mark.parametrize("path", RAW_FIXTURES)
    def test_raw_fixtures(self, path: tuple[str, str], schemas: dict[str, Any], registry: Registry) -> None:
        instance = load_fixture(*path)
        errors = json_schema_errors(schemas["raw-event.schema.json"], instance, registry)

        if path[-1] == "malformed_event.json":
            assert errors, "malformed_event.json обязан не проходить валидацию"
        else:
            assert errors == []

    @pytest.mark.parametrize("path", EVENT_FIXTURES)
    def test_event_fixtures(self, path: tuple[str, str], schemas: dict[str, Any], registry: Registry) -> None:
        assert json_schema_errors(schemas["event.schema.json"], load_fixture(*path), registry) == []

    def test_ingest_batch_fixture(self, schemas: dict[str, Any], registry: Registry) -> None:
        instance = load_fixture("ingest", "ingest_batch_001.json")
        assert json_schema_errors(schemas["ingest-batch.schema.json"], instance, registry) == []

    def test_every_json_file_is_covered(self) -> None:
        """Ни одна фикстура не должна остаться без проверки."""
        covered = {FIXTURES_DIR.joinpath(*path) for path in RAW_FIXTURES + EVENT_FIXTURES}
        covered.add(FIXTURES_DIR / "ingest" / "ingest_batch_001.json")

        present = {
            path for path in FIXTURES_DIR.rglob("*.json")
        }
        assert present == covered, f"не покрыты тестами: {sorted(p.name for p in present - covered)}"


class TestPydanticAgreesWithJsonSchema:
    """Главная проверка §51: две реализации контракта не разошлись."""

    @pytest.mark.parametrize("path", RAW_FIXTURES)
    def test_verdicts_match(self, path: tuple[str, str], schemas: dict[str, Any], registry: Registry) -> None:
        instance = load_fixture(*path)

        schema_ok = not json_schema_errors(schemas["raw-event.schema.json"], instance, registry)
        try:
            RawEventIn.model_validate(instance)
            pydantic_ok = True
        except ValidationError:
            pydantic_ok = False

        assert schema_ok == pydantic_ok, (
            f"{path[-1]}: JSON Schema говорит {'ок' if schema_ok else 'отказ'}, "
            f"Pydantic — {'ок' if pydantic_ok else 'отказ'}"
        )

    def test_batch_verdicts_match_on_empty_events(self, schemas: dict[str, Any], registry: Registry) -> None:
        empty_batch = {"schema_version": SUPPORTED_SCHEMA_VERSION, "events": []}

        schema_ok = not json_schema_errors(schemas["ingest-batch.schema.json"], empty_batch, registry)
        try:
            IngestBatchIn.model_validate(empty_batch)
            pydantic_ok = True
        except ValidationError:
            pydantic_ok = False

        assert schema_ok is False
        assert pydantic_ok is False

    def test_batch_verdicts_match_on_oversized_events(self, schemas: dict[str, Any], registry: Registry) -> None:
        oversized = {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "events": [load_fixture("windows", "process_start_001.json")] * 1001,
        }

        schema_ok = not json_schema_errors(schemas["ingest-batch.schema.json"], oversized, registry)
        try:
            IngestBatchIn.model_validate(oversized)
            pydantic_ok = True
        except ValidationError:
            pydantic_ok = False

        assert schema_ok is False
        assert pydantic_ok is False

    def test_batch_fixture_accepted_by_pydantic(self) -> None:
        IngestBatchIn.model_validate(load_fixture("ingest", "ingest_batch_001.json"))

    def test_extra_field_rejected_by_both(self, schemas: dict[str, Any], registry: Registry) -> None:
        instance = {**load_fixture("windows", "process_start_001.json"), "unexpected": 1}

        assert json_schema_errors(schemas["raw-event.schema.json"], instance, registry)
        with pytest.raises(ValidationError):
            RawEventIn.model_validate(instance)


class TestGeneratedIdentifiersMatchContract:
    """Идентификаторы, которые выдаёт домен, обязаны удовлетворять контракту.

    Это защита от расхождения алфавита ULID между реализацией и схемой: если
    они разойдутся, Core начнёт отвергать собственные идентификаторы.
    """

    def test_raw_event_ids(self, schemas: dict[str, Any]) -> None:
        pattern = schemas["raw-event.schema.json"]["$defs"]["raw_event_id"]["pattern"]
        for _ in range(200):
            assert re.match(pattern, new_raw_event_id())

    @pytest.mark.parametrize("field", ["host_id", "boot_id"])
    def test_host_and_boot_ids(self, schemas: dict[str, Any], field: str) -> None:
        pattern = schemas["raw-event.schema.json"]["$defs"][field]["pattern"]
        generator = new_host_id if field == "host_id" else new_boot_id
        for _ in range(100):
            assert re.match(pattern, generator())

    def test_event_id(self, schemas: dict[str, Any]) -> None:
        pattern = schemas["event.schema.json"]["$defs"]["event_id"]["pattern"]
        for _ in range(100):
            assert re.match(pattern, new_event_id())

    def test_process_instance_id(self, schemas: dict[str, Any]) -> None:
        pattern = schemas["event.schema.json"]["$defs"]["entity_ref"]["properties"]["id"]["pattern"]
        from datetime import UTC, datetime

        for pid in range(1, 50):
            instance_id = process_instance_id(
                host_id=new_host_id(),
                boot_id=new_boot_id(),
                pid=pid,
                started_at=datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC),
            )
            assert re.match(pattern, instance_id)


class TestApiResponsesMatchContract:
    """Ответы API обязаны проходить ту же схему, что и фикстуры (§13)."""

    @pytest.mark.parametrize("path", EVENT_FIXTURES)
    def test_event_out_serializes_to_valid_contract(
        self, path: tuple[str, str], schemas: dict[str, Any], registry: Registry
    ) -> None:
        event = event_from_contract(load_fixture(*path))
        payload = EventOut.from_domain(event).model_dump(mode="json")

        errors = json_schema_errors(schemas["event.schema.json"], payload, registry)
        assert errors == [], errors

    def test_timestamps_use_z_suffix(self) -> None:
        """Схема требует суффикс Z, а не смещение +00:00."""
        event = event_from_contract(load_fixture("events", "process_started_001.json"))
        payload = EventOut.from_domain(event).model_dump(mode="json")

        assert payload["timestamp"].endswith("Z")
        assert payload["observed_at"].endswith("Z")
        assert "+00:00" not in json.dumps(payload)

    def test_normalizer_output_matches_committed_fixture(self) -> None:
        """Нормализатор должен воспроизводить закоммиченную фикстуру.

        Сравниваются детерминированные части: идентификатор события выдаётся
        случайно и потому сравниваться не может, а ``subject.id`` выводится из
        данных и обязан совпадать в точности.
        """
        from chronoscope.domain.events.entity_ref import EntityRef
        from chronoscope.normalization.registry import NormalizationContext, default_registry

        expected = load_fixture("events", "process_started_001.json")
        raw = raw_event_from_contract(load_fixture("windows", "process_start_001.json"))

        # Подставляем родителя ровно таким, каким он был бы найден среди
        # сохранённых событий: идентификатор выведен из его времени старта,
        # имя взято из его собственного события старта.
        context = NormalizationContext(
            find_process_instance=lambda *, boot_id, pid, before: EntityRef(
                "process", expected["actor"]["id"], expected["actor"]["name"]
            )
        )
        produced = EventOut.from_domain(default_registry().normalize(raw, context)).model_dump(mode="json")

        assert produced["subject"] == expected["subject"]
        assert produced["actor"] == expected["actor"]
        assert produced["type"] == expected["type"]
        assert produced["timestamp"] == expected["timestamp"]
        assert produced["attributes"] == expected["attributes"]


class TestDomainAcceptsContractFixtures:
    @pytest.mark.parametrize("path", RAW_FIXTURES[:3])
    def test_raw_event_constructed_from_fixture(self, path: tuple[str, str]) -> None:
        raw = raw_event_from_contract(load_fixture(*path))
        assert isinstance(raw, RawEvent)
        assert raw.schema_version == SUPPORTED_SCHEMA_VERSION

    @pytest.mark.parametrize("path", EVENT_FIXTURES)
    def test_event_constructed_from_fixture(self, path: tuple[str, str]) -> None:
        assert isinstance(event_from_contract(load_fixture(*path)), Event)
