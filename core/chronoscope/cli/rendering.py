"""Форматирование вывода CLI.

Здесь собрано то, что относится к человеку, а не к данным: разделители разрядов,
человекочитаемый размер, местное время. §24 прямо говорит, что локальная
timezone — забота UI, а не хранилища, поэтому перевод из UTC в местное время
происходит именно здесь, на границе вывода.

Вывод на русском, хотя пример в §57 приведён на английском. Причина не в
вольности: CLI показывает и сообщения самого Core, а они на русском — например
«ни одно из 2 событий пакета не прошло валидацию». Английские подписи над
русским текстом ошибки читались бы как небрежность. Разделитель разрядов —
пробел, как принято в русской типографике.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

#: Пустое значение, когда данных нет. Одно на весь CLI, чтобы «неизвестно»
#: выглядело одинаково во всех командах.
NO_VALUE = "—"

_SIZE_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ")


def format_count(value: Any) -> str:
    """Число с разделителями разрядов: 12419 -> «12 419».

    Не-число даёт «неизвестно», а не строковое представление: в позиции
    количества нечисловое значение означает, что количество неизвестно, и
    показать там `True` или `None` значило бы выдать отсутствие данных за данные.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return NO_VALUE

    return f"{value:,}".replace(",", " ")


def format_size(size_bytes: Any) -> str:
    """Человекочитаемый размер: 19084083 -> «18.2 МБ»."""
    if not isinstance(size_bytes, (int, float)) or isinstance(size_bytes, bool):
        return NO_VALUE

    size = float(size_bytes)
    unit_index = 0
    while size >= 1024 and unit_index < len(_SIZE_UNITS) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {_SIZE_UNITS[unit_index]}"

    return f"{size:.1f} {_SIZE_UNITS[unit_index]}"


def format_timestamp(value: Any) -> str:
    """Время из UTC в местное.

    Хранилище отдаёт `Z`-время, потому что только UTC и хранится (§24). Показывать
    пользователю UTC было бы издевательством: он сопоставляет события со своими
    действиями по часам на стене.
    """
    if not isinstance(value, str) or not value:
        return NO_VALUE

    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_duration(duration_ms: Any) -> str:
    """Длительность жизни процесса из миллисекунд.

    Пустое значение остаётся пустым: `null` в ответе означает «известно только
    одно из времён», и показывать на его месте «0 с» значило бы утверждать, что
    процесс жил мгновение.
    """
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
        return NO_VALUE

    if duration_ms < 1000:
        return f"{duration_ms} мс"

    seconds = duration_ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} с"

    # Дробная часть отбрасывается, а не округляется вверх: длительность не
    # должна выглядеть больше измеренной.
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} мин {seconds} с"

    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин"


def format_observed(flag: Any) -> str:
    """Ответ на вопрос «наблюдалось ли событие» в терминах CLI.

    Не «true/false»: рядом стоят времена, полученные из данных источника, и
    разница между «время известно» и «событие наблюдалось» — именно то, ради
    чего она показывается отдельной строкой.
    """
    if flag is True:
        return "да"
    if flag is False:
        return "нет"
    return NO_VALUE


def render_kv(rows: Sequence[tuple[str, Any]], *, indent: int = 0, width: int = 0) -> str:
    """Две колонки с выравниванием — форма примера из §57."""
    if not rows:
        return ""

    label_width = width or max(len(label) for label, _ in rows)
    prefix = " " * indent

    return "\n".join(f"{prefix}{label.ljust(label_width)}  {value}" for label, value in rows)


def render_events(events: Iterable[Mapping[str, Any]]) -> str:
    """Таблица событий: время, тип, субъект, идентификатор.

    Колонка с идентификатором события обязательна, хотя список от неё только
    шире: `event <id>` иначе нечем запустить, и переход «список → подробности»,
    ради которого список и существует, становится невозможен.

    Колонка `pid` не показывается намеренно: идентичность экземпляра процесса
    несёт `subject`, а PID переиспользуется ОС и в списке только путает (§14).
    Идентификатор экземпляра виден в `event <id>`, где он и нужен.
    """
    rows = [
        (
            format_timestamp(event.get("timestamp")),
            str(event.get("type", NO_VALUE)),
            _entity_name(event.get("subject")),
            str(event.get("id", NO_VALUE)),
        )
        for event in events
    ]

    if not rows:
        return "Событий нет."

    widths = [max(len(row[index]) for row in rows) for index in range(3)]
    header_labels = ("Время", "Тип", "Субъект")
    header = "  ".join(
        label.ljust(widths[index]) for index, label in enumerate(header_labels)
    ) + "  Идентификатор"
    separator = "-" * len(header)

    lines = [header, separator]
    lines.extend(
        f"{time.ljust(widths[0])}  {kind.ljust(widths[1])}  {subject.ljust(widths[2])}  {identifier}"
        for time, kind, subject, identifier in rows
    )

    return "\n".join(lines)


def _entity_name(entity: Any) -> str:
    """Имя сущности без идентификатора — для табличного вывода.

    Если имени нет, показывается идентификатор: пустая ячейка в списке не
    сообщает ничего, а идентификатор хотя бы позволяет перейти к подробностям.
    """
    if not isinstance(entity, Mapping):
        return NO_VALUE

    name = entity.get("name")
    identifier = entity.get("id")

    if name:
        return str(name)
    if identifier:
        return str(identifier)

    return NO_VALUE


def _entity_label(entity: Any) -> str:
    """Имя сущности, а если его нет — её идентификатор.

    Имя может отсутствовать (`name` в контракте необязательно), но показывать
    пустоту нельзя: без идентификатора событие нечем связать с другими.
    """
    if not isinstance(entity, Mapping):
        return NO_VALUE

    name = entity.get("name")
    identifier = entity.get("id")

    if name and identifier:
        return f"{name} ({identifier})"
    if name:
        return str(name)
    if identifier:
        return str(identifier)

    return NO_VALUE
