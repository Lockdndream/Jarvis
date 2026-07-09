"""Milestone 6, Phase 2: deterministic real-execution proof.

Not a pytest test — like tests/m5_validate2.py, this requires a real
opencode.exe and is gated/manual, run explicitly:

    python tests/m6_execution_probe.py

It proves (or disproves) that a Jarvis-created OpenCode task actually
executes work, using the SAME production code path Jarvis uses
(OpenCodeSupervisor.start_session -> OpenCodeAdapter.send_prompt), bypassing
the conversational LLM/Supervisor layer entirely — that layer's tool-routing
correctness is already covered by Milestone 5's real validation; this test
is specifically about whether OpenCode itself executes the work Jarvis
submits to it, using a filesystem artifact as independent, primary proof
(per the Milestone 6 spec: HTTP 200/204 alone, a tool-call log alone, LLM
prose, or elapsed silence are explicitly NOT acceptable proof).

Safe sandbox only: tests/test_projects/safe-test. Only the probe artifact
itself is cleaned up after a successful run — nothing else in the sandbox
is touched by this script, and any *other* file changes detected there are
reported as a finding, not silently accepted.

Known at the time this test was written (Milestone 6 Phase 1/3
investigation, 2026-07-09): every OpenCode session created via the API in
this environment fails with a server-side `SQLiteError: NOT NULL constraint
failed: session_message.seq` before any model call — see SESSION.md,
Milestone 6 Phase 3. This test is EXPECTED to currently report FAILED
execution for that documented, out-of-Jarvis's-control reason. It is built
and committed anyway so it is immediately usable as real proof once that
upstream issue is resolved, without needing to reconstruct it then.
"""
import asyncio
import os
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

import app.database as db
from app.connection_manager import ConnectionManager
from app.task_manager import TaskManager
from app.integrations.opencode_supervisor import OpenCodeSupervisor

SAFE_DIR = os.path.join(os.path.dirname(__file__), "test_projects", "safe-test")
TERMINAL_STATES = ("completed", "failed", "cancelled")
WAIT_TIMEOUT = 90


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def snapshot_dir(path):
    snap = {}
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                snap[fp] = os.stat(fp).st_mtime_ns
            except OSError:
                pass
    return snap


async def main():
    probe_id = uuid.uuid4().hex[:10]
    marker = f"JARVIS_EXECUTION_PROBE_{probe_id}"
    filename = f"jarvis_execution_probe_{probe_id}.txt"
    filepath = os.path.join(SAFE_DIR, filename)
    instruction = (
        f"Create a new file named exactly `{filename}` in the current directory. "
        f"Its entire contents must be exactly this single line and nothing else: {marker}\n"
        f"Do not modify, create, or delete any other file."
    )

    log("=" * 60)
    log("MILESTONE 6 PHASE 2: DETERMINISTIC EXECUTION PROOF")
    log("=" * 60)
    log(f"probe_id={probe_id}")
    log(f"expected file: {filepath}")
    log(f"expected content: {marker!r}")

    f, tmp_db = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = tmp_db
    db.init_db()

    before_snapshot = snapshot_dir(SAFE_DIR)

    cm = ConnectionManager()
    tm = TaskManager(cm)
    sv = OpenCodeSupervisor(cm, tm)

    evidence = {}

    try:
        await sv.start()
        log("A. OpenCode server started/connected")

        result = await sv.start_session(SAFE_DIR, instruction)
        task_id = result["task_id"]
        session_id = result["session_id"]
        evidence["task_session_mapping"] = db.get_opencode_task(task_id) is not None
        log(f"A. Jarvis task/session mapping exists: task_id={task_id} session_id={session_id} -> {evidence['task_session_mapping']}")
        evidence["submission_succeeded"] = True  # start_session raised nothing
        log("B. Instruction submission API succeeded (no exception from send_prompt)")

        log(f"Waiting up to {WAIT_TIMEOUT}s for a verified terminal state...")
        completed = await sv.wait_for_completion(task_id, timeout=WAIT_TIMEOUT)
        task = db.get_task(task_id)
        oc_task = db.get_opencode_task(task_id)
        evidence["terminal_state_reached"] = completed and task["status"] in TERMINAL_STATES
        evidence["final_status"] = task["status"]
        evidence["last_evidence_type"] = oc_task.get("last_evidence_type") if oc_task else None
        log(f"G. Terminal state via verified OpenCode semantics: reached={completed}, "
            f"status={task['status']}, last_evidence={evidence['last_evidence_type']}")

    finally:
        await sv.stop()

    after_snapshot = snapshot_dir(SAFE_DIR)

    # D/E. Filesystem artifact — the primary, independent proof.
    evidence["file_exists"] = os.path.isfile(filepath)
    file_content = None
    if evidence["file_exists"]:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            file_content = fh.read()
    evidence["content_exact_match"] = file_content is not None and file_content.strip() == marker
    log(f"D. Expected file exists: {evidence['file_exists']}")
    log(f"E. Content exact match: {evidence['content_exact_match']} (actual: {file_content!r})")

    # F. No unexpected files modified.
    unexpected_changes = []
    all_paths = set(before_snapshot) | set(after_snapshot)
    for p in all_paths:
        if p == filepath:
            continue
        if before_snapshot.get(p) != after_snapshot.get(p):
            unexpected_changes.append(p)
    evidence["no_unexpected_changes"] = not unexpected_changes
    log(f"F. No unexpected file modifications: {evidence['no_unexpected_changes']} "
        f"(changed: {unexpected_changes})" if unexpected_changes else "F. No unexpected file modifications: True")

    # C. Relevant OpenCode events observed (approximate — did we get past 'pending'?)
    evidence["events_observed"] = evidence.get("last_evidence_type") is not None
    log(f"C. Relevant OpenCode events observed: {evidence['events_observed']}")

    overall_pass = all([
        evidence["task_session_mapping"],
        evidence["submission_succeeded"],
        evidence["file_exists"],
        evidence["content_exact_match"],
        evidence["no_unexpected_changes"],
        evidence["terminal_state_reached"],
        evidence["final_status"] == "completed",
    ])

    log("=" * 60)
    log(f"RESULT: {'PASS — real execution proven' if overall_pass else 'FAIL — real execution NOT proven'}")
    log("=" * 60)
    for k, v in evidence.items():
        log(f"  {k}: {v}")

    if evidence["file_exists"] and evidence["content_exact_match"]:
        os.remove(filepath)
        log(f"Cleaned up probe artifact: {filepath}")
    elif evidence["file_exists"]:
        log(f"NOTE: leaving unexpected-content artifact in place for inspection: {filepath}")

    os.unlink(tmp_db)
    return overall_pass


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
