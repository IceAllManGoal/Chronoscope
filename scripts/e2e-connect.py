"""Real Windows Agent/Core/processes/SQLite/API/CLI, in an isolated run directory.

From core: uv run python ../scripts/e2e-connect.py --keep-core
The report explicitly leaves browser verification pending; open its UI URL.
Without --keep-core all owned processes stop. Never touches the default DB.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, UTC

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-core", action="store_true")
    parser.add_argument("--ui-fixtures", action="store_true", help="add clearly synthetic XSS/conflict fixture AFTER live verification")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("BLOCKED: real Windows required")
    run = ROOT / "data" / ("connect-e2e-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    run.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = run / "chronoscope.toml"
    config.write_text(f'''[core]
host = "127.0.0.1"
port = {port}
[storage]
database_path = "{(run / 'history.db').as_posix()}"
[agent]
data_directory = "{(run / 'agent').as_posix()}"
flush_interval_ms = 200
[collectors.process]
enabled = true
capture_path = false
capture_command_line = false
capture_user = false
[collectors.event_log]
enabled = true
channels = ["System", "Application"]
capture_message = false
''', encoding="utf-8")
    env = dict(os.environ, CHRONOSCOPE_CONFIG=str(config), PYTHONUTF8="1", TEMP=str(run), TMP=str(run))
    base = f"http://127.0.0.1:{port}"
    owned = []
    logs = []
    core = None
    report = {"directory": str(run), "url": base + "/ui/", "attempts": [], "browser": "PENDING"}

    def spawn(command, name, cwd=ROOT):
        output = (run / (name + ".log")).open("w", encoding="utf-8")
        logs.append(output)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        owned.append(process)
        return process

    def get(path, **params):
        url = base + "/api/v1" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)

    def wait_for(predicate, seconds=25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError("condition timed out")

    def all_process_events():
        rows, cursor = [], None
        while True:
            params = {"source": "windows.process", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            page = get("/events", **params)
            rows.extend(page["events"])
            cursor = page["next_cursor"]
            if not cursor:
                return rows

    def ps(script, name):
        path = run / (name + ".ps1")
        path.write_text(script, encoding="utf-8-sig")
        return spawn(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)], name)

    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "core", env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        core = spawn([sys.executable, "-m", "chronoscope"], "core", ROOT / "core")
        wait_for(lambda: get("/health")["database"] == "ok")
        # The missing parent exists BEFORE the collector subscribes and is kept
        # alive until AFTER the collector stops, so no parent lifecycle is sent.
        quoted = str(run).replace("'", "''")
        orphan_parent = ps(f"""
$runPath = '{quoted}'
Set-Content -LiteralPath (Join-Path $runPath 'orphan-ready.txt') -Value $PID
while (-not (Test-Path -LiteralPath (Join-Path $runPath 'orphan-go.txt'))) {{ Start-Sleep -Milliseconds 200 }}
$child = Start-Process -FilePath ping.exe -ArgumentList '-n 12 127.0.0.1' -PassThru -WindowStyle Hidden
Set-Content -LiteralPath (Join-Path $runPath 'orphan-child.txt') -Value $child.Id
$child.WaitForExit()
Start-Sleep -Seconds 120
""", "orphan-parent")
        wait_for(lambda: (run / "orphan-ready.txt").exists())
        agent_dll = ROOT / "agent/src/Chronoscope.Agent.Host/bin/Debug/net8.0-windows/Chronoscope.Agent.Host.dll"
        agent = spawn(["dotnet", str(agent_dll)], "agent")
        wait_for(lambda: '"collector_started"' in (run / "agent.log").read_text(encoding="utf-8-sig"))
        time.sleep(3)
        (run / "orphan-go.txt").touch()
        chain = ps(f"""
$runPath = '{quoted}'
Start-Sleep -Seconds 4
$child = Start-Process -FilePath cmd.exe -ArgumentList '/d /c ping.exe -n 16 127.0.0.1 >NUL' -PassThru -WindowStyle Hidden
Set-Content -LiteralPath (Join-Path $runPath 'chain-cmd.txt') -Value $child.Id
Start-Sleep -Seconds 2
$ping = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($child.Id)" | Where-Object Name -eq 'PING.EXE'
$ping.ProcessId | Set-Content -LiteralPath (Join-Path $runPath 'chain-ping.txt')
$child.WaitForExit()
Start-Sleep -Seconds 3
""", "chain")
        print("RUN_DIRECTORY=" + str(run), flush=True)
        chain.wait(timeout=40)
        time.sleep(4)
        pids = [chain.pid, int((run / "chain-cmd.txt").read_text().strip()), int((run / "chain-ping.txt").read_text().strip())]
        orphan_pid = int((run / "orphan-child.txt").read_text().strip())
        rows = all_process_events()
        found = {pid: [e for e in rows if e["attributes"].get("pid") == pid] for pid in pids + [orphan_pid]}
        attempt = {"launched_chain": 3, "chain_pids": pids, "orphan_child_pid": orphan_pid,
                   "launched_target_processes": 5, "unobserved_parent_pid": orphan_parent.pid,
                   "observed_chain_starts": sum(any(e["type"] == "process.started" for e in found[p]) for p in pids),
                   "observed_chain_exits": sum(any(e["type"] == "process.exited" for e in found[p]) for p in pids),
                   "instances": {str(p): sorted({e["subject"]["id"] for e in found[p]}) for p in found}}
        report["attempts"].append(attempt)
        assert all(found[p] for p in found), "collector missed a target; see recorded attempt"
        ids = [found[p][0]["subject"]["id"] for p in pids]
        report["root_id"] = ids[0]
        report["root_event_id"] = found[pids[0]][0]["id"]
        relation_bodies = [get(f"/processes/{id_}/relations") for id_ in ids]
        attempt["resolved_chain_links"] = sum(relation_bodies[i]["parent"]["parent_instance_id"] == ids[i-1] for i in (1, 2))
        report["relations"] = relation_bodies
        assert attempt["resolved_chain_links"] == 2
        tree = get(f"/processes/{ids[0]}/tree")
        report["tree"] = tree
        assert set(ids) <= {n["process"]["id"] for n in tree["nodes"]}
        orphan_id = found[orphan_pid][0]["subject"]["id"]
        orphan = get(f"/processes/{orphan_id}/relations")
        report["missing_parent"] = orphan
        assert orphan["parent"]["parent_pid"] == orphan_parent.pid
        assert orphan["parent_process"] is None
        assert not any(e["attributes"].get("pid") == orphan_parent.pid for e in rows)
        attempt["orphan_child_start_observed"] = any(e["type"] == "process.started" for e in found[orphan_pid])
        attempt["orphan_child_exit_observed"] = any(e["type"] == "process.exited" for e in found[orphan_pid])
        for command in (["process-relations", ids[1]], ["process-tree", ids[0]], ["events", "--entity-id", ids[0]]):
            cli = subprocess.run([sys.executable, "-m", "chronoscope.cli.main", "--core-url", base, *command],
                                 cwd=ROOT / "core", env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
            (run / (command[0] + ".txt")).write_text(cli.stdout + cli.stderr, encoding="utf-8")
            assert cli.returncode == 0, cli.stderr
        report["system_event_count"] = get("/events", source="windows.eventlog", limit=1000)["count"]
        agent.terminate(); agent.wait(timeout=10)
        before = get(f"/processes/{ids[1]}/relations")
        core.terminate(); core.wait(timeout=10)
        core = spawn([sys.executable, "-m", "chronoscope"], "core-restart", ROOT / "core")
        wait_for(lambda: get("/health")["database"] == "ok")
        assert get(f"/processes/{ids[1]}/relations") == before
        report["restart"] = "PASS"
        report["pipeline"] = "PASS"
        if args.ui_fixtures:
            # Synthetic fixture for UI safety only; never counted as Windows evidence.
            import uuid
            def raw_id():
                # Decimal characters are a valid subset of Crockford base32.
                return "raw_" + str(uuid.uuid4().int % (10 ** 25)).zfill(26)
            moment = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            envelope = {"schema_version": 1, "collector": "windows.process", "collector_version": "ui-test",
                        "observed_at": moment, "source_timestamp": moment,
                        "host_id": "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9", "boot_id": "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HA"}
            payload = {"pid": 88002, "name": "<img src=x onerror=alert('connect')>.exe", "process_started_at": moment}
            raw = [{**envelope, "raw_event_id": raw_id(), "payload_type": kind,
                    "payload": {**payload, "parent_pid": parent}} for kind, parent in (("process_start", 88000), ("process_exit", 88001))]
            request = urllib.request.Request(base + "/api/v1/ingest/raw-events",
                                             data=json.dumps({"schema_version": 1, "events": raw}).encode(),
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                assert json.load(response)["accepted"] == 2
            fixture = get("/events", limit=1)["events"][0]
            report["synthetic_ui_fixture"] = {"event_id": fixture["id"], "instance_id": fixture["subject"]["id"]}
    except Exception as exc:
        report["pipeline"] = "FAIL"
        report["error"] = repr(exc)
    finally:
        report["core_pid"] = core.pid if core and args.keep_core else None
        (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for process in reversed(owned):
            if process.poll() is None and not (process is core and args.keep_core):
                process.terminate()
                process.wait(timeout=10)
        for stream in logs:
            stream.close()
        print(json.dumps({"report": str(run / "report.json"), "pipeline": report.get("pipeline"), "error": report.get("error"),
                          "url": report["url"], "core_pid": report["core_pid"]}, ensure_ascii=False), flush=True)
    return 0 if report.get("pipeline") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
