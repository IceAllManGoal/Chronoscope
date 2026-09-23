"""Точка входа CLI (§57).

Команды: `status`, `events`, `event <id>`, `doctor`.

CLI существует потому, что в ранних версиях он полезнее GUI (§57), и потому что
до появления страницы единственным способом увидеть историю был `curl`. Команды
намеренно тонкие: вся работа уже сделана API Core, а CLI отвечает за то, чтобы
результат был читаемым, а ошибки — понятными.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from chronoscope import __version__
from chronoscope.cli import rendering
from chronoscope.cli.client import CliError, CoreClient, CoreRejected, CoreUnavailable, resolve_base_url
from chronoscope.infrastructure.config.settings import ConfigurationError

PROGRAM = "chronoscope"

#: Размер страницы по умолчанию для `events`. Совпадает с умолчанием API (§32).
DEFAULT_LIMIT = 20

#: Подпись состояния, когда данных нет. Не «unknown» и не «connected»: Core не
#: отслеживает Agent в этой версии (§31), и выдуманное «connected» было бы
#: единственным местом продукта, где состояние придумано, а не измерено.
NOT_TRACKED = "не отслеживается"


@dataclass(frozen=True, slots=True)
class Check:
    """Результат одной проверки `doctor`."""

    name: str
    state: str
    detail: str
    hint: str | None = None

    @property
    def failed(self) -> bool:
        return self.state == "ошибка"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Chronoscope — история компьютера, которую можно исследовать.",
        epilog="Адрес Core берётся из того же файла конфигурации, что читает сам Core (§36).",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument(
        "--core-url",
        default=None,
        help="адрес Core, например http://127.0.0.1:7342 (по умолчанию из конфигурации)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="таймаут обращения к Core в секундах (по умолчанию 5)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<команда>")

    subparsers.add_parser("status", help="состояние Core, базы и объёмы данных")

    events = subparsers.add_parser("events", help="список событий с фильтрами и страницами")
    events.add_argument("--type", dest="types", default=None, help="тип события; несколько — через запятую")
    events.add_argument("--source", default=None, help="источник, например windows.process")
    events.add_argument("--actor-id", dest="actor_id", default=None, help="идентификатор актора")
    events.add_argument("--subject-id", dest="subject_id", default=None, help="идентификатор субъекта")
    events.add_argument("--from", dest="time_from", default=None, help="начало периода, включительно (ISO 8601)")
    events.add_argument("--to", dest="time_to", default=None, help="конец периода, включительно (ISO 8601)")
    events.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"размер страницы (по умолчанию {DEFAULT_LIMIT})")
    events.add_argument("--cursor", default=None, help="курсор следующей страницы из предыдущего вывода")

    detail = subparsers.add_parser("event", help="одно событие по идентификатору")
    detail.add_argument("event_id", metavar="<id>", help="идентификатор вида evt_<ULID>")

    process = subparsers.add_parser("process", help="экземпляр процесса по идентификатору")
    process.add_argument(
        "process_instance_id",
        metavar="<id>",
        help="идентификатор вида proc_<ULID> — он же subject.id в событиях процесса",
    )

    subparsers.add_parser("doctor", help="диагностика: доступность Core, схема, данные")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        base_url = resolve_base_url(args.core_url)
    except ConfigurationError as error:
        print(f"Конфигурация не прочитана: {error}", file=sys.stderr)
        return 1

    client = CoreClient(base_url=base_url, timeout=args.timeout)

    try:
        if args.command == "status":
            return command_status(client)
        if args.command == "events":
            return command_events(client, args)
        if args.command == "event":
            return command_event(client, args.event_id)
        if args.command == "process":
            return command_process(client, args.process_instance_id)
        if args.command == "doctor":
            return command_doctor(client)
    except CoreUnavailable as error:
        print(str(error), file=sys.stderr)
        return 2
    except CoreRejected as error:
        # Код ошибки показывается намеренно: по нему видно, что именно чинить —
        # неподдерживаемая версия схемы и невалидный ввод лечатся по-разному (§60).
        print(f"Core отверг запрос [{error.code}]: {error.message}", file=sys.stderr)
        return 1
    except CliError as error:
        print(str(error), file=sys.stderr)
        return 1

    parser.error(f"неизвестная команда: {args.command}")
    return 1


def command_status(client: CoreClient) -> int:
    health = client.health()
    status = client.status()
    events = status.get("events") or {}
    raw_events = status.get("raw_events") or {}

    print(
        rendering.render_kv(
            [
                ("Chronoscope Core", _core_state(health)),
                ("Chronoscope Agent", _agent_state(status)),
                ("Хранилище", str(health.get("database", rendering.NO_VALUE))),
            ]
        )
    )
    print()
    print(
        rendering.render_kv(
            [
                ("Событий всего", rendering.format_count(events.get("total"))),
                ("За последнюю минуту", rendering.format_count(events.get("last_minute"))),
                ("Сырых событий", rendering.format_count(raw_events.get("total"))),
                ("Размер базы", rendering.format_size(status.get("database_size_bytes"))),
                ("Путь к базе", str(status.get("database_path", rendering.NO_VALUE))),
            ]
        )
    )

    if _agent_state(status) == NOT_TRACKED:
        print()
        print("Agent не отслеживается: Core в этой версии не знает о его подключении (§31).")

    return 0


def command_events(client: CoreClient, args: argparse.Namespace) -> int:
    page = client.events(
        type=args.types,
        source=args.source,
        actor_id=args.actor_id,
        subject_id=args.subject_id,
        **{"from": args.time_from, "to": args.time_to},
        limit=args.limit,
        cursor=args.cursor,
    )

    events = page.get("events") or []
    print(rendering.render_events(events))

    count = page.get("count", len(events))
    next_cursor = page.get("next_cursor")

    print()
    if next_cursor:
        print(f"Показано {rendering.format_count(count)}. Дальше:")
        print(f"  {PROGRAM} events --limit {args.limit} --cursor {next_cursor}")
    else:
        print(f"Показано {rendering.format_count(count)}. Это все события по заданным условиям.")

    return 0


def command_event(client: CoreClient, event_id: str) -> int:
    event = client.event(event_id)

    print(
        rendering.render_kv(
            [
                ("Идентификатор", str(event.get("id", rendering.NO_VALUE))),
                ("Тип", str(event.get("type", rendering.NO_VALUE))),
                ("Время", rendering.format_timestamp(event.get("timestamp"))),
                ("Замечено", rendering.format_timestamp(event.get("observed_at"))),
                ("Источник", str(event.get("source", rendering.NO_VALUE))),
                ("Узел", str(event.get("host_id", rendering.NO_VALUE))),
                ("Загрузка ОС", str(event.get("boot_id") or rendering.NO_VALUE)),
                ("Актор", rendering._entity_label(event.get("actor"))),
                ("Субъект", rendering._entity_label(event.get("subject"))),
                ("Сырое событие", str(event.get("raw_event_id") or rendering.NO_VALUE)),
            ]
        )
    )

    attributes = event.get("attributes") or {}
    print()
    print("Атрибуты:")
    if not attributes:
        print(f"  {rendering.NO_VALUE}")
    else:
        # Значения не форматируются: атрибуты — domain-specific данные, и
        # приводить их к общему виду значило бы терять то, что в них записано.
        for key in sorted(attributes):
            print(f"  {key.ljust(max(len(name) for name in attributes))}  {attributes[key]}")

    return 0


def command_process(client: CoreClient, process_instance_id: str) -> int:
    """Экземпляр процесса как сущность (§77.1).

    Команда только показывает то, что отдал API: никаких вычислений здесь нет.
    Иначе у одной и той же сущности появилось бы второе представление, и
    вопросы вида «почему в CLI не так, как в API» стали бы законными.
    """
    detail = client.process(process_instance_id)

    parent = detail.get("parent") or {}
    parent_label = rendering._entity_label(
        {"id": parent.get("id"), "name": parent.get("name")} if parent.get("id") else None
    )

    print(
        rendering.render_kv(
            [
                ("Идентификатор", str(detail.get("id", rendering.NO_VALUE))),
                ("Имя", str(detail.get("name") or rendering.NO_VALUE)),
                ("PID", str(detail.get("pid", rendering.NO_VALUE))),
                ("Загрузка ОС", str(detail.get("boot_id") or rendering.NO_VALUE)),
                # Время и признак наблюдения — разные строки: событие завершения
                # несёт время старта, поэтому «когда» может быть известно без
                # «наблюдался» (docs/EVENT_MODEL.md §2.4.1).
                ("Запуск", rendering.format_timestamp(detail.get("started_at"))),
                ("Наблюдался запуск", rendering.format_observed(detail.get("observed_start"))),
                ("Завершение", rendering.format_timestamp(detail.get("exited_at"))),
                ("Наблюдался выход", rendering.format_observed(detail.get("observed_exit"))),
                ("Длительность", rendering.format_duration(detail.get("duration_ms"))),
                ("Родитель", parent_label),
                ("PID родителя", str(parent.get("pid", rendering.NO_VALUE))),
            ]
        )
    )

    notes: list[str] = []

    if parent.get("pid") is not None and not parent.get("resolved"):
        # Разные факты: PID приходит от источника, идентичность родителя — из
        # наблюдавшегося события его старта (§20).
        notes.append(
            "Связь с родителем не установлена: событие старта родительского процесса не наблюдалось."
        )

    if detail.get("observed_exit") is False:
        notes.append(
            "Завершение не наблюдалось. Это не значит, что процесс работает: "
            "Chronoscope не видел события выхода, а коллектор может его пропустить (§8.4)."
        )

    if notes:
        print()
        for note in notes:
            print(note)

    print()
    print("События этого экземпляра:")
    print(f"  {PROGRAM} events --subject-id {detail.get('id', process_instance_id)}")

    return 0


def command_doctor(client: CoreClient) -> int:
    checks: list[Check] = []

    health: Mapping[str, Any] | None = None
    try:
        health = client.health()
        checks.append(Check("Core отвечает", "ok", f"версия {health.get('version', rendering.NO_VALUE)}"))
    except CoreUnavailable as error:
        checks.append(Check("Core отвечает", "ошибка", "нет связи", hint=str(error).splitlines()[1].strip()))
        _print_checks(checks)
        return 1

    database_state = str(health.get("database", "unknown"))
    if database_state == "ok":
        checks.append(Check("Схема базы", "ok", "применена"))
    elif database_state == "schema_missing":
        checks.append(
            Check(
                "Схема базы",
                "ошибка",
                "схема не применена",
                hint="cd core && uv run alembic upgrade head",
            )
        )
    else:
        checks.append(Check("Схема базы", "ошибка", f"база недоступна ({database_state})"))

    core_version = str(health.get("version", ""))
    if core_version and core_version != __version__:
        # Расхождение версий CLI и Core означает, что команда может обращаться к
        # API, которого в этом Core нет. Молча это выглядело бы как загадочная
        # ошибка при первой же новой возможности.
        checks.append(
            Check(
                "Версии совпадают",
                "внимание",
                f"CLI {__version__}, Core {core_version}",
                hint="обнови обе части до одной версии",
            )
        )
    else:
        checks.append(Check("Версии совпадают", "ok", __version__))

    try:
        status = client.status()
    except CoreRejected:
        checks.append(Check("Данные", "ошибка", "Core не отдал состояние", hint="см. сообщение команды status"))
        _print_checks(checks)
        return 1

    events = status.get("events") or {}
    raw_events = status.get("raw_events") or {}
    total = events.get("total", 0)
    raw_total = raw_events.get("total", 0)

    checks.append(Check("Хранилище", "ok", f"событий {rendering.format_count(total)}, сырых {rendering.format_count(raw_total)}"))
    checks.append(Check("Размер базы", "ok", rendering.format_size(status.get("database_size_bytes"))))

    if raw_total and not total:
        checks.append(
            Check(
                "Есть нормализованные события",
                "ошибка",
                "сырые события есть, нормализованных нет",
                hint="нормализатор не понял payload — смотри лог Core (§60)",
            )
        )
    elif not raw_total:
        checks.append(
            Check(
                "Данные поступают",
                "внимание",
                "событий пока нет",
                hint="Agent не запущен или ничего не произошло: dotnet run --project agent/src/Chronoscope.Agent.Host",
            )
        )
    else:
        checks.append(Check("Данные поступают", "ok", f"последняя минута: {rendering.format_count(events.get('last_minute'))}"))

    checks.append(Check("Chronoscope Agent", "внимание", NOT_TRACKED, hint="Core не отслеживает Agent в этой версии (§31)"))

    _print_checks(checks)

    return 1 if any(check.failed for check in checks) else 0


def _print_checks(checks: Sequence[Check]) -> None:
    width = max(len(check.name) for check in checks)
    marks = {"ok": "ок", "внимание": "внимание", "ошибка": "ОШИБКА"}

    for check in checks:
        print(f"{check.name.ljust(width)}  {marks[check.state].ljust(8)}  {check.detail}")
        if check.hint:
            print(f"{' ' * width}  {' ' * 8}  → {check.hint}")

    failed = sum(1 for check in checks if check.failed)
    warnings = sum(1 for check in checks if check.state == "внимание")

    print()
    if failed:
        print(f"Проверок с ошибкой: {failed}.")
    elif warnings:
        print(f"Ошибок нет, требуют внимания: {warnings}.")
    else:
        print("Всё в порядке.")


def _core_state(health: Mapping[str, Any]) -> str:
    return "работает" if health.get("status") == "ok" else f"понижено ({health.get('status', rendering.NO_VALUE)})"


def _agent_state(status: Mapping[str, Any]) -> str:
    """Состояние Agent, как его сообщает Core.

    `agent` в ответе `/status` равен `null`, потому что Core не отслеживает
    подключение Agent (§31). Здесь это так и показывается: подставить
    «connected», как в примере §57, значило бы выдать выдумку за измерение.
    """
    agent = status.get("agent")
    if not isinstance(agent, Mapping):
        return NOT_TRACKED

    return "подключён" if agent.get("connected") else "не подключён"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
