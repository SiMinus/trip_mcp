"""旅行状态结构定义 + 从用户消息中提取结构化信息"""

import json
import re
from typing import Optional
from typing_extensions import TypedDict

from langchain_openai import ChatOpenAI
from agent.config import settings
from agent.retry_manager import SmartRetryManager

retry_manager = SmartRetryManager()


class TravelState(TypedDict):
    destination: Optional[str]      # 目的地
    days: Optional[int]             # 旅行天数
    budget: Optional[str]           # 预算等级
    travel_group: Optional[str]     # 同行人
    interests: Optional[list[str]]  # 兴趣偏好


BUDGET_OPTIONS = ["经济实惠", "适中", "豪华享受"]
TRAVEL_GROUP_OPTIONS = ["独自旅行", "情侣出游", "亲子家庭", "朋友结伴", "带老人出行"]
INTEREST_OPTIONS = ["历史文化", "自然风景", "美食探索", "购物娱乐", "休闲放松", "运动冒险", "艺术展览"]

_EXTRACT_PROMPT = f"""从用户的旅游需求描述中提取以下信息，以 JSON 格式返回。如果某字段在描述中未提及，返回 null。

字段说明：
- destination: 目的地城市或地区（字符串）
- days: 旅行天数（整数，只取数字）
- budget: 预算等级，从 {BUDGET_OPTIONS} 中选最接近的一个，或返回 null
- travel_group: 同行人，从 {TRAVEL_GROUP_OPTIONS} 中选最接近的一个，或返回 null
- interests: 兴趣偏好列表，从 {INTEREST_OPTIONS} 中选多个，返回列表；完全没提到则返回 []

只返回 JSON 对象，不要 markdown 代码块，不要其他文字。

用户描述：{{user_message}}"""


async def extract_travel_state(user_message: str) -> TravelState:
    """调用 LLM 从用户消息中提取结构化旅行信息"""
    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0,
    )
    prompt = _EXTRACT_PROMPT.format(user_message=user_message)
    # response = await llm.ainvoke([{"role": "user", "content": prompt}])
    result = await retry_manager.execute_with_retry(
        "openai_extract_state",
        llm.ainvoke,
        [{"role": "user", "content": prompt}],
    )
    if not result["success"]:
        error = result.get("error")
        if isinstance(error, Exception):
            raise error
        raise RuntimeError(f"extract_travel_state LLM 调用失败: {error}")
    response = result["data"]
    content = response.content.strip()

    # 去掉可能出现的 markdown 代码块
    content = re.sub(r"^```[a-z]*\n?", "", content)
    content = re.sub(r"\n?```$", "", content)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {}

    # 类型安全修正
    days = data.get("days")
    if days is not None:
        try:
            days = int(days)
        except (ValueError, TypeError):
            days = None

    interests = data.get("interests")
    if not isinstance(interests, list):
        interests = []

    return TravelState(
        destination=data.get("destination") or None,
        days=days,
        budget=data.get("budget") if data.get("budget") in BUDGET_OPTIONS else None,
        travel_group=data.get("travel_group") if data.get("travel_group") in TRAVEL_GROUP_OPTIONS else None,
        interests=[i for i in interests if i in INTEREST_OPTIONS],
    )


def state_to_prompt(state: TravelState) -> str:
    """将确认后的 TravelState 转为 Agent 可理解的规划请求"""
    interests_str = "、".join(state["interests"]) if state["interests"] else "综合游览"
    return (
        f"请为我规划旅行方案：\n"
        f"- 目的地：{state['destination']}\n"
        f"- 旅行天数：{state['days']} 天\n"
        f"- 预算：{state['budget']}\n"
        f"- 同行人：{state['travel_group']}\n"
        f"- 兴趣偏好：{interests_str}\n\n"
        f"请根据以上信息，调用工具查询天气、搜索景点、检索旅游攻略，生成详细的每日行程安排。"
    )
