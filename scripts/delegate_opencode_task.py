"""Milestone 9B.2 (ADR-013): standalone delegation harness for the Chief
Engineer / DeepSeek-workforce model. Spins up its own isolated OpenCode
server instance (separate port from the main Jarvis server's owned
instance, and from any other concurrently-running delegation role — e.g.
Builder and Reviewer must use different ports), submits one prompt, and
waits for a *verified* terminal signal (`session.idle`/`session.error` via
SSE — never inferred from elapsed silence, per ADR-010) before returning.

Explicitly sets project_dir on OpenCodeServerManager — the M9B.0 finding
(SESSION.md, Known Limitation #54) was that the spawn cwd, not
send_prompt()'s per-request `directory` param, governs where an agent's
file-write tools actually resolve paths; omitting it let D4 write outside
its intended directory.

Usage:
    python scripts/delegate_opencode_task.py \\
        --port 4201 --project-dir D:\\Projects\\Jarvis \\
        --prompt-file task.md --output-file result.txt

Not a pytest test. Requires a real opencode.exe and real OpenRouter
credit (DeepSeek V4 Flash is a paid model — see ADR-013's credit-threshold
guardrail, checked by the caller before invoking this script, not by the
script itself).
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from app.integrations.opencode_server import OpenCodeServerManager
from app.integrations.opencode_adapter import OpenCodeAdapter, ALLOWED_PAID_OPENCODE_MODEL_ID

DEFAULT_TIMEOUT_SECONDS = 600


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


async def run(port: int, project_dir: str, prompt: str, timeout: int) -> dict:
    server = OpenCodeServerManager(port=port, project_dir=project_dir)
    adapter = OpenCodeAdapter(base_url=server.base_url)

    log(f"Starting isolated OpenCode server on port {port} (project_dir={project_dir})")
    await server.start()
    try:
        session_id = await _create_session_with_retry(adapter, project_dir)
        log(f"Session created: {session_id}")

        await adapter.send_prompt(
            session_id, project_dir, prompt,
            provider_id="openrouter", model_id=ALLOWED_PAID_OPENCODE_MODEL_ID,
        )
        log(f"Prompt submitted (model={ALLOWED_PAID_OPENCODE_MODEL_ID})")

        outcome = await _wait_for_terminal_event(adapter, project_dir, session_id, timeout)
        messages = await adapter.get_messages(session_id, project_dir, limit=100)
        return {"session_id": session_id, "outcome": outcome, "messages": messages}
    finally:
        await server.stop()
        log("Server stopped")


async def _create_session_with_retry(adapter: OpenCodeAdapter, project_dir: str, attempts: int = 5):
    """A real bug found running this script for the first time (Milestone
    9B.2 pilot): OpenCodeServerManager.start()'s health check
    (`/global/health`) returning OK does not guarantee `POST /session` is
    immediately ready on a freshly-spawned isolated instance — the first
    real request hit a genuine httpx.ReadTimeout. Retrying with backoff
    here (in the delegation harness) rather than raising the adapter's
    per-request timeout, since this is specifically a cold-start gap for
    brand-new isolated instances, not a general adapter timeout problem."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await adapter.create_session(project_dir)
        except Exception as e:
            last_error = e
            log(f"create_session attempt {attempt}/{attempts} failed: {type(e).__name__}: {e}")
            if attempt < attempts:
                await asyncio.sleep(3 * attempt)
    raise last_error


async def _wait_for_terminal_event(adapter: OpenCodeAdapter, project_dir: str, session_id: str, timeout: int) -> str:
    """Blocks until a verified session.idle or session.error SSE event for
    this session_id, or returns 'timeout' — never infers completion from
    elapsed time alone (ADR-010)."""
    deadline = time.monotonic() + timeout
    async for frame in adapter.consume_events(project_dir):
        if time.monotonic() > deadline:
            return "timeout"
        data = frame.get("data", {})
        payload = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            continue
        props = payload.get("properties", {}) or {}
        info = props.get("info", {}) or {}
        event_session_id = info.get("sessionID") or props.get("sessionID")
        if event_session_id != session_id:
            continue
        event_type = payload.get("type")
        if event_type == "session.idle":
            log("session.idle observed")
            return "idle"
        if event_type == "session.error":
            log(f"session.error observed: {props.get('error')}")
            return "error"
    return "stream_closed"


def _format_transcript(result: dict) -> str:
    lines = [f"session_id={result['session_id']}", f"outcome={result['outcome']}", "---"]
    for m in result["messages"]:
        role = m.get("info", {}).get("role") if isinstance(m.get("info"), dict) else m.get("role")
        parts = m.get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
        lines.append(f"[{role}]\n{text}\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    with open(args.prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()

    result = asyncio.run(run(args.port, args.project_dir, prompt, args.timeout))

    transcript = _format_transcript(result)
    with open(args.output_file, "w", encoding="utf-8") as f:
        f.write(transcript)

    log(f"Outcome: {result['outcome']}. Transcript written to {args.output_file}")
    if result["outcome"] != "idle":
        sys.exit(1)


if __name__ == "__main__":
    main()
