"""Use case ``GetProcessDetail`` — один экземпляр процесса (§14, §77.1).

Возвращает ``None``, если об экземпляре ничего не известно: отсутствие данных —
нормальный результат запроса, а решение о том, что это 404, принимает
транспортный слой (так же устроен ``GetEvent``).

Ленты событий здесь нет намеренно. Detail отвечает на вопрос «что это за
экземпляр процесса», а «что с ним происходило» отвечает ``GET /api/v1/events``
с фильтром по ``subject_id`` (§31, §32). Второй способ получать те же данные
означал бы вторую реализацию пагинации, фильтров и порядка сортировки — и
расхождение между ними рано или поздно.
"""

from __future__ import annotations

from chronoscope.application.ports import EventRepositoryPort
from chronoscope.application.queries.process_detail import (
    ProcessDetail,
    build_process_detail,
)
from chronoscope.domain.errors import HistoryLimitExceededError

#: Предел числа событий, по которым строится detail.
#:
#: Жизнь одного экземпляра в текущем коллекторе — это два события,
#: ``process.started`` и ``process.exited``. Предел нужен не для экономии, а
#: чтобы чтение detail не зависело от того, сколько событий кто-то записал под
#: одним ``subject_id``.
PROCESS_EVENTS_LIMIT = 1000


class GetProcessDetail:
    def __init__(self, event_repository: EventRepositoryPort) -> None:
        self._event_repository = event_repository

    def execute(self, process_instance_id: str) -> ProcessDetail | None:
        """Собрать detail экземпляра или вернуть ``None``, если событий нет.

        Событий запрашивается на одно больше предела: так отличие «история
        ровно в предел» от «история длиннее предела» видно без второго запроса.
        """
        events = self._event_repository.find_instance_events(
            process_instance_id,
            limit=PROCESS_EVENTS_LIMIT + 1,
        )

        if not events:
            return None

        if len(events) > PROCESS_EVENTS_LIMIT:
            # По неполной истории detail сообщил бы `observed_exit: false`
            # о процессе, чьё завершение просто не попало в выборку. Отказ
            # честнее усечения — то же правило, что и в самой модели.
            raise HistoryLimitExceededError(
                process_instance_id=process_instance_id,
                limit=PROCESS_EVENTS_LIMIT,
            )

        return build_process_detail(process_instance_id, events)
