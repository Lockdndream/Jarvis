"""Jarvis Control Center — demo/test data seeder.

Drives a real, running Jarvis instance (started separately, see
docs/... or the Control Center PR description for exact invocation) so
every dashboard panel has representative data to render, without needing
a paired phone or a paid LLM/OpenCode call for the whole tour.

Two kinds of seeding happen here, and the script prints which is which:

  1. REAL, LIVE traffic — driven over an actual WebSocket connection to
     the running server, exactly like the Android companion or the PWA
     would. This exercises the genuine event/broadcast path end to end:
     local task lifecycle (/mock-agent — a real subprocess, a real
     question, a real answer, a real completion) and a plain-text
     Supervisor turn (using the server's already-configured LLM
     provider — FakeLLMProvider unless JARVIS_LLM_API_KEY is set).
     These appear on the dashboard THE MOMENT they happen, via the
     WebSocket broadcast stream — no polling, no reload needed.

  2. SEEDED FIXTURE rows — written directly into the same SQLite
     database file the server itself uses (WAL mode makes this safe
     concurrently), for scenarios impractical to trigger for free in a
     scripted demo: a completed OpenCode task, a FAILED OpenCode task
     (so the Failed bucket / Recent Alerts / red timeline node have
     something to show), a full voice-session lifecycle, and a
     Supervisor tool-call turn. These are clearly logged as "(seeded)"
     below and will NOT push live over the WebSocket — a reviewer sees
     them the next time the dashboard's 5s connectivity poll or a page
     reload calls GET /api/dashboard/snapshot (which reads the same
     rows), consistent with this feature's rule that only genuinely
     unobservable-any-other-way state gets a direct-DB shortcut.

Usage:
    # 1. Start the server first (separate terminal, isolated ports —
    #    see the testing instructions for exact env vars):
    #    JARVIS_TEST_MODE=1 JARVIS_OPENCODE_PORT=4198 ... uvicorn app.main:app --port 8010
    #
    # 2. Then, from the repo root, with the SAME cwd the server used
    #    (DB_PATH="jarvis.db" is a relative path):
    python scripts/dashboard_demo_seed.py --http-port 8010
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import websockets
import httpx

import app.database as db

def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _wait_for_type(ws, wanted_type: str, max_seconds: float = 8.0):
    """Polls for a specific frame type, ignoring everything else in
    between (there's plenty of unrelated traffic — task_stdout lines,
    the initial snapshot dump — on the way to the one we care about).
    Returns the parsed frame, or None if max_seconds elapses first.
    Short per-recv timeouts (not one long one) keep this responsive
    without ever calling recv() after the connection has started
    closing — the real cause of a "Cannot call send once a close
    message has been sent" transport error hit while building this
    script with a fixed-window drain-then-move-on approach."""
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        data = json.loads(frame)
        if data.get("type") == wanted_type:
            return data
    return None


async def run_live_traffic(http_port: int) -> None:
    base = f"http://127.0.0.1:{http_port}"
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base}/api/ws-token", json={"client_id": "demo-seed-phone"})
        token = r.json().get("token", "") if r.status_code == 200 else ""

    ws_url = f"ws://127.0.0.1:{http_port}/ws" + (f"?token={token}" if token else "")
    log(f"Connecting to {ws_url} as a simulated phone...")
    async with websockets.connect(ws_url) as ws:
        # Drain the initial snapshot dump (history/running_tasks/...).
        await asyncio.wait_for(ws.recv(), timeout=5)

        log("Sending device_status (simulated Android companion)...")
        await ws.send(json.dumps({
            "type": "device_status",
            "device_id": "demo-pixel-8",
            "capabilities": {"voice": True, "wakeword": True, "widget": False, "notifications": True, "foregroundService": True},
            "pairing_state": "paired",
            "batteryOptimizationExempt": True,
            "notificationPermissionGranted": True,
            "connectionGeneration": 1,
        }))
        await ws.send(json.dumps({"type": "heartbeat"}))

        log("Starting a plain demo task (/demo-task) — completes cleanly...")
        await ws.send(json.dumps({"type": "user_message", "content": "/demo-task"}))
        await _wait_for_type(ws, "task_completed", max_seconds=20)

        log("Starting a second demo task, then cancelling it (/demo-task, /cancel)...")
        await ws.send(json.dumps({"type": "user_message", "content": "/demo-task"}))
        started = await _wait_for_type(ws, "task_started", max_seconds=10)
        task_id = json.loads(started["content"])["task_id"] if started else None
        if task_id:
            await ws.send(json.dumps({"type": "user_message", "content": f"/cancel {task_id}"}))
            await _wait_for_type(ws, "task_cancelled", max_seconds=10)
        else:
            log("WARNING: no task_started observed for the second demo task — cancel-flow demo skipped.")

        log("Starting the mock agent (/mock-agent) — will ask a real question...")
        await ws.send(json.dumps({"type": "user_message", "content": "/mock-agent"}))
        question = await _wait_for_type(ws, "question_asked", max_seconds=15)
        question_id = json.loads(question["content"])["question_id"] if question else None
        if question_id:
            log(f"Answering question {question_id[:8]} with 'A' (attention resolves, task resumes)...")
            await ws.send(json.dumps({"type": "user_message", "content": f"/answer {question_id} A"}))
            await _wait_for_type(ws, "task_completed", max_seconds=15)
        else:
            log("WARNING: no question_asked observed for the mock agent — Attention Center demo skipped.")

        log("Sending a plain-text message to exercise the Supervisor turn broadcast...")
        await ws.send(json.dumps({"type": "user_message", "content": "What's the status of everything?"}))
        await _wait_for_type(ws, "supervisor_message", max_seconds=15)

    log("Live WebSocket traffic complete.")


def seed_fixture_rows() -> None:
    log("(seeded) Writing a completed OpenCode task directly to the DB...")
    task_id = f"oc_{uuid.uuid4().hex[:12]}"
    session_id = f"ses_{uuid.uuid4().hex[:16]}"
    db.create_task_record(task_id, "OpenCode: summarize the auth module", "summarize the auth module")
    db.create_opencode_task_record(task_id, session_id, "D:\\Projects\\Jarvis", "summarize the auth module")
    db.save_event("opencode_task_created", json.dumps({"task_id": task_id, "session_id": session_id, "instruction": "summarize the auth module"}))
    db.update_opencode_task_evidence(task_id, "message.part.delta")
    db.update_task_status(task_id, "completed", 0)
    db.update_opencode_task_status(task_id, "completed")
    db.save_event("opencode_task_completed", json.dumps({"task_id": task_id, "status": "completed", "source": "opencode"}))

    log("(seeded) Writing a FAILED OpenCode task directly to the DB (Failed bucket + alert)...")
    fail_task_id = f"oc_{uuid.uuid4().hex[:12]}"
    fail_session_id = f"ses_{uuid.uuid4().hex[:16]}"
    db.create_task_record(fail_task_id, "OpenCode: migrate the database schema", "migrate the database schema")
    db.create_opencode_task_record(fail_task_id, fail_session_id, "D:\\Projects\\Jarvis", "migrate the database schema")
    db.save_event("opencode_task_created", json.dumps({"task_id": fail_task_id, "session_id": fail_session_id, "instruction": "migrate the database schema"}))
    db.update_task_status(fail_task_id, "failed", -1)
    db.update_opencode_task_status(fail_task_id, "failed")
    db.save_event("opencode_error", json.dumps({"task_id": fail_task_id, "raw": {"message": "rate limited"}, "source": "opencode"}))
    db.save_event("opencode_task_completed", json.dumps({"task_id": fail_task_id, "status": "failed", "source": "opencode"}))

    log("(seeded) Writing a full voice-session lifecycle...")
    vs_id = f"vs_{uuid.uuid4().hex[:12]}"
    conv_id = db.new_conversation_id()
    db.create_voice_session(vs_id, conv_id, None)
    db.update_voice_session_state(vs_id, "opening")
    db.update_voice_session_state(vs_id, "listening")
    db.save_event("voice_session_lifecycle", json.dumps({"voice_session_id": vs_id, "conversation_id": conv_id, "attention_request_id": None, "phase": "opened"}))
    db.save_event("voice_session_lifecycle", json.dumps({"voice_session_id": vs_id, "conversation_id": conv_id, "attention_request_id": None, "phase": "transcript_received", "transcript": "Hey Jarvis, what's running right now?"}))
    db.save_event("voice_session_lifecycle", json.dumps({"voice_session_id": vs_id, "conversation_id": conv_id, "attention_request_id": None, "phase": "turn_completed", "response": "You have one OpenCode task running.", "voice_session_state": "listening"}))
    db.update_voice_session_state(vs_id, "closed", termination_reason="client_requested")
    db.save_event("voice_session_lifecycle", json.dumps({"voice_session_id": vs_id, "conversation_id": conv_id, "attention_request_id": None, "phase": "closed", "reason": "client_requested"}))

    log("(seeded) Writing a Supervisor tool-call turn (start_opencode_task)...")
    db.save_event("supervisor_turn_started", json.dumps({"conversation_id": conv_id, "user_message": "Can you look into the failing tests in the payments module?", "bound_attention_request_id": None}))
    db.save_event("supervisor_tool_call", json.dumps({"conversation_id": conv_id, "sequence": 1, "tool": "start_opencode_task", "args": {"project_alias": "jarvis", "instruction": "Investigate the failing tests in the payments module"}, "result_summary": f"OpenCode task started: task_id={task_id}"}))
    db.save_event("supervisor_turn", json.dumps({"conversation_id": conv_id, "user_message": "Can you look into the failing tests in the payments module?", "response": "I've started looking into the failing payments tests — I'll let you know what I find.", "tool_call_count": 1}))

    log("(seeded) Writing a PERMISSION attention request directly...")
    db.create_attention_request(
        attention_request_id=f"attn_{uuid.uuid4().hex[:12]}",
        conversation_id=conv_id, task_id=fail_task_id,
        source_type="opencode_permission", source_id=f"perm_{uuid.uuid4().hex[:8]}",
        attention_type="PERMISSION", urgency="HIGH",
        summary="OpenCode wants to run a database migration script.",
        context_json=None, contact_policy=None,
        dedup_key=f"seed-permission-{uuid.uuid4().hex[:8]}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-port", type=int, default=8010)
    parser.add_argument("--skip-live", action="store_true", help="Only write seeded fixture rows, skip the live WebSocket traffic.")
    parser.add_argument("--skip-seed", action="store_true", help="Only drive live traffic, skip the direct-DB fixture rows.")
    args = parser.parse_args()

    if not args.skip_seed:
        seed_fixture_rows()
    if not args.skip_live:
        asyncio.run(run_live_traffic(args.http_port))

    log(f"Done. Open http://127.0.0.1:{args.http_port}/dashboard to view the Control Center.")


if __name__ == "__main__":
    main()
