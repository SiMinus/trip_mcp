"""11 轮对话记忆集成测试。

默认不自动执行，避免无意中消耗真实模型额度。

运行方式：
    RUN_LLM_INTEGRATION=1 pytest -q tests/test_memory_51_rounds.py

或直接：
    RUN_LLM_INTEGRATION=1 python tests/test_memory_51_rounds.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
import uuid
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.config import settings
from agent.graph import create_agent_client, invoke_agent

ROUND_COUNT = 11
INITIAL_TRAVEL_STATE = {
    "destination": "杭州",
    "days": 3,
    "budget": "3000-5000",
    "travel_group": "情侣",
    "interests": ["美食", "历史人文"],
}


def _integration_enabled() -> bool:
    return os.getenv("RUN_LLM_INTEGRATION") == "1"


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if match:
            cleaned = match.group(0)
    return json.loads(cleaned)


def _normalize_travel_state(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "destination": str(value["destination"]),
        "days": int(value["days"]),
        "budget": str(value["budget"]),
        "travel_group": str(value["travel_group"]),
        "interests": [str(item) for item in value["interests"]],
    }


async def _run_turn(
    agent: Any,
    thread_id: str,
    user_message: str,
    travel_state: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    async for event in invoke_agent(agent, user_message, thread_id, travel_state):
        if event.get("type") == "token":
            parts.append(event["content"])
    return "".join(parts).strip()


async def _run_memory_scenario() -> tuple[dict[str, Any], dict[str, str], dict[str, Any], str]:
    _client, agent = await create_agent_client()
    thread_id = f"memory-11-rounds-{uuid.uuid4()}"
    round_outputs: dict[str, str] = {}

    first_turn = (
        "请记住我已经确认的旅行需求，后面我不再重复。"
        "这一轮只回复“已记住”，不要调用任何工具。"
    )
    await _run_turn(agent, thread_id, first_turn, INITIAL_TRAVEL_STATE)

    for round_no in range(2, ROUND_COUNT):
        if round_no == 5:
            budget_prompt = (
                "这是第 5 轮。不要调用任何工具。"
                "请直接回答：我目前确认的预算范围是多少？只用一句话回答。"
            )
            round_outputs["round_5_budget"] = await _run_turn(agent, thread_id, budget_prompt)
            continue

        if round_no == 8:
            group_prompt = (
                "这是第 8 轮。不要调用任何工具。"
                "请直接回答：我这次是和谁一起去？只用一句话回答。"
            )
            round_outputs["round_8_group"] = await _run_turn(agent, thread_id, group_prompt)
            continue

        filler_prompt = (
            f"这是第 {round_no} 轮。"
            f"请只回复“收到第{round_no}轮”，不要调用任何工具，也不要改写我之前确认的旅行需求。"
        )
        await _run_turn(agent, thread_id, filler_prompt)

    final_prompt = (
        "这是第 11 轮。不要调用任何工具。"
        "请只根据你当前记住的已确认旅行信息，严格输出 JSON，"
        '字段只能有 destination、days、budget、travel_group、interests，'
        "不要添加任何解释。"
    )
    final_text = await _run_turn(agent, thread_id, final_prompt)
    final_json = _extract_json_object(final_text)

    snapshot = await agent.aget_state({"configurable": {"thread_id": thread_id}})
    state_values = getattr(snapshot, "values", {})

    return state_values, round_outputs, final_json, final_text


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _integration_enabled(),
    reason="Set RUN_LLM_INTEGRATION=1 to run the 11-round LLM memory integration test.",
)
async def test_agent_retains_travel_state_after_11_rounds():
    if not settings.openai_api_key:
        pytest.skip("OPENAI-compatible API key is not configured.")

    state_values, round_outputs, final_json, final_text = await _run_memory_scenario()

    assert state_values.get("travel_state") == INITIAL_TRAVEL_STATE
    assert "3000-5000" in round_outputs["round_5_budget"], round_outputs["round_5_budget"]
    assert "情侣" in round_outputs["round_8_group"], round_outputs["round_8_group"]
    assert _normalize_travel_state(final_json) == INITIAL_TRAVEL_STATE, final_text


if __name__ == "__main__":
    if not _integration_enabled():
        raise SystemExit("Please set RUN_LLM_INTEGRATION=1 before running this script.")

    async def _main():
        state_values, round_outputs, final_json, final_text = await _run_memory_scenario()
        print("Checkpoint travel_state:")
        print(json.dumps(state_values.get("travel_state"), ensure_ascii=False, indent=2))
        print("\nRound 5 budget answer:")
        print(round_outputs["round_5_budget"])
        print("\nRound 8 travel group answer:")
        print(round_outputs["round_8_group"])
        print("\nRound 11 model output:")
        print(final_text)
        print("\nParsed JSON:")
        print(json.dumps(final_json, ensure_ascii=False, indent=2))

    asyncio.run(_main())
