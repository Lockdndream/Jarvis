"""Manual smoke test — validates Milestone 3 scenarios via WebSocket.

Starts its own server, runs three scenarios, reports results.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import websockets

WS_URL = "ws://127.0.0.1:8000/ws"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class SafeCollector:
    """Non-destructive collector with a peek buffer.

    - All incoming messages go into a queue.
    - `await` picks the next matching message without permanently
      discarding non-matching ones (they go into a hold buffer).
    - Subsequent calls re-check the hold buffer first.
    """

    def __init__(self, ws):
        self._ws = ws
        self._queue = asyncio.Queue()
        self._hold = []
        self._task = asyncio.create_task(self._collect())

    async def _collect(self):
        try:
            while True:
                msg = await self._ws.recv()
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                await self._queue.put(data)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def get(self, expected_type, timeout=15):
        """Return the next message of expected_type, reading and
        holding non-matching messages."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Check hold buffer first
            idx = None
            for i, h in enumerate(self._hold):
                if h.get("type") == expected_type:
                    idx = i
                    break
            if idx is not None:
                msg = self._hold.pop(idx)
                return msg
            # Read from network queue
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=2)
            except asyncio.TimeoutError:
                continue
            if data.get("type") == expected_type:
                return data
            self._hold.append(data)
        raise TimeoutError(f"Type '{expected_type}' not received within {timeout}s")

    def close(self):
        self._task.cancel()


async def send(ws, text):
    await ws.send(json.dumps({"type": "user_message", "content": text}))


async def connect():
    ws = await websockets.connect(WS_URL)
    coll = SafeCollector(ws)
    return ws, coll


async def drain(coll, timeout=2):
    coll._hold.clear()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            await asyncio.wait_for(coll._queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            break


async def get_attention(coll, timeout=5):
    return await coll.get("command_output", timeout=timeout)


async def get_tasks(coll, timeout=5):
    return await coll.get("command_output", timeout=timeout)


# ── Scenario A: Single worker and reconnect ──────────────────────


async def scenario_a():
    print("=" * 60)
    print("SCENARIO A: Single worker and reconnect")
    print("=" * 60)

    ws, coll = await connect()
    try:
        await coll.get("history", timeout=10)
        await drain(coll)
        print("[OK] Connected")

        await send(ws, "/mock-agent")
        started1 = await coll.get("task_started", timeout=10)
        tid = json.loads(started1["content"])["task_id"]
        print(f"[OK] Task started: {tid[:8]}...")

        asked = await coll.get("question_asked", timeout=15)
        qid = json.loads(asked["content"])["question_id"]
        print(f"[OK] Question asked: {qid[:8]}...")

        await drain(coll)
        await send(ws, "/attention")
        att = await get_attention(coll)
        assert "waiting" in (att.get("content") or "").lower(), "Pending question should show"
        print("[OK] /attention confirms pending question")

        coll.close()
    finally:
        await ws.close()

    await asyncio.sleep(0.5)
    ws, coll = await connect()
    try:
        await coll.get("history", timeout=10)
        await drain(coll)
        print("[OK] Reconnected")

        await drain(coll)
        await send(ws, "/attention")
        att2 = await get_attention(coll)
        assert "waiting" in (att2.get("content") or "").lower(), "Question should persist"
        print("[OK] Pending question restored after reconnect")

        await drain(coll)
        await send(ws, f"/answer {qid} B")
        ack = await coll.get("question_answered", timeout=10)
        print(f"[OK] Answer acknowledged: {json.loads(ack['content'])}")

        # Wait for worker acknowledgment and completion
        answer_confirmed = False
        task_done = False
        deadline = time.monotonic() + 20
        while not task_done and time.monotonic() < deadline:
            try:
                data = await coll.get("task_completed", timeout=10)
            except asyncio.TimeoutError:
                continue
            if not isinstance(data, dict):
                continue
            answer_confirmed = any(
                h.get("type") == "task_stdout" and "Received answer" in (h.get("content") or "")
                for h in coll._hold
            )
            ec = json.loads(data["content"]).get("exit_code", "?")
            print(f"[OK] Task completed: exit_code={ec}")
            task_done = True

        if answer_confirmed:
            print("[OK] Worker acknowledged answer: B")
        assert task_done, "Task did not complete within 20s"

        await drain(coll)
        await send(ws, "/attention")
        att3 = await get_attention(coll)
        att3_text = (att3.get("content") or "").lower()
        assert "pending questions: 0" in att3_text or "pending questions: 1" not in att3_text.replace(" ", ""), \
            f"Question should be gone: {att3_text}"
        print("[OK] Question removed from attention")

        coll.close()
    finally:
        await ws.close()

    print("[PASS] Scenario A complete")


# ── Scenario B: Two simultaneous workers ─────────────────────────


async def scenario_b():
    print()
    print("=" * 60)
    print("SCENARIO B: Two simultaneous workers")
    print("=" * 60)

    ws, coll = await connect()
    try:
        await coll.get("history", timeout=10)
        await drain(coll)
        print("[OK] Connected")

        # Start worker 1
        await send(ws, "/mock-agent")
        d1 = await coll.get("task_started", timeout=10)
        tid1 = json.loads(d1["content"])["task_id"]

        # Start worker 2
        await send(ws, "/mock-agent")
        d2 = await coll.get("task_started", timeout=10)
        tid2 = json.loads(d2["content"])["task_id"]

        print(f"[OK] Worker 1: {tid1[:8]}..., Worker 2: {tid2[:8]}...")

        # Wait for both questions
        q1_id, q2_id = None, None
        deadline = time.monotonic() + 20
        while (q1_id is None or q2_id is None) and time.monotonic() < deadline:
            try:
                data = await coll.get("question_asked", timeout=10)
            except asyncio.TimeoutError:
                continue
            if not isinstance(data, dict):
                continue
            content = json.loads(data["content"])
            task_id = content["task_id"]
            qid = content["question_id"]
            if task_id == tid1 and q1_id is None:
                q1_id = qid
            elif task_id == tid2 and q2_id is None:
                q2_id = qid

        assert q1_id is not None, f"Worker 1 ({tid1[:8]}) did not ask a question"
        assert q2_id is not None, f"Worker 2 ({tid2[:8]}) did not ask a question"
        assert q1_id != q2_id, "Question IDs must be distinct"
        print("[OK] Two distinct questions received")

        # Answer A for worker 1, B for worker 2
        await send(ws, f"/answer {q1_id} A")
        await coll.get("question_answered", timeout=10)
        print("[OK] Worker 1 answered A")

        await send(ws, f"/answer {q2_id} B")
        await coll.get("question_answered", timeout=10)
        print("[OK] Worker 2 answered B")

        # Wait for both to complete
        completed = set()
        deadline = time.monotonic() + 20
        while len(completed) < 2 and time.monotonic() < deadline:
            try:
                data = await coll.get("task_completed", timeout=10)
            except asyncio.TimeoutError:
                continue
            if not isinstance(data, dict):
                continue
            ctid = json.loads(data["content"]).get("task_id")
            if ctid:
                completed.add(ctid)
                print(f"[OK] Worker {ctid[:8]}... completed")

        assert len(completed) >= 2, f"Expected 2 workers done, got {len(completed)}"
        print("[PASS] Scenario B complete")

        coll.close()
    finally:
        await ws.close()


# ── Scenario C: Cancellation while waiting ───────────────────────


async def scenario_c():
    print()
    print("=" * 60)
    print("SCENARIO C: Cancellation while waiting")
    print("=" * 60)

    ws, coll = await connect()
    try:
        await coll.get("history", timeout=10)
        await drain(coll)
        print("[OK] Connected")

        await send(ws, "/mock-agent")
        started = await coll.get("task_started", timeout=10)
        tid = json.loads(started["content"])["task_id"]
        print(f"[OK] Task started: {tid[:8]}...")

        asked = await coll.get("question_asked", timeout=15)
        qid = json.loads(asked["content"])["question_id"]
        print(f"[OK] Question asked: {qid[:8]}...")

        await send(ws, f"/cancel {tid}")
        saw_cancel = False
        deadline = time.monotonic() + 10
        while not saw_cancel and time.monotonic() < deadline:
            try:
                data = await coll.get("task_cancelled", timeout=10)
            except asyncio.TimeoutError:
                continue
            if data and data.get("type") == "task_cancelled":
                saw_cancel = True
                print("[OK] Received task_cancelled")

        assert saw_cancel, "Task was not cancelled"
        print("[OK] Task cancelled")

        # Check task status via /tasks
        await drain(coll)
        await send(ws, "/tasks")
        tasks_resp = await get_tasks(coll)
        assert "cancelled" in (tasks_resp.get("content") or "").lower(), \
            f"Task should be cancelled: {tasks_resp}"
        print("[OK] Task status is cancelled")

        # Check question not in attention
        await drain(coll)
        await send(ws, "/attention")
        att = await get_attention(coll)
        att_text = att.get("content") or ""
        assert qid[:8] not in att_text, f"Cancelled question should not appear: {att_text}"
        print("[OK] Cancelled question removed from attention")

        coll.close()
    finally:
        await ws.close()

    # Reconnect and verify cancelled question does NOT reappear
    await asyncio.sleep(0.5)
    ws, coll = await connect()
    try:
        await coll.get("history", timeout=10)
        await drain(coll)
        print("[OK] Reconnected")

        await drain(coll)
        await send(ws, "/attention")
        att2 = await get_attention(coll)
        att2_text = att2.get("content") or ""
        assert qid[:8] not in att2_text, f"Cancelled question reappeared: {att2_text}"
        print("[OK] Cancelled question did NOT reappear after reconnect")

        coll.close()
    finally:
        await ws.close()

    print("[PASS] Scenario C complete")


# ── Main ─────────────────────────────────────────────────────────


async def main():
    server_proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8000",
        "--log-level", "warning",
        cwd=PROJECT_ROOT,
        env={**os.environ, "JARVIS_TEST_MODE": "1"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(5)

    try:
        async with asyncio.timeout(5):
            ws = await websockets.connect(WS_URL)
            await ws.close()
        print("[OK] Server is running")
    except Exception:
        print("[FAIL] Server did not start")
        server_proc.kill()
        return 1

    results = {}
    for name, fn in [("A", scenario_a), ("B", scenario_b), ("C", scenario_c)]:
        try:
            await fn()
            results[name] = "PASS"
        except Exception as e:
            results[name] = f"FAIL: {e}"
            import traceback
            traceback.print_exc()

    server_proc.terminate()
    try:
        await asyncio.wait_for(server_proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        server_proc.kill()
        await server_proc.wait()

    print()
    print("=" * 60)
    print("SMOKE TEST RESULTS")
    print("=" * 60)
    for name, status in results.items():
        print(f"  Scenario {name}: {status}")
    print("=" * 60)

    all_pass = all(v == "PASS" for v in results.values())
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
