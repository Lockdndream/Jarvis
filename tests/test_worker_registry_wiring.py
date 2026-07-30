import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database as db
from app.supervisor.tools import ToolRegistry
from app.supervisor.supervisor import Supervisor
from app.workers.base import WorkerRegistry, WorkerResult, WorkerResultStatus, WorkerStatus, Worker


@pytest.fixture(autouse=True)
def test_db():
    # consult_strategist calls the real build_context(), which reads
    # actual tables via app.database -- needs real schema, unlike this
    # file's other tests which mock everything else.
    old_path = db.DB_PATH
    f, path = tempfile.mkstemp(suffix=".db")
    os.close(f)
    db.DB_PATH = path
    db.init_db()
    yield
    db.DB_PATH = old_path
    if os.path.exists(path):
        os.unlink(path)


class FakeStrategistWorker(Worker):
    name = "strategist"
    capabilities = ["planning"]

    def __init__(self, status=WorkerResultStatus.COMPLETED, output="strategist says hello", error=None):
        self._status = status
        self._output = output
        self._error = error
        self.invoke_calls = []

    async def invoke(self, task_description: str, **kwargs):
        self.invoke_calls.append((task_description, kwargs))
        return WorkerResult(status=self._status, output=self._output, error=self._error)

    async def status(self):
        return WorkerStatus.AVAILABLE


class FakeOtherWorker(Worker):
    name = "opencode"
    capabilities = ["coding"]

    async def invoke(self, task_description: str, **kwargs):
        return WorkerResult(status=WorkerResultStatus.COMPLETED, output="done")

    async def status(self):
        return WorkerStatus.AVAILABLE


@pytest.fixture
def mock_oc():
    oc = MagicMock()
    oc.start_session = AsyncMock()
    oc.stop_session = AsyncMock()
    oc.get_status = AsyncMock()
    oc.get_status.return_value = {"server_alive": True, "running_tasks": []}
    oc.send_instruction = AsyncMock()
    oc.cancel_session = AsyncMock()
    oc.answer_question = AsyncMock()
    oc.approve_permission = AsyncMock()
    oc.fetch_task_result_text = AsyncMock()
    return oc


@pytest.fixture
def mock_tm_deps():
    tm = MagicMock()
    tm.get_running_tasks_info = MagicMock(return_value=[])
    tm.get_pending_questions_info = MagicMock(return_value=[])
    tm.answer_question = AsyncMock()
    tm.cancel = AsyncMock()
    return tm


@pytest.fixture(autouse=True)
def mock_resolve_project():
    with patch("app.supervisor.tools.resolve_project") as mock:
        mock.return_value = ("jarvis", "/tmp/test-project")
        yield mock


class TestToolRegistryStartOpencodeTaskWithoutRegistry:
    @pytest.mark.asyncio
    async def test_start_opencode_task_succeeds_without_worker_registry(self, mock_oc, mock_tm_deps):
        mock_oc.start_session.return_value = {
            "task_id": "oc_test123",
            "session_id": "ses_abc_session_id_1234567890",
            "name": "OpenCode: test task",
        }
        registry = ToolRegistry(mock_tm_deps, mock_oc, None)

        result = await registry.call("start_opencode_task", {
            "project_alias": "jarvis",
            "instruction": "run tests",
        })

        assert "Task started:" in result
        assert "oc_test123" in result
        mock_oc.start_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_opencode_task_fails_when_no_opencode_supervisor(self, mock_tm_deps):
        registry = ToolRegistry(mock_tm_deps, None, None)

        result = await registry.call("start_opencode_task", {
            "project_alias": "jarvis",
            "instruction": "run tests",
        })

        assert result == "Error: OpenCode supervisor not available"


class TestConsultStrategist:
    @pytest.mark.asyncio
    async def test_consult_strategist_returns_output_on_completed(self, mock_oc, mock_tm_deps):
        worker = FakeStrategistWorker(status=WorkerResultStatus.COMPLETED, output="plan: do X then Y")
        wr = WorkerRegistry()
        wr.register(worker)
        registry = ToolRegistry(mock_tm_deps, mock_oc, None, wr)

        result = await registry.call("consult_strategist", {
            "question": "how should I refactor this?",
        })

        assert result == "plan: do X then Y"
        assert len(worker.invoke_calls) == 1
        prompt = worker.invoke_calls[0][0]
        assert "how should I refactor this?" in prompt

    @pytest.mark.asyncio
    async def test_consult_strategist_returns_error_on_failed(self, mock_oc, mock_tm_deps):
        worker = FakeStrategistWorker(
            status=WorkerResultStatus.FAILED,
            error="strategist process crashed",
        )
        wr = WorkerRegistry()
        wr.register(worker)
        registry = ToolRegistry(mock_tm_deps, mock_oc, None, wr)

        result = await registry.call("consult_strategist", {
            "question": "how should I refactor this?",
        })

        assert result == "Error consulting strategist: strategist process crashed"

    @pytest.mark.asyncio
    async def test_consult_strategist_no_worker_registry_at_all(self, mock_oc, mock_tm_deps):
        registry = ToolRegistry(mock_tm_deps, mock_oc, None)

        result = await registry.call("consult_strategist", {
            "question": "how should I refactor this?",
        })

        assert result == "Error: strategist not available"

    @pytest.mark.asyncio
    async def test_consult_strategist_registry_has_no_strategist(self, mock_oc, mock_tm_deps):
        wr = WorkerRegistry()
        wr.register(FakeOtherWorker())
        registry = ToolRegistry(mock_tm_deps, mock_oc, None, wr)

        result = await registry.call("consult_strategist", {
            "question": "how should I refactor this?",
        })

        assert result == "Error: strategist not available"


class TestSupervisorConstructionWithoutRegistry:
    def test_supervisor_constructed_without_worker_registry(self, mock_oc, mock_tm_deps):
        sup = Supervisor(mock_tm_deps, mock_oc, None)
        assert sup.tools is not None
        assert isinstance(sup.tools, ToolRegistry)
