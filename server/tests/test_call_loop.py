import asyncio

from app.call_loop import run_call_loop
from app.call_manager import CallManager


class FakeWS:
    def __init__(self, msgs):
        self._msgs = list(msgs)

    async def receive(self):
        await asyncio.sleep(0)
        if self._msgs:
            return self._msgs.pop(0)
        raise ConnectionError("closed")


class Handler:
    def __init__(self, fail=False):
        self.started = False
        self.msgs = []
        self.fail = fail

    async def start(self):
        self.started = True
        if self.fail:
            raise RuntimeError("boom")

    async def on_message(self, msg):
        self.msgs.append(msg)


async def test_run_call_loop_calls_start_and_forwards_messages():
    cm = CallManager()
    await cm.acquire("c", "test")
    h = Handler()
    try:
        await run_call_loop(cm, "c", FakeWS(["a", "b"]), h)
    except ConnectionError:
        pass
    assert h.started
    assert h.msgs == ["a", "b"]


async def test_run_call_loop_ends_when_start_fails():
    cm = CallManager()
    await cm.acquire("c", "test")
    h = Handler(fail=True)

    class SlowWS:
        async def receive(self):
            await asyncio.sleep(0.01)
            return "x"

    await asyncio.wait_for(run_call_loop(cm, "c", SlowWS(), h), 2)
    assert h.started
