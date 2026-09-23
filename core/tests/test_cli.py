"""Тесты CLI (§57).

Разделены на две части. Модульные проверяют форматирование — то есть то, что
превращает данные API в читаемый вывод, и где легче всего незаметно ошибиться
(разделители разрядов, местное время, отсутствующие поля). Интеграционные
запускают CLI против **настоящего** Core по HTTP: `urllib` из CLI нельзя
подменить TestClient'ом, а проверять клиент без сервера означало бы проверять
не то, что запускается у пользователя.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn

from chronoscope.cli import rendering
from chronoscope.cli.main import main
from chronoscope.infrastructure.config.settings import CoreSettings
from chronoscope.main import create_app
from tests.conftest import load_fixture
from tests.factories import PROC_ID

API = "/api/v1"


# ── Форматирование ───────────────────────────────────────────────────


class TestRendering:
    def test_count_uses_space_separator(self) -> None:
        """Разделитель — пробел, а не запятая: в русской типографике это разряды."""
        assert rendering.format_count(12419) == "12 419"
        assert rendering.format_count(2) == "2"
        assert rendering.format_count(0) == "0"

    def test_count_survives_missing_value(self) -> None:
        assert rendering.format_count(None) == rendering.NO_VALUE
        assert rendering.format_count(True) == rendering.NO_VALUE

    def test_size_is_human_readable(self) -> None:
        assert rendering.format_size(512) == "512 Б"
        assert rendering.format_size(1024) == "1.0 КБ"
        assert rendering.format_size(19084083) == "18.2 МБ"
        assert rendering.format_size(None) == rendering.NO_VALUE

    def test_timestamp_moves_to_local_time(self) -> None:
        """§24: в хранилище UTC, местное время — забота вывода, то есть этого слоя."""
        rendered = rendering.format_timestamp("2026-09-18T10:42:15.220Z")

        expected = datetime(2026, 9, 18, 10, 42, 15, tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        assert rendered == expected

    def test_timestamp_survives_broken_value(self) -> None:
        assert rendering.format_timestamp(None) == rendering.NO_VALUE
        assert rendering.format_timestamp("не время") == "не время"

    def test_events_table_has_identifier_column(self) -> None:
        """Без колонки с идентификатором `event <id>` нечем запустить."""
        table = rendering.render_events(
            [
                {
                    "id": "evt_01K5R8Z9M8R1X6Y0Z5D7F9H1K3",
                    "timestamp": "2026-09-18T10:42:15.220Z",
                    "type": "process.started",
                    "subject": {"name": "notepad.exe", "id": "proc_01M2T1R61M8W1EMS4WBMRDKSTZ"},
                }
            ]
        )

        assert "Идентификатор" in table
        assert "evt_01K5R8Z9M8R1X6Y0Z5D7F9H1K3" in table
        assert "process.started" in table
        assert "notepad.exe" in table

    def test_events_table_falls_back_to_identifier_without_name(self) -> None:
        table = rendering.render_events(
            [{"id": "evt_01K5R8Z9M8R1X6Y0Z5D7F9H1K3", "type": "process.started", "subject": {"id": "proc_X"}}]
        )

        assert "proc_X" in table

    def test_empty_events_say_so(self) -> None:
        assert rendering.render_events([]) == "Событий нет."

    def test_missing_subject_is_not_blank(self) -> None:
        table = rendering.render_events([{"id": "evt_X", "type": "system.booted"}])

        assert rendering.NO_VALUE in table

    def test_kv_alignment(self) -> None:
        """Проверяется свойство выравнивания, а не посчитанная вручную строка.

        Ожидаемая строка с точным числом пробелов ломалась бы от любого изменения
        разделителя, ничего не сообщая о сути; здесь же проверяется то, ради чего
        функция существует, — что значения начинаются в одной колонке.
        """
        lines = rendering.render_kv([("Короткое", "1"), ("Значительно длиннее", "2")]).splitlines()

        assert lines[0].startswith("Короткое")
        assert lines[1].startswith("Значительно длиннее")
        assert lines[0].index("1") == lines[1].index("2")

    def test_duration_is_human_readable(self) -> None:
        """1 мин 47.894 с показывается как «1 мин 47 с»: дробь отбрасывается, не округляется вверх."""
        assert rendering.format_duration(250) == "250 мс"
        assert rendering.format_duration(5490) == "5.5 с"
        assert rendering.format_duration(107894) == "1 мин 47 с"
        assert rendering.format_duration(7_200_000) == "2 ч 0 мин"

    def test_duration_survives_missing_value(self) -> None:
        """`null` означает «известно только одно из времён» — и остаётся пустым."""
        assert rendering.format_duration(None) == rendering.NO_VALUE
        assert rendering.format_duration(True) == rendering.NO_VALUE
        assert rendering.format_duration(-1) == rendering.NO_VALUE

    def test_observed_flag_is_not_shown_as_boolean(self) -> None:
        """Разница «время известно» и «событие наблюдалось» — то, ради чего строка есть."""
        assert rendering.format_observed(True) == "да"
        assert rendering.format_observed(False) == "нет"
        assert rendering.format_observed(None) == rendering.NO_VALUE


# ── Интеграция с настоящим Core ──────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def running_core(migrated_database: CoreSettings) -> str:
    """Поднять настоящий Core по HTTP на свободном порту.

    Тесты Core до сих пор обходились TestClient'ом, то есть проверяли приложение
    в процессе. CLI работает через `urllib`, и подменить его TestClient'ом
    нельзя, поэтому здесь поднимается настоящий сервер — иначе проверялся бы не
    тот путь, которым пользуется человек.
    """
    app = create_app(migrated_database)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning"))

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Core не поднялся за 20 секунд")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=20)


@pytest.fixture
def populated_core(running_core: str) -> str:
    """Core с событиями из фикстуры, то есть с чем-то, что можно показать."""
    batch = load_fixture("ingest", "ingest_batch_001.json")
    response = httpx.post(f"{running_core}{API}/ingest/raw-events", json=batch, timeout=10)
    assert response.status_code == 200
    return running_core


class TestCliAgainstRealCore:
    def test_status_reports_core_and_data(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--core-url", populated_core, "status"]) == 0

        out = capsys.readouterr().out
        assert "Chronoscope Core" in out
        assert "работает" in out
        assert "Событий всего" in out
        assert "2" in out

    def test_status_does_not_invent_agent_state(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        """§57 показывает «connected», но Core не отслеживает Agent (§31).

        Выдуманное «подключён» было бы единственным местом продукта, где
        состояние придумано, а не измерено, поэтому CLI сообщает как есть.
        """
        main(["--core-url", populated_core, "status"])

        out = capsys.readouterr().out
        assert "не отслеживается" in out
        assert "подключён" not in out

    def test_events_lists_both_fixture_events(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--core-url", populated_core, "events"]) == 0

        out = capsys.readouterr().out
        assert "process.started" in out
        assert "process.exited" in out
        assert "notepad.exe" in out
        assert "evt_" in out

    def test_events_filter_by_type(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--core-url", populated_core, "events", "--type", "process.started"]) == 0

        out = capsys.readouterr().out
        assert "process.started" in out
        assert "process.exited" not in out

    def test_events_time_filter_accepts_offset(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        """Смещение в параметре допустимо и пересчитывается в UTC (§24)."""
        assert main(
            [
                "--core-url",
                populated_core,
                "events",
                "--from",
                "2026-09-18T15:43:00+05:00",
                "--to",
                "2026-09-18T15:45:00+05:00",
            ]
        ) == 0

        out = capsys.readouterr().out
        assert "process.exited" in out
        assert "process.started" not in out

    def test_event_shows_details_and_attributes(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        listed = httpx.get(f"{populated_core}{API}/events", params={"limit": 1}, timeout=10).json()
        event_id = listed["events"][0]["id"]

        assert main(["--core-url", populated_core, "event", event_id]) == 0

        out = capsys.readouterr().out
        assert event_id in out
        assert "Атрибуты:" in out
        assert "pid" in out
        assert "Сырое событие" in out

    def test_process_shows_lifecycle(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        """Экземпляр процесса целиком: времена, признаки наблюдения, длительность."""
        assert main(["--core-url", populated_core, "process", PROC_ID]) == 0

        out = capsys.readouterr().out
        assert PROC_ID in out
        assert "notepad.exe" in out
        assert "Запуск" in out
        assert "Наблюдался запуск  да" in out
        assert "Наблюдался выход   да" in out
        assert "1 мин 47 с" in out
        assert "PID родителя" in out

    def test_process_points_to_its_events(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        """Ленты в detail нет — команда показывает, чем её получить (§31)."""
        main(["--core-url", populated_core, "process", PROC_ID])

        out = capsys.readouterr().out
        assert f"chronoscope events --subject-id {PROC_ID}" in out

    def test_process_reports_unresolved_parent(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        """PID родителя известен, идентичность — нет: это разные факты (§20)."""
        main(["--core-url", populated_core, "process", PROC_ID])

        out = capsys.readouterr().out
        assert "4312" in out
        assert "Связь с родителем не установлена" in out

    def test_process_without_observed_exit_does_not_claim_running(
        self, running_core: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Отсутствие выхода — не «процесс работает»: Chronoscope его не наблюдал.

        Это ровно то место, где продукт легко начинает выдумывать состояние, и
        проверяется оно на живом Core, а не в разметке ответа.
        """
        response = httpx.post(
            f"{running_core}{API}/ingest/raw-events",
            json={"schema_version": 1, "events": [load_fixture("windows", "process_start_001.json")]},
            timeout=10,
        )
        assert response.status_code == 200

        assert main(["--core-url", running_core, "process", PROC_ID]) == 0

        out = capsys.readouterr().out
        assert "Наблюдался выход   нет" in out
        assert "не значит, что процесс работает" in out
        assert "работает" not in out.replace("не значит, что процесс работает", "")

    def test_doctor_reports_ok(self, populated_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--core-url", populated_core, "doctor"]) == 0

        out = capsys.readouterr().out
        assert "Core отвечает" in out
        assert "Схема базы" in out
        assert "Версии совпадают" in out

    def test_doctor_fails_when_schema_is_missing(self, temp_settings: CoreSettings, capsys: pytest.CaptureFixture[str]) -> None:
        """Без применённой схемы `doctor` обязан не только сказать об этом, но и подсказать команду."""
        app = create_app(temp_settings)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Core не поднялся")
            time.sleep(0.05)

        try:
            assert main(["--core-url", f"http://127.0.0.1:{port}", "doctor"]) == 1

            out = capsys.readouterr().out
            assert "схема не применена" in out
            assert "alembic upgrade head" in out
        finally:
            server.should_exit = True
            thread.join(timeout=20)


class TestCliFailures:
    def test_unreachable_core_is_reported_with_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Core не запущен — самая частая причина, и CLI обязан объяснить её, а не молчать."""
        port = _free_port()

        assert main(["--core-url", f"http://127.0.0.1:{port}", "status"]) == 2

        captured = capsys.readouterr()
        assert "Core не отвечает" in captured.err
        assert "python -m chronoscope" in captured.err

    def test_unknown_event_is_reported_with_code(self, running_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--core-url", running_core, "event", "evt_01K5R8Z9M8R1X6Y0Z5D7F9H1K3"]) == 1

        captured = capsys.readouterr()
        assert "not_found" in captured.err
        assert "не найдено" in captured.err

    def test_invalid_limit_is_reported_with_code(self, running_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--core-url", running_core, "events", "--limit", "0"]) == 1

        captured = capsys.readouterr()
        assert "invalid_input" in captured.err

    def test_unknown_process_is_reported_with_code(self, running_core: str, capsys: pytest.CaptureFixture[str]) -> None:
        """Идентификатор правильной формы, но такого экземпляра нет — это not_found."""
        assert main(["--core-url", running_core, "process", "proc_01K5R8Z9M0A1B2C3D4E5F6G7H8"]) == 1

        captured = capsys.readouterr()
        assert "not_found" in captured.err
        assert "не найден" in captured.err

    def test_malformed_process_id_is_reported_with_code(
        self, running_core: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Опечатка в префиксе — неверный запрос, а не отсутствие данных."""
        assert main(["--core-url", running_core, "process", "pid-9812"]) == 1

        captured = capsys.readouterr()
        assert "invalid_input" in captured.err

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        from chronoscope import __version__

        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])

        assert exit_info.value.code == 0
        assert __version__ in capsys.readouterr().out
