"""Persistent AttentionRequest scheduler (Milestone 8 Phase 8).

The source of truth for "is anything due" is the database
(attention_requests.deferred_until / next_contact_at), never a browser
timer — a deferred AttentionRequest must survive browser closure,
WebSocket disconnect, and a full Jarvis restart (Phase 19). This is an
asyncio background task integrated with FastAPI's lifespan, the same
pattern already used for OpenCodeSupervisor's SSE/poll loops.
"""
import asyncio
import logging

import app.database as db
from app import attention_manager

logger = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 15


class AttentionScheduler:
    def __init__(self, conn_manager, tick_seconds: int = DEFAULT_TICK_SECONDS, clock=None):
        self.cm = conn_manager
        self.tick_seconds = tick_seconds
        # Optional callable -> ISO-8601 UTC "now" string, for deterministic
        # tests (Phase 8: "do not make unit tests wait in real time" —
        # tests call reconcile_on_startup()/run_due_pass() directly with a
        # fake clock instead of sleeping through tick_seconds).
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._stopped = False
        # Guards a due item from being processed twice by overlapping
        # passes (Phase 8.6/21) — belt-and-suspenders alongside the
        # database-level guarded transition in mark_due().
        self._claiming: set[str] = set()

    def _now(self) -> str | None:
        return self._clock() if self._clock else None  # None -> database layer uses real utcnow()

    async def start(self) -> None:
        self._stopped = False
        await self.reconcile_on_startup()
        self._task = asyncio.create_task(self._loop())
        logger.info("attention scheduler started (tick=%ss)", self.tick_seconds)

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("attention scheduler stopped")

    async def reconcile_on_startup(self) -> None:
        """Overdue items must be detected even if Jarvis was offline when
        they became due, not just ones that become due while running
        (Phase 19 restart-recovery requirement)."""
        due = db.get_due_attention_requests(now=self._now())
        if due:
            logger.info("scheduler recovery: %d overdue attention request(s) found at startup", len(due))
        await self.run_due_pass(due)

    async def run_due_pass(self, due_rows: list[dict] | None = None) -> int:
        """Processes one batch of due AttentionRequests. Exposed
        separately from the sleep loop so tests can call it directly with
        deterministic rows/clock, without waiting real time. Returns the
        count actually re-contacted."""
        if due_rows is None:
            due_rows = db.get_due_attention_requests(now=self._now())
        processed = 0
        for row in due_rows:
            attention_request_id = row["attention_request_id"]
            if attention_request_id in self._claiming:
                continue
            self._claiming.add(attention_request_id)
            try:
                if await self._recontact_one(row):
                    processed += 1
            finally:
                self._claiming.discard(attention_request_id)
        return processed

    async def _recontact_one(self, row: dict) -> bool:
        logger.info("attention due: id=%s status=%s", row["attention_request_id"], row["status"])
        # mark_due() is a guarded conditional UPDATE (Phase 21) — if
        # something else (a user resolving/cancelling it concurrently)
        # already moved this request off PENDING-eligible statuses, this
        # returns False and we skip it rather than fighting the race.
        if not await attention_manager.mark_due(row["attention_request_id"]):
            return False
        fresh = db.get_attention_request(row["attention_request_id"])
        if not fresh or fresh["status"] != attention_manager.STATUS_PENDING:
            return False
        await attention_manager.initiate_contact(self.cm, fresh)
        return True

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(self.tick_seconds)
                if self._stopped:
                    break
                count = await self.run_due_pass()
                if count:
                    logger.info("scheduler tick: re-contacted %d due attention request(s)", count)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("attention scheduler tick error (loop continues): %s", e, exc_info=True)
