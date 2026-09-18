# Test fixtures

Синтетические события для тестов. Главная цель — проверять нормализацию и контракт Agent ↔ Core без запуска реальных процессов и без риска утечки приватных данных пользователя:

```text
фикстура → normalizer / ingest → проверка результата
```

Ни одна фикстура не должна содержать реальных командных строк, токенов, паролей, доменных имён, путей пользователя или SID конкретной машины. Всё содержимое — вымышленное.

## Состав

| Файл | Что проверяет | Валиден против схемы |
|---|---|---|
| `windows/process_start_001.json` | Базовый `process.started`: полный набор полей, включая `path`, `command_line`, `user_sid` | да |
| `windows/process_exit_001.json` | Парный `process.exited` для того же process instance | да |
| `windows/process_start_missing_path.json` | Отсутствие опциональных полей payload (`path`, `command_line`, `user_sid`, `process_started_at`) — §60: одно неполное событие не должно ломать pipeline | да |
| `windows/malformed_event.json` | **Намеренно невалидное** событие — негативный тест ingest | **нет, ожидаемо** |
| `ingest/ingest_batch_001.json` | Batch envelope из двух событий, `POST /api/v1/ingest/raw-events` | да |
| `events/process_started_001.json` | Ожидаемый результат нормализации `windows/process_start_001.json` | да |
| `events/process_exited_001.json` | Ожидаемый результат нормализации `windows/process_exit_001.json` | да |

## Соглашения

- `host_id` и `boot_id` во всех windows-фикстурах одинаковые: они описывают одну машину в рамках одной загрузки ОС. Так фикстуры образуют связную историю, пригодную для проверки группировки по boot session.
- `process_start_001.json` и `process_exit_001.json` описывают **один и тот же экземпляр процесса**: `pid` 9812, одинаковый `process_started_at`. Из этой пары Core должен вывести одинаковый `process_instance_id` — это основная проверка правила «PID не является идентичностью» (§14).
- `process_start_missing_path.json` относится к другому процессу (`pid` 1234) в той же boot session.
- `source_timestamp` всегда не позже `observed_at`: так выглядит нормальная задержка между событием в источнике и его наблюдением (§24).
- `events/process_started_001.json` и `events/process_exited_001.json` — ожидаемый результат нормализации соответствующей windows-фикстуры. Ключевая проверка: `subject.id` в обоих файлах **одинаков** (`proc_01M2T1R61M8W1EMS4WBMRDKSTZ`), хотя события разные. Это прямое следствие требования детерминированности `process_instance_id` из [`docs/EVENT_MODEL.md`](../../docs/EVENT_MODEL.md): идентификатор выводится из `host_id + boot_id + pid + process_started_at`, а не генерируется случайно. Если нормализатор выдаст здесь разные идентификаторы, связь «процесс запустился → процесс завершился» будет потеряна.

  Значение `subject.id` не выдумано: это результат применения формулы вывода к данным windows-фикстуры, поэтому оно проверяемо. Значение `actor.id` (`proc_01M2SZAT80WBG97HT3GY4XRGRK`) соответствует родительскому `explorer.exe` при допущении, что тот стартовал в `10:00:00.000Z` и его событие старта уже наблюдалось. Это допущение важно: родитель известен событию только как `parent_pid`, а идентичность выводится из времени старта родителя, которого в событии нет. Поэтому нормализатор ищет родителя среди уже сохранённых событий, и если не находит — оставляет `actor` пустым и выставляет `parent_resolved: false` в атрибутах.
- Идентификаторы в фикстурах записаны в канонической форме ULID: 26 символов, заглавные, алфавит Crockford base32 **без букв `I`, `L`, `O`, `U`**.

## Что именно нарушает `malformed_event.json`

Файл специально ломает восемь правил контракта, чтобы негативный тест покрывал разные ветки валидации:

| Поле | Нарушение |
|---|---|
| `schema_version` | `99` вместо поддерживаемой `1` — §52: Core обязан явно отклонить неподдерживаемую версию |
| `collector_version` | отсутствует — обязательное поле |
| `raw_event_id` | отсутствует |
| `event_id` | лишнее поле: вместо него должен быть `raw_event_id`; нарушает и форму идентификатора, и `additionalProperties: false` |
| `collector` | `Windows.Process` — uppercase вместо lowercase dot notation |
| `observed_at` | `+05:00` вместо суффикса `Z` — §24: в хранилище только UTC |
| `host_id` | `desktop-abc` — не соответствует форме `host_<ULID>` |
| `payload` | строка вместо объекта |

Тесты ingest должны проверять, что такое событие отвергается явной ошибкой, а не молча разбирается неверно.

## Как валидировать

Схемы лежат в [`shared/schemas/`](../schemas/). `ingest-batch.schema.json` ссылается на `raw-event.schema.json` по `$id`, поэтому валидатор должен загрузить оба файла в registry, не ходя в сеть:

```python
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

schemas_dir = Path("shared/schemas")
registry = Registry()
for path in schemas_dir.glob("*.schema.json"):
    contents = json.loads(path.read_text(encoding="utf-8"))
    registry = registry.with_resource(contents["$id"], Resource.from_contents(contents))

validator = Draft202012Validator(json.loads((schemas_dir / "raw-event.schema.json").read_text(encoding="utf-8")), registry=registry)
```

Контрактные тесты (см. §58 спеки) должны валидировать все фикстуры, кроме `malformed_event.json`, и отдельно проверять, что `malformed_event.json` валидацию не проходит.
