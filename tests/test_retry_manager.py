"""SmartRetryManager 行为测试。

不依赖 pytest-asyncio，直接使用 asyncio.run 执行异步用例。
"""

from __future__ import annotations

import asyncio
import time

from agent.retry_manager import SmartRetryManager


class StatusCodeError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def test_rate_limit_retry_then_success(monkeypatch):
    manager = SmartRetryManager()
    attempts = {"count": 0}
    delays: list[float] = []

    async def fake_sleep(delay: float):
        delays.append(delay)

    async def flaky_call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise Exception("429 rate limit exceeded")
        return {"answer": "ok"}

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        manager.execute_with_retry("openai", flaky_call)
    )

    assert result["success"] is True
    assert result["data"] == {"answer": "ok"}
    assert attempts["count"] == 3
    assert delays == [5.0, 15.0]


def test_timeout_error_retries_to_exhaustion(monkeypatch):
    manager = SmartRetryManager()
    attempts = {"count": 0}
    delays: list[float] = []

    async def fake_sleep(delay: float):
        delays.append(delay)

    async def timeout_call():
        attempts["count"] += 1
        raise TimeoutError("read_timeout while calling llm")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        manager.execute_with_retry("openai_timeout", timeout_call)
    )

    assert result["success"] is False
    assert isinstance(result["error"], TimeoutError)
    assert attempts["count"] == 4
    assert delays == [2.0, 4.0, 8.0]


def test_auth_error_does_not_retry(monkeypatch):
    manager = SmartRetryManager()
    attempts = {"count": 0}
    delays: list[float] = []

    async def fake_sleep(delay: float):
        delays.append(delay)

    async def auth_fail():
        attempts["count"] += 1
        raise StatusCodeError("401 unauthorized", 401)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        manager.execute_with_retry("openai_auth", auth_fail)
    )

    assert result["success"] is False
    assert isinstance(result["error"], StatusCodeError)
    assert attempts["count"] == 1
    assert delays == []


def test_circuit_breaker_opens_after_repeated_failures():
    manager = SmartRetryManager()
    attempts = {"count": 0}

    async def auth_fail():
        attempts["count"] += 1
        raise StatusCodeError("401 unauthorized", 401)

    for _ in range(5):
        result = asyncio.run(manager.execute_with_retry("openai_breaker", auth_fail))
        assert result["success"] is False

    breaker = manager.get_circuit_breaker("openai_breaker")
    assert breaker.get_state() == "OPEN"

    blocked = asyncio.run(manager.execute_with_retry("openai_breaker", auth_fail))
    assert blocked["success"] is False
    assert blocked["error"] == "Circuit breaker open"
    assert attempts["count"] == 5


def test_circuit_breaker_half_open_recovery_closes_after_successes():
    manager = SmartRetryManager()
    breaker = manager.get_circuit_breaker("openai_recover")
    breaker.state = "OPEN"
    breaker.last_failure_time = time.time() - 120
    breaker.recovery_timeout = 1

    attempts = {"count": 0}

    async def success_call():
        attempts["count"] += 1
        return {"answer": "recovered"}

    first = asyncio.run(manager.execute_with_retry("openai_recover", success_call))
    second = asyncio.run(manager.execute_with_retry("openai_recover", success_call))
    third = asyncio.run(manager.execute_with_retry("openai_recover", success_call))

    assert first["success"] is True
    assert second["success"] is True
    assert third["success"] is True
    assert attempts["count"] == 3
    assert breaker.get_state() == "CLOSED"
