import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workers.base import WorkerResultStatus, WorkerStatus
from app.workers.strategist_worker import StrategistWorker


class FakeProcess:
    def __init__(self, returncode=0, stdout=b"plan complete", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.communicate = AsyncMock(return_value=(stdout, stderr))
        self.terminate = MagicMock()
        self.kill = MagicMock()
        # wait is sync here because tests patch asyncio.wait_for; using AsyncMock
        # without awaiting would emit RuntimeWarnings about unawaited coroutines.
        self.wait = MagicMock(return_value=returncode)


@pytest.fixture
def worker():
    return StrategistWorker()


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STRATEGIST_RUNTIME_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_invoke_returns_completed_on_success(worker, isolated_runtime, monkeypatch):
    fake = FakeProcess(returncode=0, stdout=b"  strategic advice  ")
    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake),
    )

    result = await worker.invoke("advise me")

    assert result.status == WorkerResultStatus.COMPLETED
    assert result.output == "strategic advice"
    assert result.error is None


@pytest.mark.asyncio
async def test_invoke_returns_failed_on_nonzero_exit(worker, isolated_runtime, monkeypatch):
    fake = FakeProcess(returncode=1, stderr=b"something went wrong")
    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake),
    )

    result = await worker.invoke("advise me")

    assert result.status == WorkerResultStatus.FAILED
    assert "something went wrong" in result.error
    assert result.output is None


@pytest.mark.asyncio
async def test_invoke_truncates_long_stderr(worker, isolated_runtime, monkeypatch):
    long_stderr = b"x" * 5000
    fake = FakeProcess(returncode=2, stderr=long_stderr)
    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake),
    )

    result = await worker.invoke("advise me")

    assert result.status == WorkerResultStatus.FAILED
    assert len(result.error) < len(long_stderr.decode())
    assert "... (truncated)" in result.error


@pytest.mark.asyncio
async def test_invoke_returns_failed_on_timeout_and_kills_process(
    worker, isolated_runtime, monkeypatch
):
    fake = FakeProcess()
    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake),
    )

    async def raise_timeout(awaitable=None, timeout=None, **kwargs):
        # Avoid leaking an unawaited AsyncMock coroutine when simulating timeout.
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.wait_for",
        raise_timeout,
    )

    result = await worker.invoke("advise me")

    assert result.status == WorkerResultStatus.FAILED
    assert "timed out" in result.error
    assert fake.kill.called or fake.terminate.called


@pytest.mark.asyncio
async def test_invoke_returns_failed_when_cli_not_found(worker, isolated_runtime, monkeypatch):
    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError()),
    )

    result = await worker.invoke("advise me")

    assert result.status == WorkerResultStatus.FAILED
    assert "opencode CLI not found" in result.error


@pytest.mark.asyncio
async def test_invoke_uses_model_override(isolated_runtime, monkeypatch):
    custom = StrategistWorker(model="opencode-go/kimi-k3")
    fake = FakeProcess(returncode=0, stdout=b"ok")
    mock_create = AsyncMock(return_value=fake)
    monkeypatch.setattr(
        "app.workers.strategist_worker.asyncio.create_subprocess_exec",
        mock_create,
    )

    await custom.invoke("advise me")

    args, _kwargs = mock_create.call_args
    assert "opencode-go/kimi-k3" in args
    assert "run" in args
    assert "-m" in args


class TestStrategistWorkerStatus:
    @pytest.mark.asyncio
    async def test_status_available_when_exe_exists(self, monkeypatch):
        monkeypatch.setattr(
            "app.workers.strategist_worker.os.path.exists",
            lambda _p: True,
        )
        monkeypatch.setattr(
            "app.workers.strategist_worker.shutil.which",
            lambda _p: None,
        )
        status = await StrategistWorker().status()
        assert status == WorkerStatus.AVAILABLE

    @pytest.mark.asyncio
    async def test_status_available_when_exe_on_path(self, monkeypatch):
        monkeypatch.setattr(
            "app.workers.strategist_worker.os.path.exists",
            lambda _p: False,
        )
        monkeypatch.setattr(
            "app.workers.strategist_worker.shutil.which",
            lambda _p: "/some/opencode",
        )
        status = await StrategistWorker().status()
        assert status == WorkerStatus.AVAILABLE

    @pytest.mark.asyncio
    async def test_status_unavailable_when_exe_missing(self, monkeypatch):
        monkeypatch.setattr(
            "app.workers.strategist_worker.os.path.exists",
            lambda _p: False,
        )
        monkeypatch.setattr(
            "app.workers.strategist_worker.shutil.which",
            lambda _p: None,
        )
        status = await StrategistWorker().status()
        assert status == WorkerStatus.UNAVAILABLE
