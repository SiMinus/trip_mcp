"""
异步 SSE 客户端 — 调用 /api/chat 并收集所有事件
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

import httpx

from .config import BASE_URL, REQUEST_TIMEOUT


async def chat_sse(
    message: str,
    session_id: Optional[str] = None,
    travel_state: Optional[dict] = None,
    base_url: str = BASE_URL,
    timeout: int = REQUEST_TIMEOUT,
) -> list[dict]:
    """
    POST /api/chat，解析 SSE 流直到收到 done 事件。

    返回收集到的全部事件列表，事件类型包括：
      tool_call   — 工具被调用（含 tool 名称、输入参数、所属 node）
      tool_result — 工具返回结果（含 tool 名称、输出文本）
      token       — LLM 流式文字片段（用于最终回复，评估时可忽略）
      done        — 流结束标志
      error       — 网络或超时错误（评估框架内部添加，非服务端事件）
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    payload: dict = {
        "message": message,
        "session_id": session_id,
        "travel_state": travel_state,
    }

    events: list[dict] = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0)
        ) as client:
            async with client.stream(
                "POST", f"{base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                        events.append(event)
                        if event.get("type") == "done":
                            break
                    except json.JSONDecodeError:
                        pass  # 忽略非 JSON 行（心跳、注释等）

    except httpx.TimeoutException:
        events.append(
            {"type": "error", "error": f"请求超时（>{timeout}s）"}
        )
    except httpx.HTTPStatusError as exc:
        events.append(
            {"type": "error", "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        )
    except Exception as exc:
        events.append({"type": "error", "error": str(exc)})

    return events
