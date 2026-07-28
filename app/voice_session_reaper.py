"""VoiceSession idle-timeout reaper (Milestone 9B.10).

Backstops every orphan path not already covered by main.py's WebSocket
disconnect cleanup (the `finally` block) or the Android client's own
onStop()-triggered close (ADR-017/9B.9): the OS killing VoiceActivity
before its onStop() can run, a Supervisor tool call (app/supervisor/
tools.py's open_voice_session) that opens a session with no matching
close, and any client that abandons a session while its connection stays
alive. Same asyncio background-task-integrated-with-FastAPI-lifespan
pattern as AttentionScheduler (app/attention_scheduler.py).
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 60
# Milestone 9B.10: every legal VoiceSession transition — including each
# conversational turn in VoiceSessionManager.handle_transcript() —
# refreshes updated_at, so a genuinely active conversation never accrues
# idle time regardless of its total duration. 15 minutes is generous
# enough that no plausible "user is thinking" pause is misclassified as
# abandonment, while still bounding the worst case far below the
# 49-minute orphan actually observed during Milestone 9B.9 real-device
# validation.
DEFAULT_MAX_IDLE_SECONDS = 15 * 60


class VoiceSessionReaper:
    def __init__(
        self,
        voice_session_manager,
        conn_manager,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
        max_idle_seconds: int = DEFAULT_MAX_IDLE_SECONDS,
        clock=None,
    ):
        self._vsm = voice_session_manager
        self.cm = conn_manager
        self.tick_seconds = tick_seconds
        self.max_idle_seconds = max_idle_seconds
        # Optional callable -> ISO-8601 UTC "now" string, for deterministic
        # tests (same clock-injection pattern as AttentionScheduler — never
        # make a test wait tick_seconds/max_idle_seconds of real time).
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._stopped = False

    def _now(self) -> str | None:
        return self._clock() if self._clock else None  # None -> database layer uses real utcnow()

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "voice session reaper started (tick=%ss, max_idle=%ss)",
            self.tick_seconds, self.max_idle_seconds,
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("voice session reaper stopped")

    async def run_reap_pass(self) -> list[dict]:
        """One reap pass, exposed separately from the sleep loop so tests
        can call it directly with a deterministic clock. Broadcasts a
        voice_session_closed frame for every reaped session so any
        still-connected client (which may not be the one whose
        disconnect/inactivity caused the reap) learns its session ended,
        the same way it would learn of any other server-initiated close —
        applyClosed() on the Android side already ignores a
        voice_session_id that doesn't match its own current session, so a
        broadcast to every connection is safe."""
        reaped = await asyncio.to_thread(self._vsm.reap_idle_sessions, self.max_idle_seconds, now=self._now())
        for session in reaped:
            await self.cm.broadcast({
                "type": "voice_session_closed",
                "voice_session_id": session["voice_session_id"],
                "reason": "idle_timeout",
            })
        return reaped

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(self.tick_seconds)
                if self._stopped:
                    break
                reaped = await self.run_reap_pass()
                if reaped:
                    logger.info("voice session reaper tick: reaped %d idle session(s)", len(reaped))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("voice session reaper tick error (loop continues): %s", e, exc_info=True)
