"""Эндпоинт экземпляра процесса: GET /api/v1/processes/{process_instance_id} (§31, §77.1).

Ответ на вопрос «что это за экземпляр процесса». Ленты событий здесь нет: «что с
ним происходило» отвечает ``GET /api/v1/events?subject_id=…`` (§31, §32), и
второй способ получать те же данные означал бы вторую реализацию пагинации.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi import status as http_status

from chronoscope.api.dependencies import get_get_process_detail
from chronoscope.api.dependencies import get_process_relations, get_process_tree
from chronoscope.api.connection_schemas import ProcessRelationsOut, ProcessTreeOut
from chronoscope.application.queries.process_connections import GetProcessRelations, GetProcessTree
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


@router.get("/processes/{process_instance_id}/relations", response_model=ProcessRelationsOut,
            responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def relations(
    process_instance_id: str = Path(pattern=prefixed_id_pattern(PREFIX_PROCESS_INSTANCE)),
    limit: int = Query(100, ge=1, le=200),
    use_case: GetProcessRelations = Depends(get_process_relations),
) -> ProcessRelationsOut:
    result = use_case.execute(process_instance_id, limit=limit)
    if result is None:
        raise HTTPException(404, "экземпляр процесса не найден")
    return ProcessRelationsOut.model_validate(result)


@router.get("/processes/{process_instance_id}/tree", response_model=ProcessTreeOut,
            responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def tree(
    process_instance_id: str = Path(pattern=prefixed_id_pattern(PREFIX_PROCESS_INSTANCE)),
    depth: int = Query(3, ge=0, le=5),
    max_nodes: int = Query(200, ge=1, le=200),
    use_case: GetProcessTree = Depends(get_process_tree),
) -> ProcessTreeOut:
    result = use_case.execute(process_instance_id, depth=depth, max_nodes=max_nodes)
    if result is None:
        raise HTTPException(404, "экземпляр процесса не найден")
    return ProcessTreeOut.model_validate(result)
