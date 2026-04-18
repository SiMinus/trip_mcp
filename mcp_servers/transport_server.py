"""交通路线规划 MCP Server — 调用高德地图路线 API"""

import os
import re
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("transport")

API_KEY = os.environ.get("AMAP_API_KEY", "")
BASE = "https://restapi.amap.com/v3/direction"
PLACE_SEARCH_URL = "https://restapi.amap.com/v3/place/text"
GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
COORD_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?\s*$")


def _fmt_duration(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    return f"{h}小时{m}分钟" if h else f"{m}分钟"


def _fmt_distance(meters: int) -> str:
    return f"{meters / 1000:.1f}公里" if meters >= 1000 else f"{meters}米"


async def _resolve_location_record(location: str, city: str = "") -> dict:
    raw = location.strip()
    if COORD_RE.match(raw):
        return {
            "query": location,
            "name": raw,
            "address": "",
            "location": raw.replace(" ", ""),
            "source": "coordinate",
        }

    async with httpx.AsyncClient(timeout=10) as c:
        place_params = {
            "key": API_KEY,
            "keywords": raw,
            "offset": 1,
            "extensions": "all",
        }
        if city:
            place_params["city"] = city
        place_resp = await c.get(PLACE_SEARCH_URL, params=place_params)
        place_resp.raise_for_status()
        place_data = place_resp.json()
        pois = place_data.get("pois", [])
        if pois:
            p = pois[0]
            return {
                "query": location,
                "name": p.get("name", raw),
                "address": p.get("address", ""),
                "location": p.get("location", ""),
                "source": "poi_search",
            }

        geo_params = {"key": API_KEY, "address": raw}
        if city:
            geo_params["city"] = city
        geo_resp = await c.get(GEOCODE_URL, params=geo_params)
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        geocodes = geo_data.get("geocodes", [])
        if geocodes:
            g = geocodes[0]
            return {
                "query": location,
                "name": g.get("formatted_address", raw),
                "address": g.get("formatted_address", ""),
                "location": g.get("location", ""),
                "source": "geocode",
            }

    raise ValueError(f"未能解析地点: {location}")


def _walking_advice(distance_m: int) -> str:
    if distance_m <= 1500:
        return "距离较短，步行体验较好，通常无需额外换乘。"
    if distance_m <= 3000:
        return "距离中等，可步行，但若赶时间可考虑打车或骑行。"
    return "距离偏长，不建议全程步行，优先考虑公交/地铁或打车。"


def _driving_advice(distance_m: int) -> str:
    if distance_m <= 1500:
        return "距离较短，若处于景区核心区，步行通常比驾车更省心。"
    if distance_m <= 8000:
        return "中短距离出行，打车或自驾通常效率较高。"
    return "距离较长，适合驾车；如遇高峰期，建议同时比较公共交通方案。"


def _transit_advice(distance_m: int) -> str:
    if distance_m <= 3000:
        return "距离不远，若景区步行环境友好，可优先步行。"
    if distance_m <= 12000:
        return "该距离通常适合地铁/公交，兼顾成本与稳定性。"
    return "跨区距离较长，公共交通更稳妥，赶时间可同时比较打车方案。"


@mcp.tool()
async def resolve_location(location: str, city: str = "") -> str:
    """解析地点名/POI 名到结构化位置结果，返回 name/address/location/source。"""
    record = await _resolve_location_record(location, city)
    return (
        f"地点解析结果：name={record['name']} | address={record['address'] or '-'} | "
        f"location={record['location']} | source={record['source']}"
    )


@mcp.tool()
async def plan_walking_route(origin: str, destination: str, city: str = "") -> str:
    """步行路线规划。origin/destination 可传地点名、POI 名或 "经度,纬度"，city 可选。"""
    origin_record = await _resolve_location_record(origin, city)
    destination_record = await _resolve_location_record(destination, city)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/walking",
            params={
                "key": API_KEY,
                "origin": origin_record["location"],
                "destination": destination_record["location"],
            },
        )
        r.raise_for_status()
        data = r.json()

    paths = data.get("route", {}).get("paths", [])
    if not paths:
        return "未找到步行路线"
    p = paths[0]
    distance = int(p["distance"])
    duration = int(p["duration"])
    return (
        f"步行路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}。"
        f"{_walking_advice(distance)}"
    )


@mcp.tool()
async def plan_driving_route(origin: str, destination: str, city: str = "") -> str:
    """驾车路线规划。origin/destination 可传地点名、POI 名或 "经度,纬度"，city 可选。"""
    origin_record = await _resolve_location_record(origin, city)
    destination_record = await _resolve_location_record(destination, city)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/driving",
            params={
                "key": API_KEY,
                "origin": origin_record["location"],
                "destination": destination_record["location"],
                "strategy": 2,  # 距离最短
            },
        )
        r.raise_for_status()
        data = r.json()

    paths = data.get("route", {}).get("paths", [])
    if not paths:
        return "未找到驾车路线"
    p = paths[0]
    distance = int(p["distance"])
    duration = int(p["duration"])
    tolls = p.get("tolls", "0")
    return (
        f"驾车路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}，"
        f"过路费约 {tolls} 元。{_driving_advice(distance)}"
    )


@mcp.tool()
async def plan_transit_route(origin: str, destination: str, city: str) -> str:
    """公交/地铁路线规划。origin/destination 可传地点名、POI 名或 "经度,纬度"，city: 城市名。"""
    origin_record = await _resolve_location_record(origin, city)
    destination_record = await _resolve_location_record(destination, city)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BASE}/transit/integrated",
            params={
                "key": API_KEY,
                "origin": origin_record["location"],
                "destination": destination_record["location"],
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
    distance = int(t.get("distance", 0))
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
        f"公交路线：{origin_record['name']} -> {destination_record['name']}，"
        f"预计 {_fmt_duration(int(t['duration']))}，费用 {cost} 元。"
        f"{_transit_advice(distance)}\n路线：{route_desc}"
    )


if __name__ == "__main__":
    mcp.run()
