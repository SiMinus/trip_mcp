"""真实 LLM 接口的小样本重试测试。

默认跳过，只有显式开启才会运行：
    RUN_LLM_LIVE_RETRY_TEST=1 pytest -s -q tests/test_retry_manager_live.py

建议加 `-s` 观察 retry_manager 打印的日志与本文件的状态输出。
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from agent.config import settings
from agent.retry_manager import ErrorCategory, SmartRetryManager


def _live_enabled() -> bool:
    return os.getenv("RUN_LLM_LIVE_RETRY_TEST") == "1"


class StatusCodeError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _build_live_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0,
    )


@pytest.mark.skipif(not _live_enabled(), reason="Set RUN_LLM_LIVE_RETRY_TEST=1 to run live retry tests.")
def test_live_llm_smoke_through_retry_manager():
    if not settings.openai_api_key:
        pytest.skip("OPENAI-compatible API key is not configured.")

    manager = SmartRetryManager()
    llm = _build_live_llm()

    result = asyncio.run(
        manager.execute_with_retry(
            "openai_live_smoke",
            llm.ainvoke,
            [HumanMessage(content="请只回复 OK")],
        )
    )

    assert result["success"] is True
    print("live_smoke_content:", getattr(result["data"], "content", ""))


@pytest.mark.skipif(not _live_enabled(), reason="Set RUN_LLM_LIVE_RETRY_TEST=1 to run live retry tests.")
def test_live_llm_retry_then_recover():
    if not settings.openai_api_key:
        pytest.skip("OPENAI-compatible API key is not configured.")

    manager = SmartRetryManager()
    policy = manager.retry_policies[ErrorCategory.TIMEOUT_ERROR]
    policy.base_delay = 0.01
    policy.max_delay = 0.05
    policy.max_retries = 2
    llm = _build_live_llm()
    attempts = {"count": 0}

    async def flaky_live_call(messages):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("synthetic read_timeout before live LLM call")
        return await llm.ainvoke(messages)

    result = asyncio.run(
        manager.execute_with_retry(
            "openai_live_retry",
            flaky_live_call,
            [HumanMessage(content="请只回复 RETRY_OK")],
        )
    )

    assert result["success"] is True
    assert attempts["count"] == 3
    print("retry_recovered_attempts:", attempts["count"])
    print("retry_recovered_content:", getattr(result["data"], "content", ""))


@pytest.mark.skipif(not _live_enabled(), reason="Set RUN_LLM_LIVE_RETRY_TEST=1 to run live retry tests.")
def test_live_llm_circuit_breaker_and_recovery():
    if not settings.openai_api_key:
        pytest.skip("OPENAI-compatible API key is not configured.")

    manager = SmartRetryManager()
    llm = _build_live_llm()
    breaker = manager.get_circuit_breaker("openai_live_breaker")
    breaker.failure_threshold = 2
    breaker.recovery_timeout = 1
    breaker.success_threshold = 1

    async def auth_fail():
        raise StatusCodeError("401 unauthorized", 401)

    first = asyncio.run(manager.execute_with_retry("openai_live_breaker", auth_fail))
    second = asyncio.run(manager.execute_with_retry("openai_live_breaker", auth_fail))
    blocked = asyncio.run(manager.execute_with_retry("openai_live_breaker", auth_fail))

    assert first["success"] is False
    assert second["success"] is False
    assert blocked["success"] is False
    assert blocked["error"] == "Circuit breaker open"
    assert breaker.get_state() == "OPEN"
    print("breaker_state_after_failures:", breaker.get_state())

    breaker.last_failure_time = time.time() - 120

    recovered = asyncio.run(
        manager.execute_with_retry(
            "openai_live_breaker",
            llm.ainvoke,
            [HumanMessage(content="请只回复 BREAKER_RECOVERED")],
        )
    )

    assert recovered["success"] is True
    assert breaker.get_state() == "CLOSED"
    print("breaker_state_after_recovery:", breaker.get_state())
    print("breaker_recovery_content:", getattr(recovered["data"], "content", ""))
