"""FastAPI 后端 — SSE 流式输出 Agent 响应"""

import json
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.graph import create_agent_client, invoke_agent
from agent.state import extract_travel_state, BUDGET_OPTIONS, TRAVEL_GROUP_OPTIONS, INTEREST_OPTIONS

# 全局 agent 实例
_client = None
_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _agent
    _client, _agent = await create_agent_client()
    yield


app = FastAPI(title="智慧旅游 Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    travel_state: Optional[dict] = None


@app.post("/api/extract")
async def extract(req: ChatRequest):
    """从用户消息中提取结构化旅行信息"""
    state = await extract_travel_state(req.message)
    return {
        "state": state,
        "options": {
            "budget": BUDGET_OPTIONS,
            "travel_group": TRAVEL_GROUP_OPTIONS,
            "interests": INTEREST_OPTIONS,
        },
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    async def generate():
        async for event in invoke_agent(_agent, req.message, session_id, req.travel_state):
            yield {"data": json.dumps(event, ensure_ascii=False)}
        yield {"data": json.dumps({"type": "done", "session_id": session_id}, ensure_ascii=False)}

    return EventSourceResponse(generate())


@app.get("/api/health")
async def health():
    return {"status": "ok", "tools": _agent is not None}
