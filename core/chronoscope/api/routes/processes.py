"""Эндпоинт экземпляра процесса: GET /api/v1/processes/{process_instance_id} (§31, §77.1).

Ответ на вопрос «что это за экземпляр процесса». Ленты событий здесь нет: «что с
ним происходило» отвечает ``GET /api/v1/events?subject_id=…`` (§31, §32), и
второй способ получать те же данные означал бы вторую реализацию пагинации.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi import status as http_status

from chronoscope.api.dependencies import get_get_process_detail
from chronoscope.api.schemas import ErrorOut, ProcessDetailOut
from chronoscope.application.queries.get_process_detail import GetProcessDetail
from chronoscope.domain.ids import PREFIX_PROCESS_INSTANCE, prefixed_id_pattern

router = APIRouter(tags=["processes"])


@router.get(
    "/processes/{process_instance_id}",
    response_model=ProcessDetailOut,
    responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Экземпляр процесса по идентификатору",
)
def get_process(
    process_instance_id: str = Path(
        description="Идентификатор экземпляра процесса вида proc_<ULID>",
        # Форма идентификатора проверяется здесь, а не только «не найдено»:
        # опечатка в префиксе — это неверный запрос, и ответ «экземпляр не
        # найден» отправил бы искать причину в данных вместо адреса.
        pattern=prefixed_id_pattern(PREFIX_PROCESS_INSTANCE),
    ),
    use_case: GetProcessDetail = Depends(get_get_process_detail),
) -> ProcessDetailOut:
    detail = use_case.execute(process_instance_id)

    if detail is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"экземпляр процесса {process_instance_id!r} не найден",
        )

    return ProcessDetailOut.from_domain(detail)
