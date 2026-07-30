import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workers.base import (
    Worker,
    WorkerRegistry,
    WorkerResult,
    WorkerResultStatus,
    WorkerStatus,
)


class FakeWorker(Worker):
    def __init__(self, name, capabilities, worker_status=WorkerStatus.AVAILABLE):
        self.name = name
        self.capabilities = capabilities
        self._status = worker_status
        self.invoke_calls = []

    async def invoke(self, task_description: str, **kwargs):
        self.invoke_calls.append((task_description, kwargs))
        return WorkerResult(status=WorkerResultStatus.COMPLETED, output="done")

    async def status(self):
        return self._status


class TestWorkerRegistry:
    def test_register_worker(self):
        registry = WorkerRegistry()
        w = FakeWorker("test-worker", ["coding"])
        registry.register(w)
        assert registry.get_by_name("test-worker") is w

    def test_duplicate_name_raises(self):
        registry = WorkerRegistry()
        registry.register(FakeWorker("dup", ["coding"]))
        with pytest.raises(ValueError, match="dup"):
            registry.register(FakeWorker("dup", ["shell"]))

    def test_get_by_name_hit(self):
        registry = WorkerRegistry()
        w = FakeWorker("alpha", ["coding"])
        registry.register(w)
        assert registry.get_by_name("alpha") is w

    def test_get_by_name_miss(self):
        registry = WorkerRegistry()
        assert registry.get_by_name("nonexistent") is None

    def test_get_by_capability_matches_multiple(self):
        registry = WorkerRegistry()
        w1 = FakeWorker("alpha", ["coding", "review"])
        w2 = FakeWorker("beta", ["shell", "review"])
        w3 = FakeWorker("gamma", ["coding"])
        registry.register(w1)
        registry.register(w2)
        registry.register(w3)

        coders = registry.get_by_capability("coding")
        assert len(coders) == 2
        assert w1 in coders
        assert w3 in coders

        reviewers = registry.get_by_capability("review")
        assert len(reviewers) == 2
        assert w1 in reviewers
        assert w2 in reviewers

    def test_get_by_capability_no_match(self):
        registry = WorkerRegistry()
        registry.register(FakeWorker("alpha", ["coding"]))
        assert registry.get_by_capability("planning") == []

    def test_list_workers(self):
        registry = WorkerRegistry()
        w1 = FakeWorker("alpha", ["coding"])
        w2 = FakeWorker("beta", ["shell"])
        registry.register(w1)
        registry.register(w2)
        workers = registry.list_workers()
        assert len(workers) == 2
        assert w1 in workers
        assert w2 in workers

    def test_list_workers_empty(self):
        registry = WorkerRegistry()
        assert registry.list_workers() == []
