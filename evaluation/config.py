"""
评估配置 — 测试用例 + 工具类别映射 + 上线阈值
"""

# ── 后端连接 ──────────────────────────────────────────────────────────
BASE_URL = "http://localhost:8000"
REQUEST_TIMEOUT = 120  # 每个请求最长等待秒数

# ── 上线阈值 ──────────────────────────────────────────────────────────
M1_THRESHOLD = 0.95   # 工具调用成功率
M2_THRESHOLD = 0.90   # 工具调用准确率

# ── 工具类别映射（MCP server 注册的真实函数名） ───────────────────────
TOOL_CATEGORIES: dict[str, list[str]] = {
    "weather": [
        "get_current_weather",
        "get_weather_forecast",
    ],
    "poi": [
        "search_poi",
        "get_poi_detail",
    ],
    "transport": [
        "plan_walking_route",
        "plan_driving_route",
        "plan_transit_route",
    ],
    "knowledge": [
        "search_knowledge",
    ],
    "flight": [
        "search_flights",
    ],
}

# ── M1 / M2 测试用例：工具调用成功率 + 准确率（各 10 条） ─────────────
# 覆盖 5 类工具，每类 2 条，确保分布均衡
ACCURACY_CASES: list[dict] = [
    {
        "id": 1,
        "description": "天气查询 — 北京实时",
        "message": "北京明天天气怎么样？",
        "expected_tool_category": "weather",
        "travel_state": None,
    },
    {
        "id": 2,
        "description": "天气查询 — 三亚预报",
        "message": "三亚未来三天的天气预报是什么？",
        "expected_tool_category": "weather",
        "travel_state": None,
    },
    {
        "id": 3,
        "description": "POI 搜索 — 餐厅",
        "message": "西湖附近有哪些特色餐厅？",
        "expected_tool_category": "poi",
        "travel_state": None,
    },
    {
        "id": 4,
        "description": "POI 搜索 — 景点",
        "message": "成都锦里附近有什么值得去的景点？",
        "expected_tool_category": "poi",
        "travel_state": None,
    },
    {
        "id": 5,
        "description": "路线规划 — 步行",
        "message": "从故宫步行到天安门广场需要多少时间？",
        "expected_tool_category": "transport",
        "travel_state": None,
    },
    {
        "id": 6,
        "description": "路线规划 — 公交",
        "message": "杭州西湖到灵隐寺怎么坐公交？需要多久？",
        "expected_tool_category": "transport",
        "travel_state": None,
    },
    {
        "id": 7,
        "description": "航班查询 — 沪京",
        "message": "上海到北京后天有哪些可以预订的航班？",
        "expected_tool_category": "flight",
        "travel_state": None,
    },
    {
        "id": 8,
        "description": "航班查询 — 粤闽",
        "message": "广州到厦门最近有哪些航班可以预订？",
        "expected_tool_category": "flight",
        "travel_state": None,
    },
    {
        "id": 9,
        "description": "知识查询 — 文化历史",
        "message": "九寨沟的历史和文化背景是什么？",
        "expected_tool_category": "knowledge",
        "travel_state": None,
    },
    {
        "id": 10,
        "description": "知识查询 — 民俗传说",
        "message": "丽江古城有什么著名的纳西族民俗和传说？",
        "expected_tool_category": "knowledge",
        "travel_state": None,
    },
]

# ── M3 测试用例：行程规划效率（10 次完整规划） ────────────────────────
# 覆盖不同城市 / 天数 / 同行人，触发多智能体 Searcher → WK → Planner 完整链路
PLANNING_CASES: list[dict] = [
    {
        "id": 1,
        "description": "北京 3 日 — 情侣历史文化",
        "message": "帮我规划北京 3 天的旅游行程",
        "travel_state": {
            "destination": "北京",
            "days": 3,
            "budget": "中等",
            "travel_group": "情侣",
            "interests": ["历史文化", "美食"],
        },
    },
    {
        "id": 2,
        "description": "上海 2 日 — 家庭亲子",
        "message": "帮我规划上海 2 天的旅游行程",
        "travel_state": {
            "destination": "上海",
            "days": 2,
            "budget": "较高",
            "travel_group": "家庭亲子",
            "interests": ["现代都市", "美食"],
        },
    },
    {
        "id": 3,
        "description": "成都 4 日 — 独自美食探索",
        "message": "帮我规划成都 4 天的自助游行程",
        "travel_state": {
            "destination": "成都",
            "days": 4,
            "budget": "经济实惠",
            "travel_group": "独自旅行",
            "interests": ["美食", "自然风光"],
        },
    },
    {
        "id": 4,
        "description": "杭州 2 日 — 情侣山水",
        "message": "杭州 2 天行程怎么安排比较合理？",
        "travel_state": {
            "destination": "杭州",
            "days": 2,
            "budget": "中等",
            "travel_group": "情侣",
            "interests": ["自然风光", "历史文化"],
        },
    },
    {
        "id": 5,
        "description": "三亚 5 日 — 家庭海滩度假",
        "message": "帮我安排三亚 5 天的度假行程",
        "travel_state": {
            "destination": "三亚",
            "days": 5,
            "budget": "较高",
            "travel_group": "家庭亲子",
            "interests": ["海滩度假", "水上运动"],
        },
    },
    {
        "id": 6,
        "description": "西安 3 日 — 朋友历史美食",
        "message": "西安 3 天历史文化之旅怎么规划？",
        "travel_state": {
            "destination": "西安",
            "days": 3,
            "budget": "中等",
            "travel_group": "朋友结伴",
            "interests": ["历史文化", "美食"],
        },
    },
    {
        "id": 7,
        "description": "丽江 3 日 — 情侣民族风情",
        "message": "丽江 3 天旅游行程推荐",
        "travel_state": {
            "destination": "丽江",
            "days": 3,
            "budget": "中等",
            "travel_group": "情侣",
            "interests": ["自然风光", "民族文化"],
        },
    },
    {
        "id": 8,
        "description": "厦门 3 日 — 朋友经济游",
        "message": "帮我规划厦门 3 天旅游行程",
        "travel_state": {
            "destination": "厦门",
            "days": 3,
            "budget": "经济实惠",
            "travel_group": "朋友结伴",
            "interests": ["美食", "历史文化"],
        },
    },
    {
        "id": 9,
        "description": "桂林 4 日 — 家庭自然摄影",
        "message": "桂林漓江 4 天游怎么安排最好？",
        "travel_state": {
            "destination": "桂林",
            "days": 4,
            "budget": "中等",
            "travel_group": "家庭亲子",
            "interests": ["自然风光", "摄影"],
        },
    },
    {
        "id": 10,
        "description": "九寨沟 4 日 — 朋友徒步摄影",
        "message": "九寨沟 4 天自然游行程规划",
        "travel_state": {
            "destination": "九寨沟",
            "days": 4,
            "budget": "较高",
            "travel_group": "朋友结伴",
            "interests": ["自然风光", "摄影", "健行"],
        },
    },
]
