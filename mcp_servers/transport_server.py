"""交通路线规划 MCP Server — 调用高德地图路线 API"""

import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("transport")

API_KEY = os.environ.get("AMAP_API_KEY", "")
BASE = "https://restapi.amap.com/v3/direction"


def _fmt_duration(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    return f"{h}小时{m}分钟" if h else f"{m}分钟"


def _fmt_distance(meters: int) -> str:
    return f"{meters / 1000:.1f}公里" if meters >= 1000 else f"{meters}米"


@mcp.tool()
async def plan_walking_route(origin: str, destination: str) -> str:
    """步行路线规划。origin/destination 格式: "经度,纬度"（从 POI 搜索结果的坐标获取）"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/walking",
            params={"key": API_KEY, "origin": origin, "destination": destination},
        )
        r.raise_for_status()
        data = r.json()

    paths = data.get("route", {}).get("paths", [])
    if not paths:
        return "未找到步行路线"
    p = paths[0]
    return (
        f"步行路线：距离 {_fmt_distance(int(p['distance']))}，"
        f"预计用时 {_fmt_duration(int(p['duration']))}"
    )


@mcp.tool()
async def plan_driving_route(origin: str, destination: str) -> str:
    """驾车路线规划。origin/destination 格式: "经度,纬度" """
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/driving",
            params={
                "key": API_KEY,
                "origin": origin,
                "destination": destination,
                "strategy": 2,  # 距离最短
            },
        )
        r.raise_for_status()
        data = r.json()

    paths = data.get("route", {}).get("paths", [])
    if not paths:
        return "未找到驾车路线"
    p = paths[0]
    tolls = p.get("tolls", "0")
    return (
        f"驾车路线：距离 {_fmt_distance(int(p['distance']))}，"
        f"预计用时 {_fmt_duration(int(p['duration']))}，"
        f"过路费约 {tolls} 元"
    )


@mcp.tool()
async def plan_transit_route(origin: str, destination: str, city: str) -> str:
    """公交/地铁路线规划。origin/destination 格式: "经度,纬度"，city: 城市名"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/transit/integrated",
            params={
                "key": API_KEY,
                "origin": origin,
                "destination": destination,
                "city": city,
                "strategy": 0,  # 最快
            },
        )
        r.raise_for_status()
        data = r.json()

    transits = data.get("route", {}).get("transits", [])
    if not transits:
        return "未找到公交路线"

    t = transits[0]
    cost = t.get("cost", "-")
    segments = []
    for seg in t.get("segments", []):
        bus = seg.get("bus", {}).get("buslines", [])
        if bus:
            segments.append(f"乘坐 {bus[0]['name']}")
        walking = seg.get("walking", {})
        if walking and int(walking.get("distance", 0)) > 100:
            segments.append(f"步行 {_fmt_distance(int(walking['distance']))}")

    route_desc = " → ".join(segments) if segments else "详情见高德地图"
    return (
        f"公交路线：预计 {_fmt_duration(int(t['duration']))}，费用 {cost} 元\n"
        f"路线：{route_desc}"
    )


if __name__ == "__main__":
    mcp.run()
