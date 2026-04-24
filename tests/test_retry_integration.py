"""验证 state.py / graph.py 的 LLM 调用确实经过 SmartRetryManager。"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

import agent.graph as graph_module
import agent.state as state_module


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeChatOpenAI:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def ainvoke(self, *args, **kwargs):
        raise AssertionError("llm.ainvoke 不应被直接调用，应该经由 retry_manager.execute_with_retry")


class FakeBoundLLM:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, *args, **kwargs):
        raise AssertionError("bound llm.ainvoke 不应被直接调用，应该经由 retry_manager.execute_with_retry")


class FakeMCPClient:
    def __init__(self, params):
        self.params = params

    async def get_tools(self):
        return []


def test_extract_travel_state_uses_retry_manager(monkeypatch):
    calls: list[tuple[str, object, tuple, dict]] = []

    async def fake_execute_with_retry(module_name, coro_fn, *args, **kwargs):
        calls.append((module_name, coro_fn, args, kwargs))
        return {
            "success": True,
            "data": FakeResponse(
                '{"destination":"杭州","days":3,"budget":"适中","travel_group":"情侣出游","interests":["美食探索"]}'
            ),
        }

    monkeypatch.setattr(state_module, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(state_module.retry_manager, "execute_with_retry", fake_execute_with_retry)

    result = asyncio.run(state_module.extract_travel_state("帮我规划杭州三日情侣美食游"))

    assert calls
    assert calls[0][0] == "openai_extract_state"
    assert calls[0][2][0][0]["role"] == "user"
    assert result["destination"] == "杭州"
    assert result["days"] == 3
    assert result["budget"] == "适中"
    assert result["travel_group"] == "情侣出游"
    assert result["interests"] == ["美食探索"]


def test_graph_agent_node_uses_retry_manager_and_guard(monkeypatch):
    calls: list[str] = []

    async def fake_execute_with_retry(module_name, coro_fn, *args, **kwargs):
        calls.append(module_name)
        if module_name == "openai_agent":
            return {"success": True, "data": AIMessage(content="从西湖到灵隐寺打车约20分钟", tool_calls=[])}
        if module_name == "openai_agent_guard":
            return {"success": True, "data": AIMessage(content="已补充路线验证后再输出建议", tool_calls=[])}
        raise AssertionError(f"unexpected retry call: {module_name}")

    monkeypatch.setattr(graph_module, "MultiServerMCPClient", FakeMCPClient)
    monkeypatch.setattr(graph_module, "_build_llm", lambda: FakeBoundLLM())
    monkeypatch.setattr(graph_module.retry_manager, "execute_with_retry", fake_execute_with_retry)

    async def run():
        _client, agent = await graph_module.create_agent_client()
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="西湖到灵隐寺顺路吗？")], "travel_state": None},
            config={"configurable": {"thread_id": "retry-integration-test"}},
        )
        return result

    result = asyncio.run(run())

    assert calls == ["openai_agent", "openai_agent_guard"]
    assert result["messages"][-1].content == "已补充路线验证后再输出建议"
