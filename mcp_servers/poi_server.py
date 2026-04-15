"""POI 搜索 MCP Server — 调用高德地图真实 API"""

import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("poi")

API_KEY = os.environ.get("AMAP_API_KEY", "")
BASE = "https://restapi.amap.com/v3"


@mcp.tool()
async def search_poi(
    keyword: str,
    city: str,
    category: str = "",
    page_size: int = 5,
) -> str:
    """在指定城市搜索 POI（景点/酒店/餐厅等）。
    keyword: 搜索关键词，如"西湖""火锅"
    city: 城市名
    category: 可选分类，如 "风景名胜""餐饮""酒店"
    """
    params: dict = {
        "key": API_KEY,
        "keywords": keyword,
        "city": city,
        "offset": page_size,
        "extensions": "all",
    }
    if category:
        params["types"] = category

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BASE}/place/text", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f'POI 搜索失败: {e}'

    if data.get("status") != "1":
        return f'高德 API 返回错误: {data.get("info", "未知错误")}'

    pois = data.get("pois", [])
    if not pois:
        return f'未找到"{keyword}"相关结果'

    lines = []
    for p in pois:
        addr = p.get("address", "无")
        tel = p.get("tel", "无")
        rating = p.get("biz_ext", {}).get("rating", "-")
        lines.append(
            f"• {p['name']}  地址: {addr}  评分: {rating}  电话: {tel}  "
            f"坐标: {p['location']}"
        )
    return f'在{city}搜索"{keyword}"的结果：\n' + "\n".join(lines)


@mcp.tool()
async def get_poi_detail(poi_id: str) -> str:
    """通过 POI ID 获取详细信息（营业时间、评分、照片等）"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/place/detail",
            params={"key": API_KEY, "id": poi_id, "extensions": "all"},
        )
        r.raise_for_status()
        data = r.json()

    pois = data.get("pois", [])
    if not pois:
        return "未找到该 POI"
    p = pois[0]
    biz = p.get("biz_ext", {})
    return (
        f"{p['name']}\n"
        f"地址: {p.get('address','')}\n"
        f"类型: {p.get('type','')}\n"
        f"评分: {biz.get('rating','-')}  人均: {biz.get('cost','-')}元\n"
        f"营业时间: {biz.get('opentime', '-')}\n"
        f"电话: {p.get('tel','-')}"
    )


if __name__ == "__main__":
    mcp.run()
