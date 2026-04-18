# TravelState 持久化迁移 — 复盘文档

> 起点：用户提出需求「把 TravelState 存到 LangGraph 的 State 图 schema 里，做到真正持久化」  
> 终点：一个自定义 StateGraph，TravelState 作为状态字段持久保存，工具链正常工作  
> 经历时间：约 10 次对话轮次  
> 涉及文件：`agent/graph.py`

---

## 背景：为什么要做这个改动

改动前，`agent/graph.py` 使用 `create_react_agent`：
每次从前端输入的表单数据直接当成message传给chat api  不经过后端state 

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(llm, tools, checkpointer=MemorySaver())
```

用户每次发起规划时，前端会把 `travel_state`（目的地、天数、预算等）通过请求体发过来，后端每次都把它拼进 system message。

**问题**：跨轮对话时（第一轮规划完、第二轮追问），前端如果没有再重新传 `travel_state`，第二轮的 system message 里就没有旅行信息了，Agent 会"忘掉"用户的基本信息。

**目标**：把 `travel_state` 放进 LangGraph 的图 schema，让 `MemorySaver` checkpointer 同时持久化它，而不是每次请求都依赖前端重传。

---

## 迁移过程

---

### Step 1 — 用 StateGraph 替换 create_react_agent

**改动文件**：`agent/graph.py`

**改动内容**：

```python
# 删除
from langgraph.prebuilt import create_react_agent

# 新增
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

def _keep_if_not_none(current, new):
    return new if new is not None else current

class GraphState(TypedDict):
    messages: Annotated[list, add_messages]
    travel_state: Annotated[Optional[dict], _keep_if_not_none]

async def create_agent_client():
    ...
    tool_node = ToolNode(tools)

    async def agent_node(state):
        sys_msg = _make_system_message(state.get("travel_state"))
        messages = [sys_msg] + list(state["messages"])
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(GraphState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    compiled = graph.compile(checkpointer=MemorySaver())
```

**设计意图**：

- `GraphState` 新增 `travel_state` 字段，与 `messages` 一起被 checkpointer 持久化
- `_keep_if_not_none` reducer：新值为 None 时保留旧值（跨轮次持久）；新值不为 None 时更新（首次写入或显式刷新）
- `ToolNode` + `tools_condition` 是 LangGraph 官方提供的：前者执行工具，后者检查最后一条消息有无 `tool_calls` 来决定是否继续

---

### Step 2 — 失败：ToolRuntime 注入导致工具调用失败 & 递归超限

**现象**：Agent 反复调工具，永远不输出正文内容，最终触发 `recursion_limit=25`。

**后端日志**：

```
[TOOL CALL] get_current_weather | input: {
  'city': '杭州',
  'runtime': "ToolRuntime(state={'messages': [...], 'travel_state': {...}}, ...)"
}
```

**根因**：

`ToolNode` 是 LangGraph 内置工具执行器，它有一套依赖注入机制，能把完整的图运行时上下文（state、config、store 等）包装成 `ToolRuntime` 对象注入到工具参数里。

```python
# ToolNode 内部逻辑（简化）
injected_call = self._inject_tool_args(call, tool_runtime)
# injected_call["args"] 里会多一个 runtime=ToolRuntime(...) 字段
response = await tool.ainvoke({**injected_call, "type": "tool_call"}, config)
```

注入条件是：工具的函数签名里通过 `InjectedState` / `InjectedStore` / `ToolRuntime` 注解声明了依赖。MCP 工具通过 `langchain-mcp-adapters` 封装后，函数签名包含 `**kwargs`，ToolNode 识别到这个，就把整个 `ToolRuntime` 作为 `runtime=` 参数塞了进去。

MCP 工具收到一个叫 `runtime` 的参数，值是 LangGraph 内部对象的 string repr，作为工具参数传给了实际的 API 请求逻辑，导致工具执行结果异常。

LLM 收到异常结果后认为任务还没完成，决定再次调工具，如此循环直到 `recursion_limit`。

**修复**：用自定义 `tools_node` 替换 `ToolNode`，在调用工具时不经过 `_inject_tool_args`：

```python
# 删除
from langgraph.prebuilt import ToolNode, tools_condition
tool_node = ToolNode(tools)

# 新增
tool_map = {t.name: t for t in tools}

async def tools_node(state: GraphState) -> dict:
    last = state["messages"][-1]
    results = []
    for tc in getattr(last, "tool_calls", []):
        tool = tool_map.get(tc["name"])
        try:
            content = await tool.ainvoke(tc["args"])  # 只传 args，不传 runtime
        except Exception as e:
            content = f"工具执行失败: {e}"
        results.append(ToolMessage(content=str(content), tool_call_id=tc["id"]))
    return {"messages": results}

def should_continue(state):
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END
```

---

### Step 3 — 失败：前端工具可视化消失（on_tool_start / on_tool_end 不触发）

**现象**：工具调用在后端执行了，但前端 UI 里完全看不到工具调用卡片，工具输入/ 输出均不显示。

**根因**：

LangChain 的流式事件（`on_tool_start`、`on_tool_end`）是通过 callback 机制触发的。LangGraph 会把当前运行的 `RunnableConfig`（内含 callbacks）透传给节点，节点再把它传给工具，工具执行时才能触发事件。

上一步的 `tools_node` 是这么调的：

```python
content = await tool.ainvoke(tc["args"])   # 没有传 config！
```

`config` 丢失 → callback 链断裂 → 工具执行了，但 `on_tool_start`/`on_tool_end` 事件从未被触发 → 后端 `invoke_agent` 的事件循环里永远等不到工具事件 → 前端收不到 `tool_call`/`tool_result` 消息。

**修复**：让 `tools_node` 接收 `config: RunnableConfig` 并透传：

```python
from langchain_core.runnables import RunnableConfig

async def tools_node(state: GraphState, config: RunnableConfig) -> dict:
    ...
    content = await tool.ainvoke(tc["args"], config)  # 透传 config
```

LangGraph 的节点签名如果有第二个参数且类型是 `RunnableConfig`，框架会自动注入当前运行的 config。

---

### Step 4 — 失败：MCP 工具返回内容块列表，LLM 无法解析

**现象**：工具调用和工具结果都在前端显示了，但模型回复里看到的是：

```
[{'type': 'text', 'text': '在杭州搜索...'}]
```

并且模型会继续反复调工具，不输出最终规划内容。

**根因**：

MCP 协议的工具返回格式是**内容块列表**（content block list），而不是纯字符串：

```python
[{'type': 'text', 'text': '在杭州搜索"西湖"的结果：\n• 西湖...'}]
```

上一步的代码这样处理返回值：

```python
content = await tool.ainvoke(tc["args"], config)
results.append(ToolMessage(content=str(content), ...))
#                                   ^^^^^^^^^^^
# str([{'type': 'text', 'text': '...'}]) → Python list repr 字符串
```

`str()` 直接序列化 list 得到的是 Python 的字面量表示：

```
"[{'type': 'text', 'text': '在杭州...'}]"
```

LLM 拿到这个 `ToolMessage` 后尝试解析内容，发现是一串奇怪的 Python 对象字符串，无法理解为"工具执行成功了，结果是xxxx"。于是判断工具执行异常，决定重试，继续调工具直到递归上限，永远不进入最终文字输出阶段。

这个问题在使用 `create_react_agent` 时不存在，因为官方实现里有 `msg_content_output()` 函数会自动展平内容块。

**修复**：引入 `_extract_mcp_text()` 函数统一提取纯文本：

```python
def _extract_mcp_text(content) -> str:
    # 先解包 LangChain Message 对象（.content 本身可能是 list）
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
```

**注意此处的顺序很关键**：必须先检查 `hasattr(content, "content")`，因为 `tool.ainvoke()` 有时返回的是 `ToolMessage` 对象，它的 `.content` 属性本身是一个 list。如果先检查 `isinstance(content, list)` 就会漏掉这种情况。

---

### Step 5 — 失败：仍然反复调工具，不输出正文

**现象**：加了 `_extract_mcp_text` 后工具结果文字正确了，但模型还是循环调工具不出正文。

**根因**：

工具调用时传的参数格式不对：

```python
content = await tool.ainvoke(tc["args"], config)
#                            ^^^^^^^^^^
# 只传了 args 字典，格式是 {"city": "杭州"}
```

LangChain MCP 工具的 `ainvoke` 期望接收的是 **完整的 tool_call 结构**，不是裸 args：

```python
# ainvoke 期望的格式
tool_call = {
    "name": "get_current_weather",
    "args": {"city": "杭州"},
    "id": "call_xxx",
    "type": "tool_call"    # ← 这个字段是关键，告诉 LangChain 这是一次工具调用
}
await tool.ainvoke(tool_call, config)
```

当只传 `tc["args"]` 时，工具内部的参数校验和调用追踪机制失效，执行出来的 `ToolMessage` 可能缺少 `tool_call_id` 或者结果回路不完整，导致 LLM 无法把这个 `ToolMessage` 和之前发出的 `tool_calls` 对应起来，重新进入"我还没收到工具结果"的判断，继续调工具。

**修复**：透传完整 tool_call 结构，工具自己返回 ToolMessage 就直接保留：

```python
call_args = {**tc, "type": "tool_call"}  # 保证 type 字段存在

response = await tool.ainvoke(call_args, config)

if isinstance(response, ToolMessage):
    results.append(response)          # 原样保留工具返回的完整 ToolMessage
else:
    content = _extract_mcp_text(response)
    results.append(ToolMessage(content=content, name=tc["name"], tool_call_id=tc["id"]))
```

---

### Step 6 — 兜底：on_chat_model_stream 不稳定时的文本补发

**现象**（低复现率）：在某些模型 / 某些版本下，最后一轮 LLM 生成文字时 `on_chat_model_stream` 流式事件不触发，只触发 `on_chat_model_end`。

**修复**：在 `on_chat_model_end` 里检查流式收到的文本和最终文本是否一致，不一致时补发差量：

```python
_streamed_text = ""   # 每轮 LLM 调用开始时清零

elif kind == "on_chat_model_end":
    text = _extract_mcp_text(msg.content)
    if text != _streamed_text:
        missing = text[len(_streamed_text):] if text.startswith(_streamed_text) else text
        if missing:
            yield {"type": "token", "content": missing}

elif kind == "on_chat_model_stream":
    content = _extract_mcp_text(chunk.content)
    if content:
        _streamed_text += content
        yield {"type": "token", "content": content}
```
---

### Step 7 — 
```python
# ❌ 修改前

async def agent_node(state: GraphState) -> dict:
sys_msg = _make_system_message(state.get("travel_state"))
messages = [sys_msg] + list(state["messages"])
response = await llm.ainvoke(messages)
return {"messages": [response]}

# ✅ 修改后

async def agent_node(state: GraphState, config: RunnableConfig) -> dict:
sys_msg = _make_system_message(state.get("travel_state"))
messages = [sys_msg] + list(state["messages"])
response = await llm.ainvoke(messages, config)
return {"messages": [response]}
```
改动只有两处：

1. 函数签名加了 config: RunnableConfig 参数
2. llm.ainvoke(messages) → llm.ainvoke(messages, config)
---

## 完整改动汇总

| 步骤 | 改动 | 失败原因 | 修复方向 |
|------|------|----------|----------|
| 1 | 用 StateGraph 替换 create_react_agent，添加 GraphState / MemorySaver | — | 持久化架构设计正确 |
| 2 | 使用 ToolNode | ToolNode 向 MCP 工具注入 ToolRuntime，工具参数被污染，工具执行失败，Agent 无限重试 | 自定义 tools_node 绕过注入 |
| 3 | tools_node 直接调用 tool.ainvoke(args) | 未透传 config，callback 链断裂，on_tool_start/end 不触发，前端工具可视化消失 | 接收并透传 config: RunnableConfig |
| 4 | ainvoke 返回值直接 str() | MCP 返回 [{'type':'text','text':'...'}] 内容块列表，str() 后 LLM 无法解析，认为工具失败继续重试 | _extract_mcp_text() 递归展平内容块 |
| 5 | 只传 tc["args"] 给 ainvoke | 缺少 type: tool_call 字段，工具返回的 ToolMessage 与 tool_call_id 对应链断裂，LLM 认为未收到工具结果 | 传完整 {**tc, "type": "tool_call"}，保留原生 ToolMessage |
| 6 | 流式输出 | 部分模型 on_chat_model_stream 不稳定 | on_chat_model_end 补发差量文本 |

---

## 关键知识点

### 1. ToolNode 的依赖注入机制

官方 `ToolNode` 设计上支持工具声明"我需要读取图 state"：

```python
from langgraph.prebuilt import InjectedState

@tool
def my_tool(query: str, state: Annotated[dict, InjectedState]) -> str:
    ...
```

它在调用前会扫描工具签名，把 `InjectedState` 替换成实际的图 state，这是设计功能。  
但 MCP 工具的函数签名里有 `**kwargs`，`ToolNode` 把它解读成"接受任意注入"，导致误注入。

### 2. LangChain tool.ainvoke 的两种调用格式

```python
# 格式 A：裸 args dict（用于简单调用）
await tool.ainvoke({"city": "杭州"})

# 格式 B：完整 tool_call（用于 Agent 场景，保持 tool_call_id 绑定）
await tool.ainvoke({
    "name": "get_current_weather",
    "args": {"city": "杭州"},
    "id": "call_xxx",
    "type": "tool_call"
})
```

Agent 场景下必须用格式 B，因为 LLM 需要把 `AIMessage.tool_calls[i].id` 和后续 `ToolMessage.tool_call_id` 对应起来，才能知道"哪个工具调用返回了哪个结果"。

### 3. MCP 内容块格式

MCP 协议规定工具返回值是 content block list：

```python
[
    {"type": "text", "text": "真正的内容"},
    # 理论上还支持 image / resource 等类型
]
```

官方 `ToolNode` 内部用 `msg_content_output()` 展平，自定义节点需要自己处理这个格式。

### 4. config 透传

LangGraph 节点如果声明第二个参数 `config: RunnableConfig`，框架会自动注入。这个 config 包含当前运行的所有 callbacks。只要工具调用链上任何一层丢失了 config，所有流式事件（on_tool_start/end 等）都会静默失失。

---

## 最终架构

```
invoke_agent()
    │
    └── agent.astream_events()
            │
     ┌──── StateGraph(GraphState) ────┐
     │                                │
     │  GraphState:                   │
     │   - messages (add_messages)    │
     │   - travel_state (_keep_if_not_none)
     │                                │
     │  [agent_node]                  │
     │    sys_msg = _make_system_message(state.travel_state)
     │    response = llm.ainvoke([sys_msg] + messages, config)
     │                                │
     │  should_continue()             │
     │    has tool_calls? → tools     │
     │    no → END                    │
     │                                │
     │  [tools_node]                  │
     │    for tc in last.tool_calls:  │
     │      call_args = {**tc, "type": "tool_call"}
     │      response = tool.ainvoke(call_args, config)
     │      results.append(response)  │
     └────────────────────────────────┘
            │
    invoke_agent 监听事件:
      on_chat_model_stream → yield token
      on_chat_model_end    → 补发未流式化文本
      on_tool_start        → yield tool_call
      on_tool_end          → yield tool_result
```
