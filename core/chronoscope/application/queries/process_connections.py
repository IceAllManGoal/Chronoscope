"""Bounded breadth-first exploration over repository ports; no process table."""

from dataclasses import dataclass
from datetime import UTC, datetime

from chronoscope.application.ports import EventRepositoryPort
from chronoscope.domain.errors import HistoryLimitExceededError, InvalidInputError
from chronoscope.domain.process_relationships import (
    ProcessObservation, ProcessRelationship, observe_process, resolve_parent,
)

MAX_EVIDENCE = 10000
MAX_CANDIDATES = 2000
MAX_NODES = 200
MAX_DEPTH = 5


@dataclass(frozen=True, slots=True)
class ConnectedProcess:
    process: ProcessObservation
    relationship: ProcessRelationship


@dataclass(frozen=True, slots=True)
class ProcessRelations:
    process: ProcessObservation
    parent: ProcessRelationship
    parent_process: ProcessObservation | None
    children: tuple[ConnectedProcess, ...]
    unresolved_children: tuple[ConnectedProcess, ...]
    truncated: bool
    reasons: tuple[str, ...]
    observation_complete: bool = False


@dataclass(frozen=True, slots=True)
class ProcessTreeNode:
    process: ProcessObservation
    parent_instance_id: str | None
    depth: int


@dataclass(frozen=True, slots=True)
class ProcessTree:
    root_instance_id: str
    nodes: tuple[ProcessTreeNode, ...]
    truncated: bool
    reasons: tuple[str, ...]
    unresolved_children: int
    observation_complete: bool = False


class EvidenceLimit(Exception):
    """Internal control flow: partial candidate evidence must never resolve an edge."""


def _order(process: ProcessObservation) -> tuple[datetime, str]:
    return process.started_at or datetime.max.replace(tzinfo=UTC), process.id


class _Reader:
    def __init__(self, repository: EventRepositoryPort):
        self.repository = repository
        self.cache: dict[str, ProcessObservation] = {}
        self.remaining = MAX_EVIDENCE

    def load(self, ids: tuple[str, ...]) -> list[ProcessObservation]:
        missing = tuple(sorted(set(ids) - self.cache.keys()))
        if missing:
            rows = self.repository.find_instances_events(missing, limit=self.remaining + 1)
            if len(rows) > self.remaining:
                raise EvidenceLimit
            self.remaining -= len(rows)
            grouped = {id_: [] for id_ in missing}
            for event in rows:
                if event.subject:
                    grouped[event.subject.id].append(event)
            for id_, events in grouped.items():
                if events:
                    self.cache[id_] = observe_process(id_, events)
        return [self.cache[id_] for id_ in ids if id_ in self.cache]

    def root(self, id_: str) -> ProcessObservation | None:
        rows = self.repository.find_instance_events(id_, limit=1001)
        if len(rows) > 1000:
            raise HistoryLimitExceededError(process_instance_id=id_, limit=1000)
        if not rows or not any(e.type in ("process.started", "process.exited") for e in rows):
            return None
        self.remaining -= len(rows)
        self.cache[id_] = observe_process(id_, rows)
        return self.cache[id_]

    def find(self, keys, field: str) -> list[ProcessObservation]:
        keys = tuple(sorted(set(key for key in keys if key is not None)))
        if not keys:
            return []
        if len(keys) > MAX_NODES:
            raise EvidenceLimit
        ids = self.repository.find_process_ids(keys, field=field, limit=MAX_CANDIDATES + 1)
        if len(ids) > MAX_CANDIDATES:
            raise EvidenceLimit
        return self.load(ids)

    def parents(self, children: list[ProcessObservation]) -> list[ProcessRelationship]:
        candidates = self.find(
            (child.key(pid) for child in children for pid in child.parent_pids), "pid",
        )
        return [resolve_parent(child, candidates) for child in children]

    def children(self, parents: list[ProcessObservation]) -> list[ConnectedProcess]:
        children = self.find((p.key() for p in parents), "parent_pid")
        children.sort(key=_order)
        relations = self.parents(children)
        return [ConnectedProcess(p, r) for p, r in zip(children, relations)]


class GetProcessRelations:
    def __init__(self, repository: EventRepositoryPort):
        self.repository = repository

    def execute(self, instance_id: str, *, limit: int = 100) -> ProcessRelations | None:
        if not 1 <= limit <= MAX_NODES:
            raise InvalidInputError("limit: ожидается 1..200")
        with self.repository.snapshot() as repository:
            reader = _Reader(repository)
            root = reader.root(instance_id)
            if root is None:
                return None
            reasons: set[str] = set()
            parent = resolve_parent(root, [])
            children: list[ConnectedProcess] = []
            try:
                parent = reader.parents([root])[0]
            except EvidenceLimit:
                reasons.add("evidence_limit")
                parent = ProcessRelationship(root.id, None, root.parent_pids[0] if len(root.parent_pids) == 1 else None,
                                             "unresolved", "evidence_limit", root.evidence)
            try:
                children = reader.children([root])
            except EvidenceLimit:
                reasons.add("evidence_limit")
            # A reused PID may discover children belonging to a different instance.
            children = [c for c in children if c.relationship.parent_instance_id in (None, root.id)]
            if len(children) > limit:
                reasons.add("children_limit")
            selected = children[:limit]
            return ProcessRelations(
                root, parent, reader.cache.get(parent.parent_instance_id),
                tuple(c for c in selected if c.relationship.resolution == "resolved"),
                tuple(c for c in selected if c.relationship.resolution != "resolved"),
                bool(reasons), tuple(sorted(reasons)),
            )


class GetProcessTree:
    def __init__(self, repository: EventRepositoryPort):
        self.repository = repository

    def execute(self, instance_id: str, *, depth: int = 3, max_nodes: int = 200) -> ProcessTree | None:
        if not 0 <= depth <= MAX_DEPTH or not 1 <= max_nodes <= MAX_NODES:
            raise InvalidInputError("depth: ожидается 0..5; max_nodes: ожидается 1..200")
        with self.repository.snapshot() as repository:
            reader = _Reader(repository)
            root = reader.root(instance_id)
            if root is None:
                return None
            nodes = [ProcessTreeNode(root, None, 0)]
            seen = {root.id}
            frontier = [root]
            reasons: set[str] = set()
            unresolved: set[str] = set()
            # Also inspect the boundary: depth truncation must not be silent.
            for level in range(depth + 1):
                try:
                    children = reader.children(frontier)
                except EvidenceLimit:
                    reasons.add("evidence_limit")
                    break
                parent_ids = {p.id for p in frontier}
                following = []
                for child in children:
                    relation = child.relationship
                    if relation.resolution != "resolved":
                        unresolved.add(child.process.id)
                        continue
                    if relation.parent_instance_id not in parent_ids:
                        continue
                    if child.process.id in seen:
                        reasons.add("cycle_detected")
                        continue
                    if level == depth:
                        reasons.add("depth_limit")
                        continue
                    if len(nodes) >= max_nodes:
                        reasons.add("node_limit")
                        continue
                    seen.add(child.process.id)
                    nodes.append(ProcessTreeNode(child.process, relation.parent_instance_id, level + 1))
                    following.append(child.process)
                if not following or "node_limit" in reasons:
                    break
                frontier = following
            return ProcessTree(root.id, tuple(nodes), bool(reasons), tuple(sorted(reasons)), len(unresolved))
