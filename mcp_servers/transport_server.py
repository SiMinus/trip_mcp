"""交通路线规划 MCP Server — 高德优先，失败时自动切换百度/腾讯地图。"""

import os
import re

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.tool_fallbacks import render_tool_fallback

mcp = FastMCP("transport")

AMAP_API_KEY = os.environ.get("AMAP_API_KEY", "")
BAIDU_MAP_AK = os.environ.get("BAIDU_MAP_AK", "")
TENCENT_MAP_KEY = os.environ.get("TENCENT_MAP_KEY", "")

AMAP_DIRECTION_BASE = "https://restapi.amap.com/v3/direction"
AMAP_PLACE_SEARCH_URL = "https://restapi.amap.com/v3/place/text"
AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"

BAIDU_DIRECTION_BASE = "https://api.map.baidu.com/directionlite/v1"
BAIDU_PLACE_SEARCH_URL = "https://api.map.baidu.com/place/v2/search"
BAIDU_GEOCODE_URL = "https://api.map.baidu.com/geocoding/v3"

TENCENT_DIRECTION_BASE = "https://apis.map.qq.com/ws/direction/v1"
TENCENT_PLACE_SEARCH_URL = "https://apis.map.qq.com/ws/place/v1/search"
TENCENT_GEOCODE_URL = "https://apis.map.qq.com/ws/geocoder/v1"

COORD_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?\s*$")


def _fmt_duration(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    return f"{h}小时{m}分钟" if h else f"{m}分钟"


def _fmt_distance(meters: int) -> str:
    return f"{meters / 1000:.1f}公里" if meters >= 1000 else f"{meters}米"


def _provider_label(name: str) -> str:
    return {"amap": "高德地图", "baidu": "百度地图", "tencent": "腾讯地图"}[name]


def _split_lng_lat(location: str) -> tuple[str, str]:
    lng, lat = [part.strip() for part in location.split(",", 1)]
    return lng, lat


def _to_lat_lng(location: str) -> str:
    lng, lat = _split_lng_lat(location)
    return f"{lat},{lng}"


async def _request_json(url: str, params: dict, attempts: int = 2) -> dict:
    last_error = None
    for _ in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            last_error = exc
    raise RuntimeError(str(last_error) if last_error else "未知网络错误")


async def _run_with_fallback(providers: list[tuple[str, str, object]], *args):
    errors: list[str] = []
    for name, key, handler in providers:
        if not key:
            errors.append(f"{name}: key not configured")
            continue
        try:
            return name, await handler(*args)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError(" | ".join(errors) if errors else "没有可用地图 provider")


async def _resolve_location_amap(location: str, city: str = "") -> dict:
    raw = location.strip()
    place_params = {
        "key": AMAP_API_KEY,
        "keywords": raw,
        "offset": 1,
        "extensions": "all",
    }
    if city:
        place_params["city"] = city
    place_data = await _request_json(AMAP_PLACE_SEARCH_URL, place_params)
    pois = place_data.get("pois", [])
    if pois:
        item = pois[0]
        return {
            "query": location,
            "name": item.get("name", raw),
            "address": item.get("address", ""),
            "location": item.get("location", ""),
            "source": "poi_search",
            "provider": "amap",
        }

    geo_params = {"key": AMAP_API_KEY, "address": raw}
    if city:
        geo_params["city"] = city
    geo_data = await _request_json(AMAP_GEOCODE_URL, geo_params)
    geocodes = geo_data.get("geocodes", [])
    if geocodes:
        item = geocodes[0]
        return {
            "query": location,
            "name": item.get("formatted_address", raw),
            "address": item.get("formatted_address", ""),
            "location": item.get("location", ""),
            "source": "geocode",
            "provider": "amap",
        }
    raise ValueError(f"高德未能解析地点: {location}")


async def _resolve_location_baidu(location: str, city: str = "") -> dict:
    raw = location.strip()
    place_data = await _request_json(
        BAIDU_PLACE_SEARCH_URL,
        {
            "query": raw,
            "region": city or raw,
            "scope": 2,
            "page_size": 1,
            "output": "json",
            "ak": BAIDU_MAP_AK,
        },
    )
    if place_data.get("status") == 0 and place_data.get("results"):
        item = place_data["results"][0]
        location_obj = item.get("location", {})
        return {
            "query": location,
            "name": item.get("name", raw),
            "address": item.get("address", ""),
            "location": f'{location_obj.get("lng", "")},{location_obj.get("lat", "")}',
            "source": "poi_search",
            "provider": "baidu",
        }

    geo_data = await _request_json(
        BAIDU_GEOCODE_URL,
        {"address": raw, "city": city, "output": "json", "ak": BAIDU_MAP_AK},
    )
    if geo_data.get("status") == 0 and geo_data.get("result", {}).get("location"):
        location_obj = geo_data["result"]["location"]
        return {
            "query": location,
            "name": geo_data.get("result", {}).get("formatted_address", raw),
            "address": geo_data.get("result", {}).get("formatted_address", ""),
            "location": f'{location_obj.get("lng", "")},{location_obj.get("lat", "")}',
            "source": "geocode",
            "provider": "baidu",
        }
    raise ValueError(f"百度未能解析地点: {location}")


async def _resolve_location_tencent(location: str, city: str = "") -> dict:
    raw = location.strip()
    place_data = await _request_json(
        TENCENT_PLACE_SEARCH_URL,
        {
            "keyword": raw,
            "boundary": f"region({city},0)" if city else "region(全国,0)",
            "page_size": 1,
            "key": TENCENT_MAP_KEY,
        },
    )
    if place_data.get("status") == 0 and place_data.get("data"):
        item = place_data["data"][0]
        location_obj = item.get("location", {})
        return {
            "query": location,
            "name": item.get("title", raw),
            "address": item.get("address", ""),
            "location": f'{location_obj.get("lng", "")},{location_obj.get("lat", "")}',
            "source": "poi_search",
            "provider": "tencent",
        }

    geo_data = await _request_json(
        TENCENT_GEOCODE_URL,
        {"address": raw, "region": city, "key": TENCENT_MAP_KEY},
    )
    if geo_data.get("status") == 0 and geo_data.get("result", {}).get("location"):
        location_obj = geo_data["result"]["location"]
        return {
            "query": location,
            "name": geo_data.get("result", {}).get("title", raw),
            "address": geo_data.get("result", {}).get("address", ""),
            "location": f'{location_obj.get("lng", "")},{location_obj.get("lat", "")}',
            "source": "geocode",
            "provider": "tencent",
        }
    raise ValueError(f"腾讯未能解析地点: {location}")


async def _resolve_location_record(location: str, city: str = "") -> dict:
    raw = location.strip()
    if COORD_RE.match(raw):
        return {
            "query": location,
            "name": raw,
            "address": "",
            "location": raw.replace(" ", ""),
            "source": "coordinate",
            "provider": "input",
        }

    _, record = await _run_with_fallback(
        [
            ("amap", AMAP_API_KEY, _resolve_location_amap),
            ("baidu", BAIDU_MAP_AK, _resolve_location_baidu),
            ("tencent", TENCENT_MAP_KEY, _resolve_location_tencent),
        ],
        location,
        city,
    )
    return record


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


async def _plan_walking_route_amap(origin_record: dict, destination_record: dict, city: str = "") -> str:
    data = await _request_json(
        f"{AMAP_DIRECTION_BASE}/walking",
        {
            "key": AMAP_API_KEY,
            "origin": origin_record["location"],
            "destination": destination_record["location"],
        },
    )
    paths = data.get("route", {}).get("paths", [])
    if not paths:
        raise RuntimeError("高德未找到步行路线")
    path = paths[0]
    distance = int(path["distance"])
    duration = int(path["duration"])
    return (
        f"步行路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}。"
        f"{_walking_advice(distance)}\n数据源: 高德地图"
    )


async def _plan_walking_route_baidu(origin_record: dict, destination_record: dict, city: str = "") -> str:
    data = await _request_json(
        f"{BAIDU_DIRECTION_BASE}/walking",
        {
            "origin": _to_lat_lng(origin_record["location"]),
            "destination": _to_lat_lng(destination_record["location"]),
            "ak": BAIDU_MAP_AK,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'百度地图返回错误: {data.get("message", "未知错误")}')
    routes = data.get("result", {}).get("routes", [])
    if not routes:
        raise RuntimeError("百度未找到步行路线")
    route = routes[0]
    distance = int(route.get("distance", 0))
    duration = int(route.get("duration", 0))
    return (
        f"步行路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}。"
        f"{_walking_advice(distance)}\n数据源: 百度地图"
    )


async def _plan_walking_route_tencent(origin_record: dict, destination_record: dict, city: str = "") -> str:
    data = await _request_json(
        f"{TENCENT_DIRECTION_BASE}/walking",
        {
            "from": _to_lat_lng(origin_record["location"]),
            "to": _to_lat_lng(destination_record["location"]),
            "key": TENCENT_MAP_KEY,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'腾讯地图返回错误: {data.get("message", "未知错误")}')
    routes = data.get("result", {}).get("routes", [])
    if not routes:
        raise RuntimeError("腾讯未找到步行路线")
    route = routes[0]
    distance = int(route.get("distance", 0))
    duration = int(route.get("duration", 0))
    return (
        f"步行路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}。"
        f"{_walking_advice(distance)}\n数据源: 腾讯地图"
    )


async def _plan_driving_route_amap(origin_record: dict, destination_record: dict, city: str = "") -> str:
    data = await _request_json(
        f"{AMAP_DIRECTION_BASE}/driving",
        {
            "key": AMAP_API_KEY,
            "origin": origin_record["location"],
            "destination": destination_record["location"],
            "strategy": 2,
        },
    )
    paths = data.get("route", {}).get("paths", [])
    if not paths:
        raise RuntimeError("高德未找到驾车路线")
    path = paths[0]
    distance = int(path["distance"])
    duration = int(path["duration"])
    tolls = path.get("tolls", "0")
    return (
        f"驾车路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}，"
        f"过路费约 {tolls} 元。{_driving_advice(distance)}\n数据源: 高德地图"
    )


async def _plan_driving_route_baidu(origin_record: dict, destination_record: dict, city: str = "") -> str:
    data = await _request_json(
        f"{BAIDU_DIRECTION_BASE}/driving",
        {
            "origin": _to_lat_lng(origin_record["location"]),
            "destination": _to_lat_lng(destination_record["location"]),
            "ak": BAIDU_MAP_AK,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'百度地图返回错误: {data.get("message", "未知错误")}')
    routes = data.get("result", {}).get("routes", [])
    if not routes:
        raise RuntimeError("百度未找到驾车路线")
    route = routes[0]
    distance = int(route.get("distance", 0))
    duration = int(route.get("duration", 0))
    tolls = route.get("toll", route.get("tolls", "0"))
    return (
        f"驾车路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}，"
        f"过路费约 {tolls} 元。{_driving_advice(distance)}\n数据源: 百度地图"
    )


async def _plan_driving_route_tencent(origin_record: dict, destination_record: dict, city: str = "") -> str:
    data = await _request_json(
        f"{TENCENT_DIRECTION_BASE}/driving",
        {
            "from": _to_lat_lng(origin_record["location"]),
            "to": _to_lat_lng(destination_record["location"]),
            "key": TENCENT_MAP_KEY,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'腾讯地图返回错误: {data.get("message", "未知错误")}')
    routes = data.get("result", {}).get("routes", [])
    if not routes:
        raise RuntimeError("腾讯未找到驾车路线")
    route = routes[0]
    distance = int(route.get("distance", 0))
    duration = int(route.get("duration", 0))
    tolls = route.get("toll", route.get("tolls", "0"))
    return (
        f"驾车路线：{origin_record['name']} -> {destination_record['name']}，"
        f"距离 {_fmt_distance(distance)}，预计用时 {_fmt_duration(duration)}，"
        f"过路费约 {tolls} 元。{_driving_advice(distance)}\n数据源: 腾讯地图"
    )


async def _plan_transit_route_amap(origin_record: dict, destination_record: dict, city: str) -> str:
    data = await _request_json(
        f"{AMAP_DIRECTION_BASE}/transit/integrated",
        {
            "key": AMAP_API_KEY,
            "origin": origin_record["location"],
            "destination": destination_record["location"],
            "city": city,
            "strategy": 0,
        },
    )
    transits = data.get("route", {}).get("transits", [])
    if not transits:
        raise RuntimeError("高德未找到公交路线")

    transit = transits[0]
    distance = int(transit.get("distance", 0))
    cost = transit.get("cost", "-")
    segments = []
    for segment in transit.get("segments", []):
        bus = segment.get("bus", {}).get("buslines", [])
        if bus:
            segments.append(f"乘坐 {bus[0]['name']}")
        walking = segment.get("walking", {})
        if walking and int(walking.get("distance", 0)) > 100:
            segments.append(f"步行 {_fmt_distance(int(walking['distance']))}")

    route_desc = " → ".join(segments) if segments else "详情见高德地图"
    return (
        f"公交路线：{origin_record['name']} -> {destination_record['name']}，"
        f"预计 {_fmt_duration(int(transit['duration']))}，费用 {cost} 元。"
        f"{_transit_advice(distance)}\n路线：{route_desc}\n数据源: 高德地图"
    )


async def _plan_transit_route_tencent(origin_record: dict, destination_record: dict, city: str) -> str:
    data = await _request_json(
        f"{TENCENT_DIRECTION_BASE}/transit",
        {
            "from": _to_lat_lng(origin_record["location"]),
            "to": _to_lat_lng(destination_record["location"]),
            "region": city,
            "key": TENCENT_MAP_KEY,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'腾讯地图返回错误: {data.get("message", "未知错误")}')
    routes = data.get("result", {}).get("routes", [])
    if not routes:
        raise RuntimeError("腾讯未找到公交路线")
    route = routes[0]
    distance = int(route.get("distance", 0))
    duration = int(route.get("duration", 0))
    cost = route.get("price", route.get("fare", "-"))
    steps = route.get("steps", [])
    step_desc = []
    for step in steps[:4]:
        if isinstance(step, dict):
            title = step.get("vehicle") or step.get("instructions") or step.get("road_name")
            if title:
                step_desc.append(str(title))
    route_desc = " → ".join(step_desc) if step_desc else "详情见腾讯地图"
    return (
        f"公交路线：{origin_record['name']} -> {destination_record['name']}，"
        f"预计 {_fmt_duration(duration)}，费用 {cost} 元。"
        f"{_transit_advice(distance)}\n路线：{route_desc}\n数据源: 腾讯地图"
    )


async def _plan_route_with_fallback(mode: str, origin: str, destination: str, city: str = "") -> str:
    origin_record = await _resolve_location_record(origin, city)
    destination_record = await _resolve_location_record(destination, city)

    provider_handlers = {
        "walking": [
            ("amap", AMAP_API_KEY, _plan_walking_route_amap),
            ("baidu", BAIDU_MAP_AK, _plan_walking_route_baidu),
            ("tencent", TENCENT_MAP_KEY, _plan_walking_route_tencent),
        ],
        "driving": [
            ("amap", AMAP_API_KEY, _plan_driving_route_amap),
            ("baidu", BAIDU_MAP_AK, _plan_driving_route_baidu),
            ("tencent", TENCENT_MAP_KEY, _plan_driving_route_tencent),
        ],
        "transit": [
            ("amap", AMAP_API_KEY, _plan_transit_route_amap),
            ("tencent", TENCENT_MAP_KEY, _plan_transit_route_tencent),
        ],
    }

    _, result = await _run_with_fallback(
        provider_handlers[mode], origin_record, destination_record, city
    )
    return result


@mcp.tool()
async def resolve_location(location: str, city: str = "") -> str:
    """解析地点名/POI 名到结构化位置结果，失败时自动切换备用地图 provider。"""
    try:
        record = await _resolve_location_record(location, city)
        provider = _provider_label(record.get("provider", "amap")) if record.get("provider") != "input" else "用户输入"
        return (
            f"地点解析结果：name={record['name']} | address={record['address'] or '-'} | "
            f"location={record['location']} | source={record['source']} | provider={provider}"
        )
    except Exception as exc:
        return render_tool_fallback("地点解析", exc)


@mcp.tool()
async def plan_walking_route(origin: str, destination: str, city: str = "") -> str:
    """步行路线规划。origin/destination 可传地点名、POI 名或 "经度,纬度"，失败时自动切换备用 provider。"""
    try:
        return await _plan_route_with_fallback("walking", origin, destination, city)
    except Exception as exc:
        return render_tool_fallback("步行路线规划", exc)


@mcp.tool()
async def plan_driving_route(origin: str, destination: str, city: str = "") -> str:
    """驾车路线规划。origin/destination 可传地点名、POI 名或 "经度,纬度"，失败时自动切换备用 provider。"""
    try:
        return await _plan_route_with_fallback("driving", origin, destination, city)
    except Exception as exc:
        return render_tool_fallback("驾车路线规划", exc)


@mcp.tool()
async def plan_transit_route(origin: str, destination: str, city: str) -> str:
    """公交/地铁路线规划。origin/destination 可传地点名、POI 名或 "经度,纬度"，失败时自动切换备用 provider。"""
    try:
        return await _plan_route_with_fallback("transit", origin, destination, city)
    except Exception as exc:
        return render_tool_fallback("公交路线规划", exc)


if __name__ == "__main__":
    mcp.run()
