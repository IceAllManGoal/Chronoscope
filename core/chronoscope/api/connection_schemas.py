"""HTTP views for Connect. Historical Event and ProcessDetail stay compatible."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer

from chronoscope.api.schemas import ProcessInstanceId, HostId, BootId, EventId
from chronoscope.domain.process_relationships import Resolution
from chronoscope.domain.timestamps import format_utc


class ConnectionOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class ProcessSummaryOut(ConnectionOut):
    id: ProcessInstanceId
    name: str | None
    host_id: HostId | None
    boot_id: BootId | None
    pid: int | None
    started_at: datetime | None
    exited_at: datetime | None
    problems: tuple[str, ...]

    @field_serializer("started_at", "exited_at", when_used="json")
    def timestamp(self, value: datetime | None) -> str | None:
        return format_utc(value) if value else None


class RelationshipOut(ConnectionOut):
    child_instance_id: ProcessInstanceId
    parent_instance_id: ProcessInstanceId | None
    parent_pid: int | None
    relationship_type: Literal["parent_of"]
    resolution: Resolution
    reason: str
    evidence: tuple[EventId, ...]


class ConnectedProcessOut(ConnectionOut):
    process: ProcessSummaryOut
    relationship: RelationshipOut


class ProcessRelationsOut(ConnectionOut):
    process: ProcessSummaryOut
    parent: RelationshipOut
    parent_process: ProcessSummaryOut | None
    children: tuple[ConnectedProcessOut, ...]
    unresolved_children: tuple[ConnectedProcessOut, ...]
    truncated: bool
    reasons: tuple[str, ...]
    observation_complete: Literal[False]


class ProcessTreeNodeOut(ConnectionOut):
    process: ProcessSummaryOut
    parent_instance_id: ProcessInstanceId | None
    depth: int


class ProcessTreeOut(ConnectionOut):
    root_instance_id: ProcessInstanceId
    nodes: tuple[ProcessTreeNodeOut, ...]
    truncated: bool
    reasons: tuple[str, ...]
    unresolved_children: int
    observation_complete: Literal[False]
