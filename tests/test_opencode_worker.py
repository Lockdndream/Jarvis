import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workers.opencode_worker import OpenCodeWorker
from app.workers.base import WorkerResultStatus, WorkerStatus


@pytest.fixture
def mock_oc():
    oc = MagicMock()
    oc.start_session = AsyncMock()
    oc.get_status = AsyncMock()
    return oc


@pytest.fixture
def worker(mock_oc):
    return OpenCodeWorker(mock_oc)


class TestOpenCodeWorkerInvoke:
    @pytest.mark.asyncio
    async def test_invoke_returns_dispatched_with_task_id(self, worker, mock_oc):
        mock_oc.start_session.return_value = {
            "task_id": "oc_abc123def456",
            "session_id": "ses_xyz",
            "name": "OpenCode: build a thing",
        }

        result = await worker.invoke("build a thing", project_dir="/tmp/test")

        mock_oc.start_session.assert_called_once_with(
            "/tmp/test",
            "build a thing",
            provider_id=None,
            model_id=None,
        )
        assert result.status == WorkerResultStatus.DISPATCHED
        assert result.task_id == "oc_abc123def456"
        assert result.metadata == {
            "session_id": "ses_xyz",
            "name": "OpenCode: build a thing",
        }
        assert result.output is None
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_passes_provider_and_model(self, worker, mock_oc):
        mock_oc.start_session.return_value = {
            "task_id": "oc_test",
            "session_id": "ses_test",
            "name": "OpenCode: test",
        }

        await worker.invoke(
            "test task",
            project_dir="/tmp/test",
            provider_id="test-provider",
            model_id="test-model",
        )

        mock_oc.start_session.assert_called_once_with(
            "/tmp/test",
            "test task",
            provider_id="test-provider",
            model_id="test-model",
        )

    @pytest.mark.asyncio
    async def test_invoke_defaults_project_dir_when_none(self, worker, mock_oc):
        mock_oc.start_session.return_value = {
            "task_id": "oc_default",
            "session_id": "ses_default",
            "name": "OpenCode: test",
        }

        result = await worker.invoke("test task")

        mock_oc.start_session.assert_called_once_with(
            ".",
            "test task",
            provider_id=None,
            model_id=None,
        )
        assert result.status == WorkerResultStatus.DISPATCHED

    @pytest.mark.asyncio
    async def test_invoke_returns_failed_on_exception(self, worker, mock_oc):
        mock_oc.start_session.side_effect = RuntimeError("connection lost")

        result = await worker.invoke("do something")

        assert result.status == WorkerResultStatus.FAILED
        assert result.error == "connection lost"
        assert result.task_id is None


class TestOpenCodeWorkerStatus:
    @pytest.mark.asyncio
    async def test_status_available_when_server_up_no_tasks(self, worker, mock_oc):
        mock_oc.get_status.return_value = {
            "server_alive": True,
            "server_url": "http://localhost:8080",
            "running_tasks": [],
            "pending_questions": [],
        }

        status = await worker.status()
        assert status == WorkerStatus.AVAILABLE

    @pytest.mark.asyncio
    async def test_status_busy_when_server_up_with_running_tasks(self, worker, mock_oc):
        mock_oc.get_status.return_value = {
            "server_alive": True,
            "server_url": "http://localhost:8080",
            "running_tasks": [{"task_id": "oc_abc"}],
            "pending_questions": [],
        }

        status = await worker.status()
        assert status == WorkerStatus.BUSY

    @pytest.mark.asyncio
    async def test_status_unavailable_when_server_down(self, worker, mock_oc):
        mock_oc.get_status.return_value = {
            "server_alive": False,
            "server_url": "http://localhost:8080",
            "running_tasks": [],
            "pending_questions": [],
        }

        status = await worker.status()
        assert status == WorkerStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_status_unavailable_when_get_status_raises(self, worker, mock_oc):
        mock_oc.get_status.side_effect = RuntimeError("timeout")

        status = await worker.status()
        assert status == WorkerStatus.UNAVAILABLE
