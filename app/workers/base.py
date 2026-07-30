from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class WorkerResultStatus(str, Enum):
    COMPLETED = "completed"
    DISPATCHED = "dispatched"
    FAILED = "failed"


class WorkerStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


@dataclass
class WorkerResult:
    status: WorkerResultStatus
    output: str | None = None
    task_id: str | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)


class Worker(ABC):
    name: str
    capabilities: list[str]

    @abstractmethod
    async def invoke(self, task_description: str, **kwargs) -> WorkerResult:
        ...

    @abstractmethod
    async def status(self) -> WorkerStatus:
        ...


class WorkerRegistry:
    def __init__(self):
        self._workers: dict[str, Worker] = {}

    def register(self, worker: Worker) -> None:
        if worker.name in self._workers:
            raise ValueError(f"Worker with name '{worker.name}' is already registered")
        self._workers[worker.name] = worker

    def get_by_name(self, name: str) -> Worker | None:
        return self._workers.get(name)

    def get_by_capability(self, capability: str) -> list[Worker]:
        return [w for w in self._workers.values() if capability in w.capabilities]

    def list_workers(self) -> list[Worker]:
        return list(self._workers.values())
