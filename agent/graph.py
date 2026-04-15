"""LangGraph ReAct Agent — 通过 MCP 协议真实调用 4 个工具服务"""

import json
from pathlib import Path
from typing import Annotated, Optional

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from agent.config import settings

# ── MCP 内容块提取 ─────────────────────────────────────────────────────
def _extract_mcp_text(content) -> str:
    """将 MCP 工具返回值统一转为纯文本字符串。
    MCP 工具可能返回：
      - ToolMessage / AIMessage 等对象（.content 可能是 str 或 list）
      - [{'type': 'text', 'text': '...'}] 内容块列表
      - 纯字符串
    """
    # 先解包 LangChain Message 对象
    if hasattr(content, "content"):
        return _extract_mcp_text(content.content)
    # MCP 内容块列表
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or str(block))
            elif hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)

SYSTEM_PROMPT = """你是「智慧旅游助手」，一个专业的旅行规划 AI Agent。

你可以调用以下工具帮助用户：
- weather 类工具：查询目的地实时天气和未来预报
- poi 类工具：搜索景点、美食、酒店等兴趣点
- transport 类工具：规划步行/驾车/公交路线
- knowledge 类工具：检索旅游文化知识、历史背景、旅行攻略

工作原则：
1. 先了解用户需求（目的地、天数、偏好），再调用工具获取信息
2. 规划行程时要考虑天气、距离、开放时间等实际因素
3. 给出的建议要具体可执行，包含地点名称、预计时间、交通方式
4. 主动调用知识库补充景点文化背景，让行程更有深度
"""

# 从 mcp_config.json 加载 server 配置
_config_path = Path(__file__).resolve().parent.parent / "mcp_config.json"
_mcp_config: dict = json.loads(_config_path.read_text())


def _build_server_params() -> dict:
    """将 mcp_config.json 转为 MultiServerMCPClient 接受的格式，并注入环境变量"""
    env_map = {
        "QWEATHER_API_KEY": settings.qweather_api_key,
        "AMAP_API_KEY": settings.amap_api_key,
        "CHROMA_PERSIST_DIR": settings.chroma_persist_dir,
        "EMBEDDING_MODEL": settings.embedding_model,
    }
    servers = {}
    for name, cfg in _mcp_config["mcpServers"].items():
        servers[name] = {
            "transport": "stdio",
            "command": cfg["command"],
            "args": cfg["args"],
            "env": env_map,
        }
    return servers


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.3,
        streaming=True,
    )


# ── 自定义 State ──────────────────────────────────────────────────────
def _keep_if_not_none(current: Optional[dict], new: Optional[dict]) -> Optional[dict]:
    """travel_state reducer：新值非 None 时覆盖，否则保留原值（跨轮次持久）"""
    return new if new is not None else current


class GraphState(TypedDict):
    messages: Annotated[list, add_messages]
    travel_state: Annotated[Optional[dict], _keep_if_not_none]


# ── 系统提示动态注入 travel_state ──────────────────────────────────────
def _make_system_message(travel_state: Optional[dict]) -> SystemMessage:
    content = SYSTEM_PROMPT
    if travel_state:
        interests = "、".join(travel_state.get("interests") or []) or "综合游览"
        content += (
            f"\n\n【当前用户已确认的旅行信息，整个对话中保持有效，无需重复询问】\n"
            f"- 目的地：{travel_state.get('destination')}\n"
            f"- 天数：{travel_state.get('days')} 天\n"
            f"- 预算：{travel_state.get('budget')}\n"
            f"- 同行人：{travel_state.get('travel_group')}\n"
            f"- 兴趣偏好：{interests}\n"
        )
    return SystemMessage(content=content)


# ── 构建 Agent ─────────────────────────────────────────────────────────
async def create_agent_client():
    """创建 MCP 客户端，返回 (client, compiled_graph)"""
    client = MultiServerMCPClient(_build_server_params())
    tools = await client.get_tools()
    llm = _build_llm().bind_tools(tools)

    async def agent_node(state: GraphState, config: RunnableConfig) -> dict:
        sys_msg = _make_system_message(state.get("travel_state"))
        messages = [sys_msg] + list(state["messages"])
        response = await llm.ainvoke(messages, config)
        return {"messages": [response]}

    # 自定义 tools_node：手动执行工具调用，不注入 ToolRuntime
    tool_map = {t.name: t for t in tools}

    async def tools_node(state: GraphState, config: RunnableConfig) -> dict:
        last = state["messages"][-1]
        results = []
        for tc in getattr(last, "tool_calls", []):
            tool = tool_map.get(tc["name"])
            if tool is None:
                results.append(
                    ToolMessage(
                        content=f"工具 {tc['name']} 不存在",
                        name=tc["name"],
                        tool_call_id=tc["id"],
                        status="error",
                    )
                )
                continue

            call_args = {**tc, "type": "tool_call"}

            try:
                response = await tool.ainvoke(call_args, config)
                if isinstance(response, ToolMessage):
                    print(
                        f"[DEBUG tools_node] {tc['name']} | response=ToolMessage | extracted[:200]={_extract_mcp_text(response.content)[:200]}"
                    )
                    results.append(response)
                else:
                    content = _extract_mcp_text(response)
                    print(
                        f"[DEBUG tools_node] {tc['name']} | response={type(response).__name__} | extracted[:200]={content[:200]}"
                    )
                    results.append(
                        ToolMessage(
                            content=content,
                            name=tc["name"],
                            tool_call_id=tc["id"],
                        )
                    )
            except Exception as e:
                results.append(
                    ToolMessage(
                        content=f"工具执行失败: {e}",
                        name=tc["name"],
                        tool_call_id=tc["id"],
                        status="error",
                    )
                )
        return {"messages": results}

    def should_continue(state: GraphState):
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    graph = StateGraph(GraphState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    compiled = graph.compile(checkpointer=MemorySaver())
    return client, compiled


async def invoke_agent(
    agent,
    user_message: str,
    thread_id: str = "default",
    travel_state: Optional[dict] = None,
):
    """流式调用 agent，yield 事件字典"""
    config = {"configurable": {"thread_id": thread_id}}
    input_msg: dict = {"messages": [{"role": "user", "content": user_message}]}
    # travel_state 非 None 时写入 State，reducer 会持久保留
    if travel_state is not None:
        input_msg["travel_state"] = travel_state
    _step = 0
    _trace: list[str] = []  # 收集完整推理链，流程结束后统一打印
    _streamed_text = ""

    async for event in agent.astream_events(input_msg, config=config, version="v2"):
        kind = event["event"]

        # 每次 LLM 决策轮开始
        if kind == "on_chain_start" and event.get("name") == "agent":
            _step += 1
            _trace.append(f"\n{'='*50}")
            _trace.append(f"[STEP {_step}] Agent 开始推理...")

        elif kind == "on_chat_model_start":
            _streamed_text = ""

        # LLM 完整输出（推理文字 + 工具调用决策）
        elif kind == "on_chat_model_end":
            msg = event["data"].get("output")
            if msg:
                text = _extract_mcp_text(msg.content) if hasattr(msg, "content") else ""
                tool_calls = getattr(msg, "tool_calls", [])
                if text:
                    _trace.append(f"[THINK] {text[:500]}")
                    # 某些模型在绑定工具后不会稳定地产生 on_chat_model_stream，
                    # 这里用 end 事件补发最终文本，避免前端只有工具调用没有正文。
                    if text != _streamed_text:
                        missing = text[len(_streamed_text):] if text.startswith(_streamed_text) else text
                        if missing:
                            yield {"type": "token", "content": missing}
                for tc in tool_calls:
                    _trace.append(f"[DECIDE] 调用工具: {tc.get('name')} | args: {tc.get('args')}")

        elif kind == "on_chat_model_stream":
            content = _extract_mcp_text(event["data"]["chunk"].content)
            if content:
                _streamed_text += content
                yield {"type": "token", "content": content}

        elif kind == "on_tool_start":
            raw_input = event["data"].get("input", {})
            # ToolRuntime 等不可序列化对象转为字符串
            safe_input = {
                k: v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
                for k, v in (raw_input.items() if isinstance(raw_input, dict) else {})
            }
            _trace.append(f"[TOOL CALL] {event['name']} | input: {safe_input}")
            yield {
                "type": "tool_call",
                "tool": event["name"],
                "input": safe_input,
            }

        elif kind == "on_tool_end":
            output = event["data"].get("output", "")
            text_output = _extract_mcp_text(output)
            _trace.append(f"[TOOL RESULT] {event['name']} | {text_output[:300]}")
            yield {
                "type": "tool_result",
                "tool": event["name"],
                "output": text_output[:2000],
            }

    # 流程全部结束后，一次性打印完整推理链
    print("\n" + "="*20 + " ReAct 完整推理链 " + "="*20)
    for line in _trace:
        print(line)
    print("="*58 + "\n")
