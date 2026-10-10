"""Temporarily inject seven defects, run focused regressions, restore exact bytes.

Run from core: uv run python ../scripts/check-connect-mutations.py
Do not run concurrently with other tests or edits of these files.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "core/chronoscope/domain/process_relationships.py"
TREE = ROOT / "core/chronoscope/application/queries/process_connections.py"
REPO = ROOT / "core/chronoscope/infrastructure/database/repositories.py"
TEST = "tests/test_process_connections.py"

MUTANTS = [
    ("PID without host/boot", DOMAIN,
     "p.key() == child.key(parent_pid) or child.key(parent_pid) in p.observed_keys", "p.pid == parent_pid",
     "test_incompatible_parent_is_never_linked"),
    ("ignore parent exit", DOMAIN,
     "if parent.exited_at is not None and parent.exited_at < child.started_at:", "if False:",
     "test_incompatible_parent_is_never_linked"),
    ("ignore parent start", DOMAIN, "if parent.started_at > child.started_at:", "if False:",
     "test_incompatible_parent_is_never_linked"),
    ("hide conflicting evidence", DOMAIN, "if child.problems:", "if False:",
     "test_conflicts_are_not_silently_selected"),
    ("remove cycle guard", TREE, "if child.process.id in seen:", "if False:",
     "test_cycles_terminate_without_duplicate_nodes"),
    ("remove node bound", TREE, "if len(nodes) >= max_nodes:", "if False:",
     "test_chain_tree_limits_and_stable_order"),
    ("temporal proximity becomes entity relationship", REPO, "*conditions, role == query.entity_id", "*conditions, True",
     "test_entity_union_pagination_and_temporal_non_causality"),
]


def main():
    scratch = ROOT / "data/connect-mutations"
    scratch.mkdir(parents=True, exist_ok=True)
    results = []
    env = dict(os.environ, PYTHONUTF8="1", TEMP=str(scratch), TMP=str(scratch))
    for name, file, old, new, test in MUTANTS:
        original = file.read_bytes()
        source = original.decode("utf-8")
        assert source.count(old) == 1, (name, source.count(old))
        try:
            file.write_text(source.replace(old, new), encoding="utf-8", newline="\n")
            # Force source recompile even on filesystems with coarse mtimes.
            for cached in (file.parent / "__pycache__").glob(file.stem + ".*.pyc"):
                cached.unlink()
            with tempfile.TemporaryDirectory(dir=scratch) as temporary:
                run = subprocess.run([sys.executable, "-m", "pytest", TEST + "::" + test, "--basetemp", temporary],
                                     cwd=ROOT / "core", env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
            # Exit 1 means assertion failures; syntax/import errors do not count.
            killed = run.returncode == 1 and "FAILED" in run.stdout and "AssertionError" in run.stdout
            results.append({"mutation": name, "detected": killed, "exit_code": run.returncode})
            print(json.dumps(results[-1]), flush=True)
            if not killed:
                print(run.stdout, run.stderr)
        finally:
            file.write_bytes(original)
            for cached in (file.parent / "__pycache__").glob(file.stem + ".*.pyc"):
                cached.unlink()
    (scratch / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if all(r["detected"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
