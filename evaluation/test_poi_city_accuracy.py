"""
POI 城市准确性测试 — 验证 search_poi 返回的结果是否真的在目标城市境内

测试方法：
1. 调用高德 /v3/place/text（与 poi_server.py 相同逻辑）搜索各城市 POI
2. 对每个有效坐标的结果，调用高德 /v3/geocode/regeo 反地理编码
3. 比对反地理编码返回的城市字段与目标城市，统计越界率

运行方式：
    conda run -n mcp python evaluation/test_poi_city_accuracy.py
    conda run -n mcp python evaluation/test_poi_city_accuracy.py --threshold 0.85
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

# 确保能 import agent.config（从项目根目录运行时自动可用）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent.config import settings

AMAP_BASE = "https://restapi.amap.com/v3"
DEFAULT_THRESHOLD = 0.90

# ── 测试用例：城市 × 关键词 ───────────────────────────────────────────
TEST_CASES: list[dict] = [
    {"city": "杭州", "keywords": ["博物馆", "景区", "公园"]},
    {"city": "上海", "keywords": ["博物馆", "景点", "公园"]},
    {"city": "成都", "keywords": ["景区", "博物馆"]},
    {"city": "北京", "keywords": ["景区", "博物馆"]},
    {"city": "西安", "keywords": ["景区", "博物馆"]},
]

# 直辖市的 regeo 结果里 city 字段为空，需要用 province 判断
MUNICIPALITIES = {"上海", "北京", "天津", "重庆"}

# 城市名 → 可接受的别名列表（用于 in 匹配）
CITY_ALIASES: dict[str, list[str]] = {
    "杭州": ["杭州"],
    "上海": ["上海"],
    "成都": ["成都"],
    "北京": ["北京"],
    "西安": ["西安"],
    "天津": ["天津"],
    "重庆": ["重庆"],
}


# ── 高德 API 封装 ─────────────────────────────────────────────────────

async def _search_pois(
    client: httpx.AsyncClient,
    city: str,
    keyword: str,
    page_size: int = 8,
) -> list[dict]:
    """高德文本搜索，返回 pois 列表"""
    resp = await client.get(
        f"{AMAP_BASE}/place/text",
        params={
            "key": settings.amap_api_key,
            "keywords": keyword,
            "city": city,
            "citylimit": "true",   # 严格限制在城市内——测试其实际效果
            "offset": page_size,
            "extensions": "base",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        raise RuntimeError(f'高德搜索失败: {data.get("info", "unknown")}')
    return data.get("pois", [])


async def _regeo(client: httpx.AsyncClient, location: str) -> dict:
    """高德反地理编码，返回 addressComponent（失败时返回空 dict）"""
    resp = await client.get(
        f"{AMAP_BASE}/geocode/regeo",
        params={
            "key": settings.amap_api_key,
            "location": location,
            "extensions": "base",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        return {}
    return data.get("regeocode", {}).get("addressComponent", {})


# ── 城市匹配判断 ──────────────────────────────────────────────────────

def _city_matches(target_city: str, addr_comp: dict) -> tuple[bool, str]:
    """
    返回 (是否匹配, 实际城市描述字符串)
    直辖市用 province 字段，其余用 city 字段。
    """
    aliases = CITY_ALIASES.get(target_city, [target_city])

    if target_city in MUNICIPALITIES:
        province = addr_comp.get("province", "")
        actual_desc = province or "未知"
        return any(a in province for a in aliases), actual_desc
    else:
        city_val = addr_comp.get("city", "")
        actual_desc = city_val or addr_comp.get("province", "未知")
        return any(a in city_val for a in aliases), actual_desc


# ── 主测试逻辑 ────────────────────────────────────────────────────────

async def run(threshold: float, verbose: bool) -> int:
    """返回 exit code：0=PASS, 1=FAIL"""
    if not settings.amap_api_key:
        print("❌ AMAP_API_KEY 未配置，请检查 .env 文件")
        return 1

    total_pois = 0
    total_in_city = 0
    outliers: list[dict] = []

    async with httpx.AsyncClient(timeout=10) as client:
        for case in TEST_CASES:
            target_city = case["city"]
            city_total = 0
            city_in = 0

            print(f"\n{'─'*52}")
            print(f"城市: {target_city}  关键词: {case['keywords']}")

            for keyword in case["keywords"]:
                try:
                    pois = await _search_pois(client, target_city, keyword)
                except Exception as e:
                    print(f"  ⚠️  [{keyword}] 搜索失败: {e}")
                    continue

                for poi in pois:
                    location: str = poi.get("location", "")
                    poi_name: str = poi.get("name", "")
                    poi_addr: str = poi.get("address", "") or ""

                    if not location or "," not in location:
                        continue  # 无坐标，跳过

                    city_total += 1
                    total_pois += 1

                    try:
                        addr_comp = await _regeo(client, location)
                    except Exception:
                        addr_comp = {}

                    in_city, actual_city = _city_matches(target_city, addr_comp)

                    if in_city:
                        city_in += 1
                        total_in_city += 1
                        if verbose:
                            print(f"  ✅ [{keyword}] {poi_name}")
                    else:
                        outliers.append(
                            {
                                "target_city": target_city,
                                "keyword": keyword,
                                "poi_name": poi_name,
                                "poi_address": poi_addr,
                                "actual_city": actual_city,
                            }
                        )
                        print(f"  ❌ [{keyword}] {poi_name}")
                        print(f"       实际城市: {actual_city}")
                        print(f"       地址: {poi_addr[:80]}")

            acc = city_in / city_total if city_total else 0.0
            print(f"  → {city_in}/{city_total} 在城市内 ({acc:.1%})")

    # ── 汇总 ──────────────────────────────────────────────────────────
    overall_acc = total_in_city / total_pois if total_pois else 0.0

    print(f"\n{'='*52}")
    print(f"测试 POI 总数 : {total_pois}")
    print(f"城市内 POI   : {total_in_city}")
    print(f"越界 POI     : {len(outliers)}")
    print(f"整体准确率   : {overall_acc:.1%}  (阈值 {threshold:.0%})")

    if outliers:
        print(f"\n【越界 POI 汇总】")
        for o in outliers:
            print(
                f"  [{o['target_city']}·{o['keyword']}] "
                f"{o['poi_name']} → {o['actual_city']}"
            )

    if overall_acc >= threshold:
        print(f"\n✅ PASS")
        return 0
    else:
        print(f"\n❌ FAIL — 建议检查高德 citylimit 参数或过滤 poi address 字段")
        return 1


# ── CLI 入口 ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="测试 search_poi 城市准确性")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"通过阈值（默认 {DEFAULT_THRESHOLD}）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出所有 POI 结果（包括正确的）",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(run(threshold=args.threshold, verbose=args.verbose))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
