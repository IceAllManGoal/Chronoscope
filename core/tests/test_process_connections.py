"""Evidence semantics plus real SQLite, ingest, pagination and traversal regressions."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event

from chronoscope.application.ports import EventQuery
from chronoscope.application.queries.process_connections import GetProcessRelations, GetProcessTree
from chronoscope.domain.events.entity_ref import EntityRef
from chronoscope.domain.ids import new_event_id, new_raw_event_id, process_instance_id
from chronoscope.domain.process_relationships import observe_process, resolve_parent
from chronoscope.domain.timestamps import format_utc
from chronoscope.main import create_app
from tests.factories import BOOT_ID, HOST_ID, SOURCE_TIMESTAMP, make_event


def lifecycle(pid, second=0, parent=None, *, exit_second=None, host=HOST_ID, boot=BOOT_ID, actor=None):
    start = SOURCE_TIMESTAMP + timedelta(seconds=second)
    id_ = process_instance_id(host_id=host, boot_id=boot, pid=pid, started_at=start)
    attrs = {"pid": pid, "process_started_at": format_utc(start)}
    if parent is not None:
        attrs["parent_pid"] = parent
    return make_event(
        id=new_event_id(), raw_event_id=None, host_id=host, boot_id=boot,
        type="process.started" if exit_second is None else "process.exited",
        timestamp=start if exit_second is None else SOURCE_TIMESTAMP + timedelta(seconds=exit_second),
        attributes=attrs, subject=EntityRef("process", id_, f"p{pid}.exe"), actor=actor,
    )


def observation(*events):
    return observe_process(events[0].subject.id, events)


def put(repository, *events):
    for item in events:
        repository.insert(item, ingested_at=SOURCE_TIMESTAMP)


def test_unique_parent_without_exit_is_inference():
    parent, child = lifecycle(1), lifecycle(2, 1, 1)
    result = resolve_parent(observation(child), [observation(parent)])
    assert result.resolution == "resolved"
    assert result.parent_instance_id == parent.subject.id
    assert set(result.evidence) == {parent.id, child.id}


@pytest.mark.parametrize("variant", ["host", "boot", "boot_missing", "future", "exited", "absent"])
def test_incompatible_parent_is_never_linked(variant):
    parent = lifecycle(1)
    child = lifecycle(2, 10, 1)
    if variant == "host":
        parent = lifecycle(1, host="host_01K5R8Z9M4Q7T2V6X1B3D5F7H8")
    if variant == "boot":
        parent = lifecycle(1, boot="boot_01K5R8Z9M4Q7T2V6X1B3D5F7HB")
    if variant == "boot_missing":
        child = lifecycle(2, 10, 1, boot=None)
    if variant == "future":
        parent = lifecycle(1, 11)
    if variant == "exited":
        parent = lifecycle(1, exit_second=9)
    result = resolve_parent(observation(child), [] if variant == "absent" else [observation(parent)])
    assert result.parent_instance_id is None
    assert result.resolution == "insufficient_observation"


def test_pid_reuse_and_competing_candidates():
    old = observation(lifecycle(1), lifecycle(1, exit_second=4))
    new = observation(lifecycle(1, 5))
    child = observation(lifecycle(2, 10, 1))
    assert resolve_parent(child, [old, new]).parent_instance_id == new.id
    no_exit = observation(lifecycle(1))
    result = resolve_parent(child, [no_exit, new])
    assert result.resolution == "unresolved"
    assert result.reason == "ambiguous_candidates"
    assert result.parent_instance_id is None


@pytest.mark.parametrize("variant", ["parent_pid", "actor", "start", "exit", "identity", "invalid_time"])
def test_conflicts_are_not_silently_selected(variant):
    parent = lifecycle(1)
    start = lifecycle(2, 10, 1, actor=parent.subject)
    end = lifecycle(2, 10, 1, exit_second=20, actor=parent.subject)
    if variant == "parent_pid":
        end = replace(end, attributes={**end.attributes, "parent_pid": 3})
    if variant == "actor":
        end = replace(end, actor=lifecycle(3).subject)
    if variant == "start":
        end = replace(end, attributes={**end.attributes, "process_started_at": format_utc(SOURCE_TIMESTAMP)})
    if variant == "exit":
        end = replace(end, timestamp=SOURCE_TIMESTAMP)
    if variant == "identity":
        end = replace(end, host_id="host_01K5R8Z9M4Q7T2V6X1B3D5F7H8")
    if variant == "invalid_time":
        end = replace(end, attributes={**end.attributes, "process_started_at": "bad"})
    result = resolve_parent(observation(start, end), [observation(parent)])
    assert result.resolution == "conflicting"
    assert result.parent_instance_id is None


def test_actor_is_not_a_tiebreaker_or_trusted_when_incompatible():
    parent = lifecycle(1)
    reused = lifecycle(1, 5)
    child = lifecycle(2, 10, 1, actor=parent.subject)
    assert resolve_parent(observation(child), [observation(parent), observation(reused)]).resolution == "unresolved"
    result = resolve_parent(observation(child), [observation(parent, lifecycle(1, exit_second=4)), observation(reused)])
    assert result.resolution == "conflicting"


def test_dangling_actor_and_self_parent():
    parent = lifecycle(1)
    child = lifecycle(2, 10, 1, actor=parent.subject)
    result = resolve_parent(observation(child), [])
    assert result.parent_instance_id is None
    assert result.reason == "actor_not_supported"
    self_child = observation(lifecycle(2, 0, 2))
    assert resolve_parent(self_child, [self_child]).reason == "self_parent"


def test_chain_tree_limits_and_stable_order(event_repository, migrated_engine):
    root, a, b, leaf = lifecycle(1), lifecycle(2, 1, 1), lifecycle(3, 1, 1), lifecycle(4, 2, 2)
    put(event_repository, leaf, b, a, root)
    relations = GetProcessRelations(event_repository).execute(root.subject.id)
    assert [x.process.id for x in relations.children] == sorted([a.subject.id, b.subject.id])
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            statements.append(statement)
    sql_event.listen(migrated_engine, "before_cursor_execute", capture)
    try:
        tree = GetProcessTree(event_repository).execute(root.subject.id)
    finally:
        sql_event.remove(migrated_engine, "before_cursor_execute", capture)
    assert len(tree.nodes) == 4 and not tree.truncated
    assert tree.observation_complete is False
    assert len(statements) <= 13  # batched per level, independent of node count
    shallow = GetProcessTree(event_repository).execute(root.subject.id, depth=1)
    assert len(shallow.nodes) == 3 and shallow.reasons == ("depth_limit",)
    limited = GetProcessTree(event_repository).execute(root.subject.id, max_nodes=2)
    assert len(limited.nodes) == 2 and limited.reasons == ("node_limit",)
    assert GetProcessTree(event_repository).execute(leaf.subject.id, depth=0).truncated is False


def test_cycles_terminate_without_duplicate_nodes(event_repository):
    a, b = lifecycle(1, 0, 2), lifecycle(2, 0, 1)
    put(event_repository, a, b)
    tree = GetProcessTree(event_repository).execute(a.subject.id, depth=5)
    assert len(tree.nodes) == 2
    assert tree.reasons == ("cycle_detected",)


def test_wide_tree_has_bounded_queries(event_repository, migrated_engine):
    root = lifecycle(1)
    put(event_repository, root, *(lifecycle(i, 1, 1) for i in range(2, 242)))
    tree = GetProcessTree(event_repository).execute(root.subject.id)
    assert len(tree.nodes) == 200
    assert tree.reasons == ("node_limit",)
    assert len({x.process.id for x in tree.nodes}) == 200
    relations = GetProcessRelations(event_repository).execute(root.subject.id, limit=2)
    assert len(relations.children) == 2 and relations.reasons == ("children_limit",)


def test_late_observation_changes_view_not_events(event_repository):
    parent, child = lifecycle(1), lifecycle(2, 1, 1)
    put(event_repository, child)
    query = GetProcessRelations(event_repository)
    assert query.execute(child.subject.id).parent.parent_instance_id is None
    saved = event_repository.get(child.id)
    put(event_repository, parent)
    assert query.execute(child.subject.id).parent.parent_instance_id == parent.subject.id
    assert event_repository.get(child.id) == saved
    # A late exit invalidates the previously plausible relation.
    put(event_repository, lifecycle(1, exit_second=0.5))
    assert query.execute(child.subject.id).parent.parent_instance_id is None


def test_snapshot_does_not_mix_ingest_between_reads(event_repository):
    parent, child = lifecycle(1), lifecycle(2, 1, 1)
    put(event_repository, child)
    with event_repository.snapshot() as snapshot:
        assert snapshot.get(child.id)
        put(event_repository, parent)
        assert snapshot.get(parent.id) is None
    assert event_repository.get(parent.id) is not None


def test_entity_union_pagination_and_temporal_non_causality(event_repository):
    root = lifecycle(1)
    child = lifecycle(2, 0, 1, actor=root.subject)
    both = replace(root, id=new_event_id(), actor=root.subject)
    unrelated = make_event(id=new_event_id(), type="system.event", source="windows.eventlog",
                           subject=None, actor=None, attributes={}, raw_event_id=None)
    put(event_repository, root, child, both, unrelated)
    result = []
    cursor = None
    while True:
        page = event_repository.list(EventQuery(entity_id=root.subject.id, limit=1, cursor=cursor))
        result.extend(e.id for e in page.events)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert result == sorted([root.id, child.id, both.id], reverse=True)
    assert len(set(result)) == 3
    assert not event_repository.list(EventQuery(entity_id=root.subject.id, types=("system.event",))).events
    assert not event_repository.list(EventQuery(entity_id=root.subject.id, time_from=SOURCE_TIMESTAMP + timedelta(seconds=1))).events


def test_http_ingest_relations_restart_and_duplicate_delivery(migrated_database):
    parent, child = lifecycle(1), lifecycle(2, 1, 1)
    def raw(e):
        return {"schema_version": 1, "raw_event_id": new_raw_event_id(), "collector": "windows.process",
                "collector_version": "0.0.4", "observed_at": format_utc(e.timestamp),
                "source_timestamp": format_utc(e.timestamp), "host_id": e.host_id, "boot_id": e.boot_id,
                "payload_type": "process_start", "payload": {**e.attributes, "name": e.subject.name}}
    batch = {"schema_version": 1, "events": [raw(child), raw(parent)]}
    with TestClient(create_app(migrated_database)) as client:
        assert client.post("/api/v1/ingest/raw-events", json=batch).json()["accepted"] == 2
        assert client.post("/api/v1/ingest/raw-events", json=batch).json()["duplicates"] == 2
        response = client.get(f"/api/v1/processes/{child.subject.id}/relations")
        assert response.status_code == 200
        body = response.json()
        assert body["parent"]["parent_instance_id"] == parent.subject.id
        assert body["parent"]["resolution"] == "resolved"
        assert body["observation_complete"] is False
        before = client.get(f"/api/v1/events?subject_id={child.subject.id}").json()
        assert before["events"][0]["actor"] is None
        for suffix in ("relations", "tree"):
            assert client.get(f"/api/v1/processes/bad/{suffix}").status_code == 422
            assert client.get(f"/api/v1/processes/{lifecycle(99).subject.id}/{suffix}").status_code == 404
        assert client.get(f"/api/v1/processes/{parent.subject.id}/tree?depth=6").status_code == 422
        assert client.get(f"/api/v1/processes/{parent.subject.id}/relations?limit=201").status_code == 422
        assert client.get(f"/api/v1/processes/{parent.subject.id}/tree").json()["nodes"][1]["process"]["id"] == child.subject.id
    with TestClient(create_app(migrated_database)) as client:
        assert client.get(f"/api/v1/processes/{child.subject.id}/relations").json() == body
        assert client.get(f"/api/v1/events?subject_id={child.subject.id}").json() == before
