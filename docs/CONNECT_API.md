# Connect 0.0.5: связи, дерево и события

Семантика: [ADR-0017](decisions/0017-evidence-based-process-relationships.md).
Release gate: [проверка 0.0.5](verification/0.0.5-connect.md).

## GET /api/v1/processes/{id}/relations?limit=100

`id` — `proc_<ULID>`, `limit` — 1..200. Предел общий для возвращённых детей
и кандидатов с неустановленной связью. Нет второго курсора: это bounded view;
отдельного потомка можно открыть как новый корень исследования.

Поля ответа:

| Поле | Значение |
|---|---|
| `process` | Реальный экземпляр: id, name, host_id, boot_id, pid, started_at, exited_at, problems |
| `parent` | `parent_of`: child_instance_id, parent_instance_id или null, parent_pid или null, resolution, reason, evidence |
| `parent_process` | Наблюдения о разрешённом родителе или null; фиктивных узлов нет |
| `children` | Список `{process, relationship}` только разрешённых связей с данным экземпляром |
| `unresolved_children` | Кандидаты по parent PID с отдельным статусом; они **не объявлены детьми** |
| `truncated`, `reasons` | Ограничение запроса: children_limit, evidence_limit или invalid_reference |
| `observation_complete` | Всегда false: WMI и доставка могут пропускать события |

`resolution`: `resolved` (единственный совместимый кандидат среди доступных
наблюдений), `unresolved` (неоднозначность/лимит), `conflicting` (противоречие),
`insufficient_observation` (недостаточно наблюдений). Это категории, не оценки
вероятности. `resolved` не доказывает причинность и не обещает полноту истории.

В `evidence` не более 32 ID сохранённых Events; `evidence_count` — сколько
ссылок учтено, `evidence_truncated` сообщает об усечении **списка ссылок**.
При разрешении учитываются все прочитанные свидетельства, не только выданные
ссылки. `/events/{id}` открывает свидетельство, `/events?subject_id=...` — историю.

Пример из реального Windows-прогона (вложенные summaries опущены):

```json
{
  "relationship_type": "parent_of",
  "child_instance_id": "proc_01M4JSPGKDF86DY3NC2KYVV5R1",
  "parent_instance_id": "proc_01M4JSPCFDJ44MAJB129BMMV1J",
  "parent_pid": 28756,
  "resolution": "resolved",
  "reason": "unique_compatible_observation",
  "evidence": [
    "evt_01M4JSPDMJ6A99X01C0TGEPPQ2",
    "evt_01M4JSPHKC8Q2JQ361B2PJ7RS4",
    "evt_01M4JSQ0MMKQXJY0AZE2VX8DYY",
    "evt_01M4JSQ2M7GT2DKJ69G3ZZAMZX"
  ],
  "evidence_count": 4,
  "evidence_truncated": false
}
```

Фрагмент финального живого ответа сохранён также как
[JSON](verification/0.0.5-parent-response.json).

## GET /api/v1/processes/{id}/tree?depth=3&max_nodes=200

`depth` — 0..5; `max_nodes` — 1..200, включая корень. Плоский `nodes` содержит
`{process, parent_instance_id, depth}`. Родитель корня здесь null **по роли в
дереве**, а не как утверждение об отсутствии родителя в Windows.
Список идёт breadth-first; дети упорядочены по `(started_at, instance_id)`.

`root_instance_id`, `truncated`, `reasons`, `unresolved_children` (число
кандидатов с неустановленной связью), `observation_complete=false` дополняют
ответ. `depth_limit`, `node_limit`, `evidence_limit`, `cycle_detected` и
`invalid_reference` явно показывают, где исследование остановилось.
`truncated=false` означает только отсутствие ограничения этого запроса.

Чтение bounded: до 1000 Events корня; до 10000 lifecycle Events на запрос,
до 2000 candidate instance IDs за поиск и до 200 host/boot/PID ключей в batch.
Overflow обнаруживается чтением limit+1; частичная история кандидатов не
используется для разрешения связи. Возможен ответ из одного корня с
`evidence_limit`, даже если `max_nodes=200`.

## GET /api/v1/events?entity_id={id}

Общий фильтр `actor_id = id OR subject_id = id`. Событие с обеими ссылками
возвращается один раз. Сохраняются `type`, `source`, `from`, `to`, `limit`
(1..1000) и существующий `cursor`; порядок `(timestamp DESC, id DESC)`.
Другие фильтры соединяются через AND. `entity_id` проверяется как prefixed ULID.
Неизвестная сущность даёт пустую страницу, как существующие actor/subject filters.
Курсор надо использовать с теми же фильтрами; между страницами новые и поздние
события могут появиться, исторический snapshot на всю сессию не обещается.

Это **записанные entity references**, включая решение нормализатора на момент
ingest. Connect может оспорить старый actor. Временно соседние `system.event`
без actor/subject сюда не попадают. Temporal context в 0.0.5 не добавлен.

## Ошибки и совместимость

Неверные ID/параметры — 422 `invalid_input`, неизвестный процесс — 404
`not_found`, более 1000 Events корня — 500 `history_limit_exceeded`, ошибка
хранилища — 500 `storage_failure`. Конфликт и отсутствие детей — ответы 200,
потому что это свойства наблюдений. Используется существующий error envelope.

Каждый relations/tree запрос читает один явный SQLite snapshot. Между detail,
relations, tree и списками Events snapshot consistency нет; поздний ingest
может изменить новый view. Event и RawEvent задним числом не меняются.

Форма ProcessDetail сохранена. Его `parent` — историческая ссылка из Events,
локальные конфликты parent PID/actor теперь сбрасывают resolved. Актуальная
связь с проверкой кандидатов находится в `/relations`; UI использует её.
Старые API и CLI команды остаются доступны, версии transport schema/API — 1.
