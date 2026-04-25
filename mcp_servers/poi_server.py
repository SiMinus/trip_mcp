"""POI 搜索 MCP Server — 高德优先，失败时自动切换百度/腾讯地图。"""

import json
import os

import httpx
import redis
from mcp.server.fastmcp import FastMCP

from mcp_servers.tool_fallbacks import render_tool_fallback

mcp = FastMCP("poi")

AMAP_API_KEY = os.environ.get("AMAP_API_KEY", "")
BAIDU_MAP_AK = os.environ.get("BAIDU_MAP_AK", "")
TENCENT_MAP_KEY = os.environ.get("TENCENT_MAP_KEY", "")
REDIS_URL = os.environ.get("REDIS_URL", "")
POI_CACHE_TTL_SECONDS = int(os.environ.get("POI_CACHE_TTL_SECONDS", "1800"))

AMAP_BASE = "https://restapi.amap.com/v3"
BAIDU_PLACE_URL = "https://api.map.baidu.com/place/v2/search"
TENCENT_PLACE_URL = "https://apis.map.qq.com/ws/place/v1/search"

_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is None and REDIS_URL:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _poi_cache_key(keyword: str, city: str, category: str, page_size: int) -> str:
    return f"poi:{city}:{keyword}:{category}:{page_size}"


def _load_cached_poi(cache_key: str) -> dict | None:
    client = _get_redis_client()
    if client is None:
        return None
    try:
        cached = client.get(cache_key)
        return json.loads(cached) if cached else None
    except Exception:
        return None


def _save_cached_poi(cache_key: str, payload: dict) -> None:
    client = _get_redis_client()
    if client is None:
        return
    try:
        client.setex(cache_key, POI_CACHE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
    except Exception:
        return


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


async def _search_poi_amap(
    keyword: str,
    city: str,
    category: str = "",
    page_size: int = 5,
) -> dict:
    data = await _request_json(
        f"{AMAP_BASE}/place/text",
        {
            "key": AMAP_API_KEY,
            "keywords": keyword,
            "city": city,
            "offset": page_size,
            "extensions": "all",
            **({"types": category} if category else {}),
        },
    )
    if data.get("status") != "1":
        raise RuntimeError(f'高德 API 返回错误: {data.get("info", "未知错误")}')

    pois = data.get("pois", [])
    if not pois:
        raise RuntimeError(f'未找到"{keyword}"相关结果')

    return {
        "provider": "amap",
        "city": city,
        "keyword": keyword,
        "category": category,
        "results": [
            {
                "name": item.get("name", ""),
                "address": item.get("address", ""),
                "location": item.get("location", ""),
                "rating": item.get("biz_ext", {}).get("rating", "-"),
                "tel": item.get("tel", ""),
                "type": item.get("type", ""),
                "poi_id": item.get("id", ""),
            }
            for item in pois
        ],
    }


async def _search_poi_baidu(
    keyword: str,
    city: str,
    category: str = "",
    page_size: int = 5,
) -> dict:
    query = f"{keyword} {category}".strip()
    data = await _request_json(
        BAIDU_PLACE_URL,
        {
            "query": query,
            "region": city,
            "scope": 2,
            "page_size": page_size,
            "output": "json",
            "ak": BAIDU_MAP_AK,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'百度地图返回错误: {data.get("message", "未知错误")}')

    pois = data.get("results", [])
    if not pois:
        raise RuntimeError(f'未找到"{keyword}"相关结果')

    results = []
    for item in pois:
        detail = item.get("detail_info", {})
        location = item.get("location", {})
        results.append(
            {
                "name": item.get("name", ""),
                "address": item.get("address", ""),
                "location": f'{location.get("lng", "")},{location.get("lat", "")}',
                "rating": detail.get("overall_rating", "-"),
                "tel": item.get("telephone", ""),
                "type": detail.get("type", detail.get("tag", "")),
                "poi_id": item.get("uid", ""),
            }
        )

    return {
        "provider": "baidu",
        "city": city,
        "keyword": keyword,
        "category": category,
        "results": results,
    }


async def _search_poi_tencent(
    keyword: str,
    city: str,
    category: str = "",
    page_size: int = 5,
) -> dict:
    query = f"{keyword} {category}".strip()
    data = await _request_json(
        TENCENT_PLACE_URL,
        {
            "keyword": query,
            "boundary": f"region({city},0)",
            "page_size": page_size,
            "key": TENCENT_MAP_KEY,
        },
    )
    if data.get("status") != 0:
        raise RuntimeError(f'腾讯地图返回错误: {data.get("message", "未知错误")}')

    pois = data.get("data", [])
    if not pois:
        raise RuntimeError(f'未找到"{keyword}"相关结果')

    results = []
    for item in pois:
        location = item.get("location", {})
        results.append(
            {
                "name": item.get("title", item.get("name", "")),
                "address": item.get("address", ""),
                "location": f'{location.get("lng", "")},{location.get("lat", "")}',
                "rating": item.get("rating", "-"),
                "tel": item.get("tel", ""),
                "type": item.get("category", ""),
                "poi_id": item.get("id", ""),
            }
        )

    return {
        "provider": "tencent",
        "city": city,
        "keyword": keyword,
        "category": category,
        "results": results,
    }


async def _search_poi_with_fallback(
    keyword: str,
    city: str,
    category: str = "",
    page_size: int = 5,
) -> dict:
    providers = [
        ("amap", AMAP_API_KEY, _search_poi_amap),
        ("baidu", BAIDU_MAP_AK, _search_poi_baidu),
        ("tencent", TENCENT_MAP_KEY, _search_poi_tencent),
    ]
    errors: list[str] = []

    for name, key, handler in providers:
        if not key:
            errors.append(f"{name}: key not configured")
            continue
        try:
            return await handler(keyword, city, category, page_size)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    raise RuntimeError(" | ".join(errors) if errors else "没有可用地图 provider")


@mcp.tool()
async def search_poi(
    keyword: str,
    city: str,
    category: str = "",
    page_size: int = 5,
) -> str:
    """在指定城市搜索 POI（景点/酒店/餐厅等），失败时自动切换备用地图 provider。"""
    cache_key = _poi_cache_key(keyword, city, category, page_size)
    try:
        payload = await _search_poi_with_fallback(keyword, city, category, page_size)
        _save_cached_poi(cache_key, payload)
    except Exception as exc:
        cached_payload = _load_cached_poi(cache_key)
        if cached_payload is not None:
            cached_payload = {
                **cached_payload,
                "cache_hit": True,
                "fallback_message": render_tool_fallback("POI 搜索", exc, cache_hit=True),
            }
            return json.dumps(cached_payload, ensure_ascii=False, indent=2)
        return render_tool_fallback("POI 搜索", exc)
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_poi_detail(poi_id: str) -> str:
    """通过 POI ID 获取详细信息（营业时间、评分、照片等）。当前明细查询仍使用高德。"""
    if not AMAP_API_KEY:
        return render_tool_fallback("POI 详情查询", RuntimeError("未配置高德地图 Key"))

    try:
        data = await _request_json(
            f"{AMAP_BASE}/place/detail",
            {"key": AMAP_API_KEY, "id": poi_id, "extensions": "all"},
        )
    except Exception as exc:
        return render_tool_fallback("POI 详情查询", exc)

    pois = data.get("pois", [])
    if not pois:
        return render_tool_fallback("POI 详情查询", ValueError("未找到该 POI"))
    item = pois[0]
    biz = item.get("biz_ext", {})
    return (
        f"{item['name']}\n"
        f"地址: {item.get('address', '')}\n"
        f"类型: {item.get('type', '')}\n"
        f"评分: {biz.get('rating', '-')}  人均: {biz.get('cost', '-')}元\n"
        f"营业时间: {biz.get('opentime', '-')}\n"
        f"电话: {item.get('tel', '-')}\n"
        f"数据源: 高德地图"
    )


if __name__ == "__main__":
    mcp.run()
