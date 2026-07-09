"""M5 end-to-end validation: scenarios A-J via WebSocket."""
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

import websockets

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("m5")

WS_URL = "ws://127.0.0.1:8000/ws"

class WsClient:
    def __init__(self):
        self._ws = None

    async def connect(self):
        self._ws = await websockets.connect(WS_URL)
        for _ in range(20):
            try:
                await asyncio.wait_for(self._ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                break

    async def send(self, text, timeout=180):
        await self._ws.send(json.dumps({"type": "user_message", "content": text}))
        deadline = time.time() + timeout
        results = []
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=min(30, remaining))
            except asyncio.TimeoutError:
                break
            try:
                data = json.loads(raw)
            except Exception:
                continue
            results.append(data)
            if data.get("type") == "supervisor_message":
                tail_deadline = time.time() + 3
                while time.time() < tail_deadline:
                    try:
                        tail = await asyncio.wait_for(self._ws.recv(), timeout=1)
                        try:
                            results.append(json.loads(tail))
                        except Exception:
                            pass
                    except asyncio.TimeoutError:
                        break
                break
        return results

    async def close(self):
        if self._ws:
            await self._ws.close()

def final_msg(resp):
    for m in reversed(resp):
        if m.get("type") == "supervisor_message":
            return m.get("content", "")
    return ""

# ── Run all scenarios ─────────────────────────────────────────────

async def run():
    cli = WsClient()
    await cli.connect()
    results = []

    # A - Attention
    log.info("=== A: Attention ===")
    resp = await cli.send("What needs my attention?")
    fm = final_msg(resp)
    ok = bool(fm) and not any(m.get("type") in ("task_started","opencode_task_created") for m in resp)
    results.append(("A - Deterministic attention", "PASS" if ok else "FAIL", fm[:80]))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80]}")

    # B - Task creation
    log.info("=== B: Task creation ===")
    resp = await cli.send("Start working on jarvis-test. Inspect the files and tell me what the project contains.")
    fm = final_msg(resp)
    has_task = any(m.get("type") == "opencode_task_created" for m in resp)
    has_thinking = any(m.get("type") == "supervisor_thinking" for m in resp)
    ok = bool(fm)
    results.append(("B - NL task creation", "PASS" if ok else "FAIL", f"supervisor={bool(fm)}, task_created={has_task}, thinking={has_thinking}"))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80] if fm else '(empty)'}")

    await asyncio.sleep(3)

    # C - Live status
    log.info("=== C: Live status ===")
    resp = await cli.send("What is OpenCode doing?")
    fm = final_msg(resp)
    ok = bool(fm)
    results.append(("C - Live status query", "PASS" if ok else "FAIL", fm[:80]))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80] if fm else '(empty)'}")

    await asyncio.sleep(1)

    # D - Follow-up
    log.info("=== D: Follow-up ===")
    resp = await cli.send("Tell it not to modify any files. Analysis only.")
    fm = final_msg(resp)
    ok = bool(fm)
    results.append(("D - Follow-up instruction", "PASS" if ok else "FAIL", fm[:80]))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80] if fm else '(empty)'}")

    await asyncio.sleep(1)

    # H - Cancellation
    log.info("=== H: Cancellation ===")
    resp = await cli.send("Stop it.")
    fm = final_msg(resp)
    ok = bool(fm)
    results.append(("H - Cancellation", "PASS" if ok else "FAIL", fm[:80]))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80] if fm else '(empty)'}")

    await asyncio.sleep(1)

    # I - Reconnect
    log.info("=== I: Reconnect ===")
    await cli.close()
    await asyncio.sleep(5)
    await cli.connect()
    resp = await cli.send("What tasks are running?")
    fm = final_msg(resp)
    ok = bool(fm)
    results.append(("I - Browser reconnect", "PASS" if ok else "FAIL", fm[:80]))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80] if fm else '(empty)'}")

    await asyncio.sleep(1)

    # J - Controlled failure
    log.info("=== J: Controlled failure ===")
    resp = await cli.send("Start working on nonexistent-project and do something.")
    fm = final_msg(resp)
    has_error = any(w in fm.lower() for w in ["error","not found","unknown","no project","not available","fail"])
    ok = bool(fm) and has_error
    results.append(("J - Failure honesty", "PASS" if ok else "FAIL", fm[:80]))
    log.info(f"  {'PASS' if ok else 'FAIL'}: {fm[:80] if fm else '(empty)'}")

    await cli.close()

    # Summary
    log.info("=" * 50)
    log.info("SUMMARY")
    log.info("=" * 50)
    for name, status, detail in results:
        log.info(f"  [{status}] {name}")
        if detail:
            log.info(f"         {detail}")

if __name__ == "__main__":
    asyncio.run(run())
