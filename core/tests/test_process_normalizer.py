"""Тесты нормализатора процессов (§13, §14, §20).

Ключевые проверки — не «поля переложились», а два обещания модели:

1. **Детерминированность идентичности.** Старт и выход одного процесса дают
   один и тот же ``subject.id`` — на этом держится связь событий во времени.
2. **Честность ``actor``.** Родитель подставляется только когда он реально
   найден среди уже сохранённых событий; иначе ``actor`` пуст, а факт неудачи
   виден в атрибутах.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from chronoscope.domain.errors import InvalidInputError, NormalizationError
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.events.event_type import (
    COLLECTOR_WINDOWS_PROCESS,
    PAYLOAD_PROCESS_EXIT,
    PAYLOAD_PROCESS_START,
    PROCESS_EXITED,
    PROCESS_STARTED,
)
from chronoscope.normalization.registry import (
    NormalizationContext,
    NormalizerRegistry,
    default_registry,
    no_process_lookup,
)
from chronoscope.normalization.windows.process_normalizer import (
    ATTRIBUTE_PARENT_RESOLVED,
    normalize_process_exit,
    normalize_process_start,
)
from tests.conftest import load_fixture
from tests.factories import PARENT_PROC_ID, PROC_ID, raw_event_from_contract


class SpyLookup:
    """Подмена поиска родителя: тесты нормализатора не должны трогать БД."""

    def __init__(self, result: EntityRef | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, boot_id: str | None, pid: int, before: datetime) -> EntityRef | None:
        self.calls.append({"boot_id": boot_id, "pid": pid, "before": before})
        return self.result


def context(lookup: SpyLookup | None = None) -> NormalizationContext:
    return NormalizationContext(find_process_instance=lookup or no_process_lookup)


class TestProcessStart:
    def test_maps_to_process_started(self, process_start_payload: dict[str, Any]) -> None:
        raw = raw_event_from_contract(process_start_payload)
        event = normalize_process_start(raw, context())

        assert event.type == PROCESS_STARTED
        assert event.source == COLLECTOR_WINDOWS_PROCESS
        assert event.raw_event_id == raw.raw_event_id
        assert event.schema_version == raw.schema_version
        assert event.observed_at == raw.observed_at

    def test_subject_id_matches_contract_fixture(self, process_start_payload: dict[str, Any]) -> None:
        """Идентификатор выведен из данных, а не придуман — и совпадает с фикстурой."""
        expected = load_fixture("events", "process_started_001.json")

        raw = raw_event_from_contract(process_start_payload)
        event = normalize_process_start(raw, context())

        assert event.subject is not None
        assert event.subject.id == expected["subject"]["id"] == PROC_ID
        assert event.subject.name == "notepad.exe"

    def test_timestamp_is_process_start_time(self, process_start_payload: dict[str, Any]) -> None:
        raw = raw_event_from_contract(process_start_payload)
        event = normalize_process_start(raw, context())
        assert event.timestamp == datetime(2026, 9, 18, 10, 42, 15, 220_000, tzinfo=UTC)

    def test_renames_path_to_executable(self, process_start_payload: dict[str, Any]) -> None:
        """Payload источника говорит ``path``, контракт события — ``executable``."""
        raw = raw_event_from_contract(process_start_payload)
        event = normalize_process_start(raw, context())

        assert event.attributes["executable"] == "C:\\Windows\\System32\\notepad.exe"
        assert "path" not in event.attributes
        assert event.attributes["command_line"] == "notepad.exe"
        assert event.attributes["user_sid"].startswith("S-1-5-21-")
        assert event.attributes["pid"] == 9812
        assert event.attributes["parent_pid"] == 4312
        assert event.attributes["process_started_at"] == "2026-09-18T10:42:15.220Z"

    def test_missing_optional_fields_are_omitted(self, process_start_missing_path_payload: dict[str, Any]) -> None:
        """§60: неполное событие не должно ломать нормализацию."""
        raw = raw_event_from_contract(process_start_missing_path_payload)
        event = normalize_process_start(raw, context())

        assert "executable" not in event.attributes
        assert "command_line" not in event.attributes
        assert "user_sid" not in event.attributes
        assert event.attributes["pid"] == 1234

    def test_falls_back_to_source_timestamp_when_start_time_absent(
        self, process_start_missing_path_payload: dict[str, Any]
    ) -> None:
        raw = raw_event_from_contract(process_start_missing_path_payload)
        event = normalize_process_start(raw, context())
        assert event.timestamp == raw.source_timestamp

    def test_missing_name_is_allowed(self, process_start_payload: dict[str, Any]) -> None:
        payload = dict(process_start_payload)
        payload["payload"] = {k: v for k, v in process_start_payload["payload"].items() if k != "name"}
        raw = raw_event_from_contract(payload)

        event = normalize_process_start(raw, context())
        assert event.subject is not None
        assert event.subject.name is None

    def test_missing_pid_fails_normalization(self, process_start_payload: dict[str, Any]) -> None:
        payload = dict(process_start_payload)
        payload["payload"] = {k: v for k, v in process_start_payload["payload"].items() if k != "pid"}
        raw = raw_event_from_contract(payload)

        with pytest.raises(NormalizationError, match="pid"):
            normalize_process_start(raw, context())

    def test_non_integer_pid_surfaces_input_error(self, process_start_payload: dict[str, Any]) -> None:
        payload = dict(process_start_payload)
        payload["payload"] = {**process_start_payload["payload"], "pid": "9812"}
        raw = raw_event_from_contract(payload)

        with pytest.raises(InvalidInputError):
            normalize_process_start(raw, context())


class TestProcessExit:
    def test_maps_to_process_exited(self, process_exit_payload: dict[str, Any]) -> None:
        raw = raw_event_from_contract(process_exit_payload)
        event = normalize_process_exit(raw, context())

        assert event.type == PROCESS_EXITED
        assert event.attributes["exit_code"] == 0

    def test_timestamp_is_exit_time(self, process_exit_payload: dict[str, Any]) -> None:
        raw = raw_event_from_contract(process_exit_payload)
        event = normalize_process_exit(raw, context())
        assert event.timestamp == datetime(2026, 9, 18, 10, 44, 3, 114_000, tzinfo=UTC)

    def test_start_and_exit_share_subject_identity(self) -> None:
        """Главное требование: разные события — один экземпляр процесса (§14)."""
        start_raw = raw_event_from_contract(load_fixture("windows", "process_start_001.json"))
        exit_raw = raw_event_from_contract(load_fixture("windows", "process_exit_001.json"))

        start_event = normalize_process_start(start_raw, context())
        exit_event = normalize_process_exit(exit_raw, context())

        assert start_event.subject is not None and exit_event.subject is not None
        assert start_event.subject.id == exit_event.subject.id

        expected = load_fixture("events", "process_exited_001.json")
        assert exit_event.subject.id == expected["subject"]["id"]

    def test_missing_exit_time_falls_back(self, process_exit_payload: dict[str, Any]) -> None:
        payload = dict(process_exit_payload)
        payload["payload"] = {
            k: v for k, v in process_exit_payload["payload"].items() if k != "exited_at"
        }
        raw = raw_event_from_contract(payload)

        event = normalize_process_exit(raw, context())
        assert event.timestamp == raw.best_timestamp


class TestParentResolution:
    def test_actor_set_when_parent_found(self, process_start_payload: dict[str, Any]) -> None:
        expected = load_fixture("events", "process_started_001.json")
        lookup = SpyLookup(EntityRef("process", expected["actor"]["id"], "explorer.exe"))
        raw = raw_event_from_contract(process_start_payload)

        event = normalize_process_start(raw, context(lookup))

        assert event.actor is not None
        assert event.actor.id == expected["actor"]["id"] == PARENT_PROC_ID
        assert event.actor.type == "process"
        # Имя родителя известно из его собственного события старта, а не
        # выдумано: в payload потомка приходит только parent_pid.
        assert event.actor.name == "explorer.exe"
        assert event.attributes[ATTRIBUTE_PARENT_RESOLVED] is True

    def test_actor_empty_when_parent_not_found(self, process_start_payload: dict[str, Any]) -> None:
        """Типичный случай 0.0.1: explorer.exe стартовал до начала наблюдения."""
        lookup = SpyLookup(None)
        raw = raw_event_from_contract(process_start_payload)

        event = normalize_process_start(raw, context(lookup))

        assert event.actor is None
        assert event.attributes[ATTRIBUTE_PARENT_RESOLVED] is False
        # PID родителя сохраняется: он остаётся фактом, даже если связь не построена.
        assert event.attributes["parent_pid"] == 4312

    def test_lookup_receives_boot_id_pid_and_time_bound(self, process_start_payload: dict[str, Any]) -> None:
        lookup = SpyLookup(None)
        raw = raw_event_from_contract(process_start_payload)

        normalize_process_start(raw, context(lookup))

        assert len(lookup.calls) == 1
        assert lookup.calls[0]["boot_id"] == raw.boot_id
        assert lookup.calls[0]["pid"] == 4312
        assert lookup.calls[0]["before"] == raw.best_timestamp

    def test_no_lookup_without_parent_pid(self, process_start_payload: dict[str, Any]) -> None:
        payload = dict(process_start_payload)
        payload["payload"] = {
            k: v for k, v in process_start_payload["payload"].items() if k != "parent_pid"
        }
        lookup = SpyLookup(None)
        raw = raw_event_from_contract(payload)

        event = normalize_process_start(raw, context(lookup))

        assert lookup.calls == []
        assert event.actor is None
        assert ATTRIBUTE_PARENT_RESOLVED not in event.attributes

    def test_lookup_receives_none_boot_id_instead_of_invented_value(self) -> None:
        """Нормализатор не выдумывает boot session, а передаёт отсутствие дальше.

        Решение о том, что без boot session родителя искать нельзя, принимает
        репозиторий — это его предметная область (PID без boot session может
        принадлежать прошлой загрузке).
        """
        payload = {**load_fixture("windows", "process_start_001.json"), "boot_id": None}
        raw = raw_event_from_contract(payload)
        lookup = SpyLookup(None)

        normalize_process_start(raw, context(lookup))

        assert len(lookup.calls) == 1
        assert lookup.calls[0]["boot_id"] is None


class TestNormalizerRegistry:
    def test_default_registry_knows_process_payloads(self) -> None:
        registry = default_registry()
        assert registry.knows(COLLECTOR_WINDOWS_PROCESS, PAYLOAD_PROCESS_START)
        assert registry.knows(COLLECTOR_WINDOWS_PROCESS, PAYLOAD_PROCESS_EXIT)

    def test_unknown_payload_raises(self, process_start_payload: dict[str, Any]) -> None:
        """Agent новее Core: события такого типа Core ещё не умеет разбирать."""
        registry = default_registry()
        payload = {
            **process_start_payload,
            "collector": "plugin.steam",
            "payload_type": "game_started",
        }
        raw = raw_event_from_contract(payload)

        with pytest.raises(NormalizationError, match="нет нормализатора"):
            registry.normalize(raw, context())

    def test_registry_dispatches(self, process_exit_payload: dict[str, Any]) -> None:
        registry = default_registry()
        raw = raw_event_from_contract(process_exit_payload)

        event = registry.normalize(raw, context())
        assert event.type == PROCESS_EXITED

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = NormalizerRegistry()
        registry.register(
            collector=COLLECTOR_WINDOWS_PROCESS,
            payload_type=PAYLOAD_PROCESS_START,
            handler=normalize_process_start,
        )
        with pytest.raises(NormalizationError, match="уже зарегистрирован"):
            registry.register(
                collector=COLLECTOR_WINDOWS_PROCESS,
                payload_type=PAYLOAD_PROCESS_START,
                handler=normalize_process_start,
            )

    def test_resolve_returns_none_for_unknown(self) -> None:
        assert default_registry().resolve("plugin.steam", "game_started") is None

    def test_registered_pairs_are_reported(self) -> None:
        assert default_registry().registered == {
            (COLLECTOR_WINDOWS_PROCESS, PAYLOAD_PROCESS_START),
            (COLLECTOR_WINDOWS_PROCESS, PAYLOAD_PROCESS_EXIT),
        }


class TestImmutability:
    def test_produced_event_is_frozen(self, process_start_payload: dict[str, Any]) -> None:
        """§21: историческое событие неизменяемо."""
        raw = raw_event_from_contract(process_start_payload)
        event = normalize_process_start(raw, context())

        with pytest.raises((AttributeError, TypeError)):
            event.type = "process.exited"  # type: ignore[misc]
