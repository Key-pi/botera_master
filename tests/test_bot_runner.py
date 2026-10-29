import asyncio

import pytest

from apps.audit.models import PlatformDiagnostic
from apps.bot import runner


def test_bot_runner_heartbeat_loop_records_current_component(monkeypatch):
    calls = []

    def record_heartbeat(**kwargs):
        calls.append(kwargs)

    async def stop_after_first_heartbeat(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "record_worker_heartbeat", record_heartbeat)
    monkeypatch.setattr(runner.asyncio, "sleep", stop_after_first_heartbeat)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner._record_bot_runner_heartbeat_loop())

    assert calls == [{"component": PlatformDiagnostic.Component.BOT_RUNNER}]
