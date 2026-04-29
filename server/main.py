"""FastAPI 后端 — SSE 流式输出 Agent 响应"""

import json
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.config import settings
from agent.graph import create_agent_client, invoke_agent
from agent.state import (
    extract_travel_state, classify_intent, parse_itinerary,
    BUDGET_OPTIONS, TRAVEL_GROUP_OPTIONS, INTEREST_OPTIONS,
)

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


@app.post("/api/intent")
async def intent(req: ChatRequest):
    """识别用户消息意图：planning / booking / other"""
    label = await classify_intent(req.message)
    return {"intent": label}


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


@app.get("/api/config/amap_key")
async def amap_key():
    """向前端暴露高德地图 Web JS Key"""
    return {"key": settings.amap_js_key or ""}


class ParseItineraryRequest(BaseModel):
    text: str


@app.post("/api/parse_itinerary")
async def parse_itinerary_endpoint(req: ParseItineraryRequest):
    """将行程文本解析为结构化 JSON，并通过高德 Geocoding 补充坐标"""
    days = await parse_itinerary(req.text)
    if not days:
        return {"days": []}

    amap_key = settings.amap_api_key
    async with httpx.AsyncClient(timeout=10) as client:
        for day in days:
            for spot in day.get("spots", []):
                name = spot.get("name", "")
                if not name or not amap_key:
                    spot["lng"] = None
                    spot["lat"] = None
                    continue
                try:
                    resp = await client.get(
                        "https://restapi.amap.com/v3/geocode/geo",
                        params={"key": amap_key, "address": name, "output": "JSON"},
                    )
                    data = resp.json()
                    geocodes = data.get("geocodes") or []
                    if geocodes:
                        loc = geocodes[0]["location"].split(",")
                        spot["lng"] = float(loc[0])
                        spot["lat"] = float(loc[1])
                    else:
                        spot["lng"] = None
                        spot["lat"] = None
                except Exception:
                    spot["lng"] = None
                    spot["lat"] = None

    return {"days": days}
