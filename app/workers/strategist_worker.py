import asyncio
import logging
import os
import shutil

from app import config
from app.integrations.opencode_server import (
    ensure_isolated_runtime_provisioned,
    isolated_env_overrides,
)
from app.workers.base import Worker, WorkerResult, WorkerResultStatus, WorkerStatus

logger = logging.getLogger(__name__)

_ERROR_TRUNCATION = 2000


class StrategistWorker(Worker):
    name = "strategist"
    capabilities = ["planning", "review", "consultation"]

    def __init__(self, model: str | None = None, timeout_seconds: int | None = None):
        self._model = model or config.strategist_model()
        self._timeout = timeout_seconds or config.strategist_timeout_seconds()

    async def invoke(self, task_description: str, **kwargs) -> WorkerResult:
        runtime_dir = config.strategist_runtime_dir()
        ensure_isolated_runtime_provisioned(runtime_dir)

        env = config.os_essential_subprocess_env()
        env.update(isolated_env_overrides(runtime_dir))

        exe = config.opencode_exe_default()
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                "run",
                "-m",
                self._model,
                task_description,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            return WorkerResult(
                status=WorkerResultStatus.FAILED,
                error=f"opencode CLI not found: {exe}",
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    pass
            return WorkerResult(
                status=WorkerResultStatus.FAILED,
                error=f"strategist consultation timed out after {self._timeout}s",
            )

        if proc.returncode != 0:
            err_text = stderr.decode(errors="replace").strip()
            if len(err_text) > _ERROR_TRUNCATION:
                err_text = err_text[:_ERROR_TRUNCATION] + "\n... (truncated)"
            return WorkerResult(
                status=WorkerResultStatus.FAILED,
                error=err_text,
            )

        return WorkerResult(
            status=WorkerResultStatus.COMPLETED,
            output=stdout.decode(errors="replace").strip(),
        )

    async def status(self) -> WorkerStatus:
        exe = config.opencode_exe_default()
        if os.path.exists(exe) or shutil.which(exe) is not None:
            return WorkerStatus.AVAILABLE
        return WorkerStatus.UNAVAILABLE
