"""Multi-Agent LangGraph
架构：Orchestrator → [Searcher ∥ WeatherKnowledge] → Planner
非规划意图降级为 DirectAgent（全工具 ReAct）
"""

import asyncio
from collections import Counter
import json
from pathlib import Path
from typing import Annotated, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import Send
from typing_extensions import TypedDict
import datetime as _dt

from agent.config import settings
from agent.retry_manager import SmartRetryManager
from agent.state import extract_travel_state, classify_intent

TOOL_CALL_STATS: Counter[str] = Counter()
retry_manager = SmartRetryManager()

# ── 工具名称分组 ───────────────────────────────────────────────────────
POI_TOOL_NAMES = {"search_poi", "get_poi_detail"}
WEATHER_TOOL_NAMES = {"get_current_weather", "get_weather_forecast"}
KNOWLEDGE_TOOL_NAMES = {"search_knowledge", "add_knowledge"}
TRANSPORT_TOOL_NAMES = {"resolve_location", "plan_walking_route", "plan_driving_route", "plan_transit_route"}
FLIGHT_TOOL_NAMES = {"search_flights"}

ROUTE_JUDGEMENT_KEYWORDS = (
    "步行", "打车", "驾车", "地铁", "公交",
    "通勤", "顺路", "多久", "分钟", "小时", "到达", "路线",
)

# ── MCP 内容块提取 ─────────────────────────────────────────────────────
def _extract_mcp_text(content) -> str:
    if hasattr(content, "content"):
        return _extract_mcp_text(content.content)
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


def _mentions_route_judgement(text: str) -> bool:
    return bool(text) and any(k in text for k in ROUTE_JUDGEMENT_KEYWORDS)


SYSTEM_PROMPT = """你是「智慧旅游助手」，一个专业的旅行规划 AI Agent。

【当前日期】{today}  ← 所有涉及日期的场景（订票、行程、天气）必须以此为基准，禁止使用历史年份。

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

规划与路线验证规则：
1. 规划行程时默认按「先用 poi 确认候选点位 -> 再用 transport 验证点位间路程/耗时 -> 最后输出行程」执行
2. 只要回答里涉及“步行多久、打车多久、是否顺路、一天能否跑完、建议地铁还是打车”这类判断，必须先调用 transport 工具，禁止凭常识估算
3. 如果只有地点名没有坐标，优先使用支持地点名输入的 transport 工具，必要时先调用 resolve_location 再验证路线
4. 若用户问题涉及两个及以上具体点位，默认至少补一次路线验证后再给最终建议

示例 1：
用户：上午去西湖，下午去灵隐寺，顺路吗？
正确做法：先调用 poi 搜索或解析西湖、灵隐寺坐标，再调用 transport 验证路线，最后根据真实路程与耗时回答是否顺路。

示例 2：
用户：河坊街和西溪湿地一天能跑完吗？
正确做法：先确认两个点位，再调用 transport 获取通勤时间；只有拿到工具结果后，才能判断一天是否可行。

工具结果透传规则：
- 工具返回的 Markdown 内容（尤其是链接 `[文字](url)`）必须**原文**输出到回复中，禁止用"已生成链接"、"可点击查看"等文字替代，用户必须在你的回复里看到可点击的真实链接。
"""

# ── 各 Agent Prompts ───────────────────────────────────────────────────
_SEARCHER_PROMPT = """你是景点搜索专员。根据用户的旅行需求，调用 poi 工具搜索相关景点、美食、酒店等兴趣点。

要求：
1. 搜索数量覆盖用户天数（每天至少 3-4 个候选地点）
2. 涵盖用户偏好的类型（文化、美食、自然、购物等）
3. 输出结构化列表：地点名称、分类、地址/坐标、简介

只负责搜索，不规划行程，不判断路线。{travel_context}"""

_WK_PROMPT = """你是天气与知识查询专员。请完成以下两项任务：
1. 调用 weather 工具查询目的地天气预报（含未来几天）
2. 调用 knowledge 工具检索目的地旅游文化背景（景点历史、特色、攻略）

请尽量在第一轮同时发起这两个工具调用，然后整理成结构化报告输出。{travel_context}"""

_PLANNER_PROMPT = """你是行程规划专员。你已拿到以下三方数据：

【候选景点列表（来自搜索专员）】
{searcher_result}

【天气预报与文化背景（来自天气知识专员）】
{wk_result}

你的任务：
1. 根据天气情况筛选调整景点（雨天推室内，晴天可安排户外）
2. 调用 transport 工具验证关键景点间的路线和通勤时间
3. 综合所有信息，输出按天分段的详细行程规划

规则：
- 凡涉及"步行多久、打车多久、是否顺路、建议交通方式"，必须先调用 transport 工具验证，禁止凭常识估算
- 工具返回的 Markdown 链接必须原文输出，不得替换为文字说明
- 每个地点包含：名称、建议游玩时长、与下一站的交通方式和时间
- 当前日期：{today}{travel_context}"""

_DIRECT_AGENT_PROMPT = """你是「智慧旅游助手」，一个专业的旅行规划 AI Agent。

【当前日期】{today}  ← 所有涉及日期的场景必须以此为基准。

你可以调用以下工具帮助用户：
- weather 类工具：查询目的地实时天气和未来预报
- poi 类工具：搜索景点、美食、酒店等兴趣点
- transport 类工具：规划步行/驾车/公交路线
- knowledge 类工具：检索旅游文化知识、历史背景、旅行攻略
- flight 类工具：搜索航班信息

规则：
1. 涉及"步行多久、打车多久、是否顺路"等判断，必须先调用 transport 工具，禁止凭常识估算
2. 工具返回的 Markdown 链接必须原文输出{travel_context}"""

# ── MCP 配置 ──────────────────────────────────────────────────────────
# 从 mcp_config.json 加载 server 配置
_config_path = Path(__file__).resolve().parent.parent / "mcp_config.json"
_mcp_config: dict = json.loads(_config_path.read_text())


def _build_server_params() -> dict:
    env_map = {
        "QWEATHER_API_KEY": settings.qweather_api_key,
        "AMAP_API_KEY": settings.amap_api_key,
        "BAIDU_MAP_AK": settings.baidu_map_ak,
        "TENCENT_MAP_KEY": settings.tencent_map_key,
        "REDIS_URL": settings.redis_url,
        "POI_CACHE_TTL_SECONDS": str(settings.poi_cache_ttl_seconds),
        "CHROMA_PERSIST_DIR": settings.chroma_persist_dir,
        "EMBEDDING_MODEL": settings.embedding_model,
    }
    project_root = str(_config_path.parent)
    servers = {}
    for name, cfg in _mcp_config["mcpServers"].items():
        servers[name] = {
            "transport": "stdio",
            "command": cfg["command"],
            "args": cfg["args"],
            "env": env_map,
            "cwd": project_root,
        }
    return servers


def _build_llm(temperature: float = 0.3) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=temperature,
        streaming=True,
    )


# ── State ─────────────────────────────────────────────────────────────
def _keep_if_not_none(current, new):
    return new if new is not None else current


class GraphState(TypedDict):
    messages: Annotated[list, add_messages]
    travel_state: Annotated[Optional[dict], _keep_if_not_none]
    intent: Annotated[Optional[str], _keep_if_not_none]
    user_query: Annotated[Optional[str], _keep_if_not_none]
    searcher_result: Annotated[Optional[str], _keep_if_not_none]
    wk_result: Annotated[Optional[str], _keep_if_not_none]


def _travel_context(travel_state: Optional[dict]) -> str:
    if not travel_state or not travel_state.get("destination"):
        return ""
    interests = "、".join(travel_state.get("interests") or []) or "综合游览"
    return (
        f"\n\n【用户旅行信息】"
        f"目的地：{travel_state.get('destination')}，"
        f"{travel_state.get('days')} 天，"
        f"预算：{travel_state.get('budget')}，"
        f"同行人：{travel_state.get('travel_group')}，"
        f"兴趣：{interests}"
    )


def _unwrap_retry_result(result: dict, context: str):
    if result["success"]:
        return result["data"]
    error = result.get("error")
    if isinstance(error, Exception):
        raise error
    raise RuntimeError(f"{context} 调用失败: {error}")


# ── 单次工具调用（支持并行） ───────────────────────────────────────────
async def _invoke_tool(tool_map: dict, tc: dict, config: RunnableConfig) -> ToolMessage:
    tool = tool_map.get(tc["name"])
    if tool is None:
        return ToolMessage(
            content=f"工具 {tc['name']} 不存在",
            name=tc["name"],
            tool_call_id=tc["id"],
            status="error",
        )
    call_args = {**tc, "type": "tool_call"}
    try:
        resp = await tool.ainvoke(call_args, config)
        TOOL_CALL_STATS[tc["name"]] += 1
        if isinstance(resp, ToolMessage):
            print(f"[TOOL] {tc['name']} | {_extract_mcp_text(resp.content)[:200]}")
            return resp
        content = _extract_mcp_text(resp)
        print(f"[TOOL] {tc['name']} | {content[:200]}")
        return ToolMessage(content=content, name=tc["name"], tool_call_id=tc["id"])
    except Exception as e:
        return ToolMessage(
            content=f"工具执行失败: {e}",
            name=tc["name"],
            tool_call_id=tc["id"],
            status="error",
        )


# ── ReAct 子循环（节点内部，同批次工具并行执行） ──────────────────────
async def _react_loop(
    llm_with_tools,
    tool_map: dict,
    messages: list,
    config: RunnableConfig,
    label: str,
    max_iterations: int = 10,
) -> str:
    """在节点内部运行 ReAct 循环，每轮的多个 tool_calls 并行执行，返回最终文本"""
    current = list(messages)
    for i in range(max_iterations):
        result = await retry_manager.execute_with_retry(
            f"openai_{label}", llm_with_tools.ainvoke, current, config=config
        )
        response = _unwrap_retry_result(result, label)
        current.append(response)
        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            return _extract_mcp_text(response.content)
        # 同批次工具调用并行执行
        tool_results = await asyncio.gather(
            *[_invoke_tool(tool_map, tc, config) for tc in tool_calls]
        )
        current.extend(tool_results)
        print(f"[{label}] round {i+1}: called {[tc['name'] for tc in tool_calls]}")
    return _extract_mcp_text(current[-1].content) if current else ""


# ── 构建 Multi-Agent Graph ─────────────────────────────────────────────
async def create_agent_client():
    """创建 MCP 客户端，返回 (client, compiled_graph)"""
    client = MultiServerMCPClient(_build_server_params())
    all_tools = await client.get_tools()

    # 按职责分组工具
    poi_tools     = [t for t in all_tools if t.name in POI_TOOL_NAMES]
    wk_tools      = [t for t in all_tools if t.name in (WEATHER_TOOL_NAMES | KNOWLEDGE_TOOL_NAMES)]
    planner_tools = [t for t in all_tools if t.name in TRANSPORT_TOOL_NAMES]
    tool_map      = {t.name: t for t in all_tools}

    poi_llm     = _build_llm().bind_tools(poi_tools)
    wk_llm      = _build_llm(temperature=0).bind_tools(wk_tools)
    planner_llm = _build_llm().bind_tools(planner_tools)
    direct_llm  = _build_llm().bind_tools(all_tools)

    # ── Orchestrator ──────────────────────────────────────────────────
    async def orchestrator_node(state: GraphState, config: RunnableConfig) -> dict:
        user_query = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, HumanMessage):
                user_query = msg.content
                break
        intent, ts = await asyncio.gather(
            classify_intent(user_query),
            extract_travel_state(user_query),
        )
        print(f"[Orchestrator] intent={intent} | destination={ts.get('destination')}")
        new_ts = ts if ts.get("destination") else state.get("travel_state")
        return {
            "intent": intent,
            "travel_state": new_ts,
            "user_query": user_query,
        }

    def route_after_orchestrator(state: GraphState):
        ts = state.get("travel_state") or {}
        if state.get("intent") == "planning" and ts.get("destination"):
            return [Send("searcher", state), Send("weather_knowledge", state)]
        return "direct_agent"

    # ── Searcher Agent（POI 搜索） ─────────────────────────────────────
    async def searcher_node(state: GraphState, config: RunnableConfig) -> dict:
        ts = state.get("travel_state") or {}
        ctx = _travel_context(ts)
        messages = [
            SystemMessage(content=_SEARCHER_PROMPT.format(travel_context=ctx)),
            HumanMessage(content=f"请搜索适合的景点和餐厅：{state.get('user_query', '')}"),
        ]
        print("[Searcher] 开始搜索 POI...")
        result = await _react_loop(poi_llm, tool_map, messages, config, "searcher")
        print(f"[Searcher] 完成 | len={len(result)}")
        return {"searcher_result": result}

    # ── Weather & Knowledge Agent ──────────────────────────────────────
    async def weather_knowledge_node(state: GraphState, config: RunnableConfig) -> dict:
        ts = state.get("travel_state") or {}
        ctx = _travel_context(ts)
        messages = [
            SystemMessage(content=_WK_PROMPT.format(travel_context=ctx)),
            HumanMessage(content=f"请查询天气和文化背景：{state.get('user_query', '')}"),
        ]
        print("[WeatherKnowledge] 开始查询...")
        result = await _react_loop(wk_llm, tool_map, messages, config, "weather_knowledge", max_iterations=5)
        print(f"[WeatherKnowledge] 完成 | len={len(result)}")
        return {"wk_result": result}

    # ── Planner Agent（汇总三方数据 + 路线规划 → 最终行程） ──────────────
    async def planner_node(state: GraphState, config: RunnableConfig) -> dict:
        ts = state.get("travel_state") or {}
        ctx = _travel_context(ts)
        sys_prompt = _PLANNER_PROMPT.format(
            searcher_result=state.get("searcher_result") or "（暂无景点数据）",
            wk_result=state.get("wk_result") or "（暂无天气/知识数据）",
            today=_dt.date.today().isoformat(),
            travel_context=ctx,
        )
        messages = [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=f"请生成最终行程规划：{state.get('user_query', '')}"),
        ]
        print("[Planner] 开始行程规划...")
        result = await _react_loop(planner_llm, tool_map, messages, config, "planner")
        print("[Planner] 完成")
        return {"messages": [AIMessage(content=result)]}

    # ── Direct Agent（非规划意图 / 无目的地 降级） ─────────────────────
    async def direct_agent_node(state: GraphState, config: RunnableConfig) -> dict:
        ts = state.get("travel_state")
        ctx = _travel_context(ts)
        sys_prompt = _DIRECT_AGENT_PROMPT.format(
            today=_dt.date.today().isoformat(),
            travel_context=ctx,
        )
        messages = [SystemMessage(content=sys_prompt)] + list(state["messages"])
        print("[DirectAgent] 开始直接对话...")
        result = await _react_loop(direct_llm, tool_map, messages, config, "direct_agent")
        return {"messages": [AIMessage(content=result)]}

    # ── 组装 Graph ─────────────────────────────────────────────────────
    graph = StateGraph(GraphState)
    graph.add_node("orchestrator",      orchestrator_node)
    graph.add_node("searcher",          searcher_node)
    graph.add_node("weather_knowledge", weather_knowledge_node)
    graph.add_node("planner",           planner_node)
    graph.add_node("direct_agent",      direct_agent_node)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges("orchestrator", route_after_orchestrator)
    graph.add_edge("searcher",          "planner")
    graph.add_edge("weather_knowledge", "planner")
    graph.add_edge("planner",           END)
    graph.add_edge("direct_agent",      END)

    compiled = graph.compile(checkpointer=MemorySaver())
    return client, compiled


# ── 流式调用入口（供 server/main.py 使用，签名不变） ──────────────────
# 只有 planner / direct_agent 节点的 LLM token 流向前端
_STREAMING_NODES = {"planner", "direct_agent"}


async def invoke_agent(
    agent,
    user_message: str,
    thread_id: str = "default",
    travel_state: Optional[dict] = None,
):
    """流式调用 multi-agent graph，yield SSE 事件字典"""
    config = {"configurable": {"thread_id": thread_id}}
    input_msg: dict = {"messages": [{"role": "user", "content": user_message}]}
    if travel_state is not None:
        input_msg["travel_state"] = travel_state

    _tool_names: list[str] = []
    _streamed_by_node: dict[str, str] = {}

    async for event in agent.astream_events(input_msg, config=config, version="v2"):
        kind = event["event"]
        node = event.get("metadata", {}).get("langgraph_node", "")

        # ── token 流：只有最终输出节点推给前端 ──────────────────────────
        if kind == "on_chat_model_stream" and node in _STREAMING_NODES:
            content = _extract_mcp_text(event["data"]["chunk"].content)
            if content:
                _streamed_by_node.setdefault(node, "")
                _streamed_by_node[node] += content
                yield {"type": "token", "content": content}

        # on_chat_model_end：补发缺失文本段（兼容不稳定流式模型）
        elif kind == "on_chat_model_end" and node in _STREAMING_NODES:
            msg = event["data"].get("output")
            if msg:
                text = _extract_mcp_text(getattr(msg, "content", ""))
                streamed = _streamed_by_node.get(node, "")
                if text and text != streamed:
                    missing = text[len(streamed):] if text.startswith(streamed) else text
                    if missing:
                        yield {"type": "token", "content": missing}

        # ── 工具调用事件：所有节点都推（前端可显示进度指示器） ───────────
        elif kind == "on_tool_start":
            tool_name = event["name"]
            _tool_names.append(tool_name)
            raw_input = event["data"].get("input", {})
            safe_input = {
                k: v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
                for k, v in (raw_input.items() if isinstance(raw_input, dict) else {})
            }
            yield {"type": "tool_call", "tool": tool_name, "input": safe_input, "node": node}

        elif kind == "on_tool_end":
            text_output = _extract_mcp_text(event["data"].get("output", ""))
            yield {
                "type": "tool_result",
                "tool": event["name"],
                "output": text_output[:2000],
                "node": node,
            }

    print(
        f"[TOOL STATS] this_turn={_tool_names} | "
        f"total={dict(TOOL_CALL_STATS)}"
    )
