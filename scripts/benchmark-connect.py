"""Run with core's uv environment. Own temporary DBs only; emit measured JSON."""

import json
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from sqlalchemy import event as sa_event, text
from chronoscope.application.ports import EventQuery
from chronoscope.application.queries.process_connections import GetProcessRelations, GetProcessTree
from chronoscope.domain.ids import process_instance_id
from chronoscope.domain.timestamps import format_utc_fixed
from chronoscope.infrastructure.config.settings import CoreSettings
from chronoscope.infrastructure.database.engine import create_database_engine
from chronoscope.infrastructure.database.models import metadata, events
from chronoscope.infrastructure.database.repositories import EventRepository, build_process_ids_statement, build_list_statement

HOST = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9"
BOOT = "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HA"
BASE = datetime(2026, 10, 1, tzinfo=UTC)


def dataset(count):
    rows = []
    ids = {}
    for i in range(1, count + 1):
        moment = BASE + timedelta(seconds=i)
        ids[i] = process_instance_id(host_id=HOST, boot_id=BOOT, pid=i, started_at=moment)
        # chain 1 -> 2 -> 3 -> 4; wide 5 -> 6..245; background unrelated.
        parent = i - 1 if 2 <= i <= 4 else 5 if 6 <= i <= 245 else 999999
        rows.append(dict(id=f"evt_{i:026d}", schema_version=1, timestamp=format_utc_fixed(moment),
                         ingested_at=format_utc_fixed(moment), type="process.started", source="windows.process",
                         host_id=HOST, boot_id=BOOT, subject_id=ids[i], subject_type="process", subject_name=f"p{i}.exe",
                         actor_id=ids.get(parent), actor_type="process" if parent in ids else None,
                         attributes_json=json.dumps({"pid": i, "parent_pid": parent, "process_started_at": format_utc_fixed(moment)})))
    return rows, ids


def main():
    report = {"cold_definition": "new connection/SQLite page cache; OS cache not flushed", "runs": []}
    scratch = Path(__file__).resolve().parents[1] / "data" / "connect-benchmark"
    scratch.mkdir(parents=True, exist_ok=True)
    for count in (1000, 20000):
        for indexed in (False, True):
            with tempfile.TemporaryDirectory(dir=scratch) as folder:
                engine = create_database_engine(CoreSettings(database_path=Path(folder) / "bench.db"))
                metadata.create_all(engine)
                with engine.begin() as conn:
                    for field in ("pid", "parent_pid"):
                        conn.exec_driver_sql(f"DROP INDEX IF EXISTS ix_events_process_{field}")
                        if indexed:
                            conn.exec_driver_sql(f"CREATE INDEX ix_events_process_{field} ON events (host_id, boot_id, json_extract(attributes_json, '$.{field}'), subject_id)")
                rows, ids = dataset(count)
                started = time.perf_counter()
                with engine.begin() as conn:
                    conn.execute(events.insert(), rows)
                ingest_ms = (time.perf_counter() - started) * 1000
                repository = EventRepository(engine)
                operations = {
                    "relations": lambda: GetProcessRelations(repository).execute(ids[1]),
                    "tree_chain": lambda: GetProcessTree(repository).execute(ids[1]),
                    "tree_wide": lambda: GetProcessTree(repository).execute(ids[5]),
                    "related_events": lambda: repository.list(EventQuery(entity_id=ids[5], limit=20)),
                }
                measurements = {}
                for name, operation in operations.items():
                    samples, queries = [], []
                    def capture(conn, cursor, statement, parameters, context, executemany):
                        if statement.lstrip().startswith("SELECT"):
                            queries.append(statement)
                    engine.dispose()
                    sa_event.listen(engine, "before_cursor_execute", capture)
                    for repeat in range(8):
                        queries.clear()
                        started = time.perf_counter()
                        result = operation()
                        samples.append((time.perf_counter() - started) * 1000)
                    sa_event.remove(engine, "before_cursor_execute", capture)
                    measurements[name] = {"first_ms": round(samples[0], 3), "warm_median_ms": round(statistics.median(samples[1:]), 3),
                                          "warm_max_ms": round(max(samples[1:]), 3), "selects": len(queries)}
                    if name == "tree_chain":
                        assert len(result.nodes) == 4 and not result.truncated
                    if name == "tree_wide":
                        assert len(result.nodes) == 200 and result.truncated
                plans = {}
                for field in ("pid", "parent_pid"):
                    statement = build_process_ids_statement(((HOST, BOOT, 1),), field=field, limit=2001)
                    sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
                    with engine.connect() as conn:
                        plans[field] = [row[-1] for row in conn.execute(text("EXPLAIN QUERY PLAN " + sql))]
                statement = build_list_statement(EventQuery(entity_id=ids[5], limit=20))
                with engine.connect() as conn:
                    plans["entity"] = [row[-1] for row in conn.execute(text("EXPLAIN QUERY PLAN " + str(statement.compile(engine, compile_kwargs={"literal_binds": True}))))]
                report["runs"].append({"events": count, "indexed": indexed, "insert_ms": round(ingest_ms, 3), "measurements": measurements, "plans": plans})
                engine.dispose()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
