"""Conservative parent resolution from immutable lifecycle observations (ADR-0017)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from chronoscope.domain.errors import InvalidInputError
from chronoscope.domain.events.event import Event
from chronoscope.domain.ids import is_prefixed_id, process_instance_id
from chronoscope.domain.timestamps import parse_utc

Resolution = Literal["resolved", "unresolved", "conflicting", "insufficient_observation"]
ProcessKey = tuple[str, str, int]
LIFECYCLE_TYPES = ("process.started", "process.exited")


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    id: str
    name: str | None
    host_id: str | None
    boot_id: str | None
    pid: int | None
    started_at: datetime | None
    exited_at: datetime | None
    parent_pids: tuple[int, ...]
    actor_ids: tuple[str, ...]
    evidence: tuple[str, ...]
    problems: tuple[str, ...] = ()

    def key(self, pid: int | None = None) -> ProcessKey | None:
        value = self.pid if pid is None else pid
        if self.host_id and self.boot_id and value is not None:
            return self.host_id, self.boot_id, value
        return None


@dataclass(frozen=True, slots=True)
class ProcessRelationship:
    child_instance_id: str
    parent_instance_id: str | None
    parent_pid: int | None
    resolution: Resolution
    reason: str
    evidence: tuple[str, ...]
    relationship_type: Literal["parent_of"] = "parent_of"


def observe_process(instance_id: str, events: Sequence[Event]) -> ProcessObservation:
    """Do not select the richest/earliest record when records disagree."""
    rows = sorted((e for e in events if e.type in LIFECYCLE_TYPES), key=lambda e: (e.timestamp, e.id))
    hosts = {e.host_id for e in rows}
    boots = {e.boot_id for e in rows if e.boot_id is not None}
    pids: set[int] = set()
    parents: set[int] = set()
    actors: set[str] = set()
    starts: set[datetime] = set()
    exits = {e.timestamp for e in rows if e.type == "process.exited"}
    problems: set[str] = set()
    for event in rows:
        if not event.subject or event.subject.type != "process" or event.subject.id != instance_id:
            problems.add("invalid_subject")
        for field, values in (("pid", pids), ("parent_pid", parents)):
            value = event.attributes.get(field)
            if value is not None:
                if type(value) is not int or value < (1 if field == "pid" else 0):
                    problems.add("invalid_" + field)
                else:
                    values.add(value)
        if event.type == "process.started":
            starts.add(event.timestamp)
        raw = event.attributes.get("process_started_at")
        if raw is not None:
            try:
                if not isinstance(raw, str):
                    raise InvalidInputError("invalid timestamp")
                starts.add(parse_utc(raw))
            except InvalidInputError:
                problems.add("invalid_timestamps")
        if event.actor is not None:
            if event.actor.type != "process" or not is_prefixed_id(event.actor.id, "proc"):
                problems.add("invalid_actor")
            else:
                actors.add(event.actor.id)
    for name, values in (("identity", hosts), ("identity", boots), ("identity", pids),
                         ("timestamps", starts), ("timestamps", exits),
                         ("parent_pids", parents), ("actor_references", actors)):
        if len(values) > 1:
            problems.add("conflicting_" + name)
    host = next(iter(hosts)) if len(hosts) == 1 else None
    boot = next(iter(boots)) if len(boots) == 1 else None
    pid = next(iter(pids)) if len(pids) == 1 else None
    start = next(iter(starts)) if len(starts) == 1 else None
    end = min(exits) if exits else None
    if start is not None and end is not None and end < start:
        problems.add("invalid_timestamps")
    if host and pid and start:
        expected = process_instance_id(host_id=host, boot_id=boot, pid=pid, started_at=start)
        if expected != instance_id:
            problems.add("identity_mismatch")
    return ProcessObservation(
        id=instance_id, name=next((e.subject.name for e in rows if e.subject and e.subject.name), None),
        host_id=host, boot_id=boot, pid=pid, started_at=start, exited_at=end,
        parent_pids=tuple(sorted(parents)), actor_ids=tuple(sorted(actors)),
        evidence=tuple(e.id for e in rows), problems=tuple(sorted(problems)),
    )


def resolve_parent(child: ProcessObservation, candidates: Sequence[ProcessObservation]) -> ProcessRelationship:
    """Resolved is an inference over available observations, never a liveness claim."""
    evidence = set(child.evidence)
    parent_pid = child.parent_pids[0] if len(child.parent_pids) == 1 else None

    def result(status: Resolution, reason: str, parent: str | None = None) -> ProcessRelationship:
        return ProcessRelationship(child.id, parent, parent_pid, status, reason, tuple(sorted(evidence)))

    if child.problems:
        return result("conflicting", child.problems[0])
    if not child.host_id or not child.boot_id or child.started_at is None or parent_pid is None:
        return result("insufficient_observation", "missing_identity_or_parent_pid")
    relevant = [p for p in candidates if p.key() == child.key(parent_pid)]
    possible: list[ProcessObservation] = []
    for parent in relevant:
        evidence.update(parent.evidence)
        if parent.problems:
            return result("conflicting", "candidate_observations_conflict")
        if parent.started_at is None:
            return result("insufficient_observation", "candidate_start_unknown")
        if parent.started_at > child.started_at:
            continue
        if parent.exited_at is not None and parent.exited_at < child.started_at:
            continue
        if parent.id == child.id:
            return result("conflicting", "self_parent")
        possible.append(parent)
    if child.actor_ids and not any(p.id == child.actor_ids[0] for p in relevant):
        return result("conflicting" if relevant else "insufficient_observation", "actor_not_supported")
    if not possible:
        return result("insufficient_observation", "no_compatible_candidate")
    if len(possible) > 1:
        return result("unresolved", "ambiguous_candidates")
    parent = possible[0]
    if child.actor_ids and child.actor_ids[0] != parent.id:
        return result("conflicting", "actor_candidate_mismatch")
    return result("resolved", "unique_compatible_observation", parent.id)
