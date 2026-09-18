"""Ссылка на сущность, участвующую в событии (§19).

Ключевое правило модели: ``id`` — это стабильная идентичность, а ``name`` —
всего лишь отображаемое значение для UI. Для процесса ``id`` — это
``process_instance_id``, а не PID: PID переиспользуется ОС и идентичностью
не является (§14).
"""

from __future__ import annotations

from dataclasses import dataclass

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.validation import (
    ENTITY_TYPE_RE,
    PREFIXED_ID_RE,
    require_matches,
)

MAX_NAME_LENGTH = 260


@dataclass(frozen=True, slots=True)
class EntityRef:
    type: str
    id: str
    name: str | None = None

    def __post_init__(self) -> None:
        require_matches(self.type, ENTITY_TYPE_RE, "entity.type")
        require_matches(self.id, PREFIXED_ID_RE, "entity.id")
        if self.name is not None:
            if not isinstance(self.name, str):
                raise InvalidInputError("entity.name: должно быть строкой или null")
            if len(self.name) > MAX_NAME_LENGTH:
                raise InvalidInputError(f"entity.name: длина превышает {MAX_NAME_LENGTH} символов")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"type": self.type, "id": self.id}
        if self.name is not None:
            result["name"] = self.name
        return result
