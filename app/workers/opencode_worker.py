import logging
from app.workers.base import Worker, WorkerResult, WorkerResultStatus, WorkerStatus

logger = logging.getLogger(__name__)


class OpenCodeWorker(Worker):
    name = "opencode"
    capabilities = ["coding", "file_editing", "shell_execution"]

    def __init__(self, opencode_supervisor):
        self._oc = opencode_supervisor

    async def invoke(self, task_description: str, **kwargs) -> WorkerResult:
        project_dir = kwargs.get("project_dir")
        provider_id = kwargs.get("provider_id")
        model_id = kwargs.get("model_id")
        try:
            result = await self._oc.start_session(
                project_dir or ".",
                task_description,
                provider_id=provider_id,
                model_id=model_id,
            )
            return WorkerResult(
                status=WorkerResultStatus.DISPATCHED,
                task_id=result["task_id"],
                metadata={
                    "session_id": result.get("session_id"),
                    "name": result.get("name"),
                },
            )
        except Exception as e:
            logger.error("OpenCodeWorker.invoke failed: %s", e)
            return WorkerResult(status=WorkerResultStatus.FAILED, error=str(e))

    async def status(self) -> WorkerStatus:
        try:
            status_dict = await self._oc.get_status()
        except Exception:
            return WorkerStatus.UNAVAILABLE

        server_alive = status_dict.get("server_alive", False)
        running_tasks = status_dict.get("running_tasks", [])

        if not server_alive:
            return WorkerStatus.UNAVAILABLE
        if running_tasks:
            return WorkerStatus.BUSY
        return WorkerStatus.AVAILABLE
