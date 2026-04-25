"""天气查询 MCP Server — 调用 Open-Meteo（免费，无需 API Key）"""

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.tool_fallbacks import render_tool_fallback

mcp = FastMCP("weather")

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

WMO_DESC = {
    0: "晴", 1: "基本晴朗", 2: "局部多云", 3: "阴天",
    45: "雾", 48: "霜雾",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "小阵雨", 81: "中阵雨", 82: "大阵雨",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}


async def _geocode(city: str) -> tuple[float, float, str]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(GEO_URL, params={"name": city, "count": 1, "language": "zh"})
        r.raise_for_status()
        results = r.json().get("results", [])
    if not results:
        raise ValueError(f"未找到城市: {city}")
    loc = results[0]
    return loc["latitude"], loc["longitude"], loc.get("name", city)


@mcp.tool()
async def get_current_weather(city: str) -> str:
    """获取指定城市的当前天气（温度、体感、湿度、风速、天气描述）"""
    try:
        lat, lon, name = await _geocode(city)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                WEATHER_URL,
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
                    "wind_speed_unit": "ms", "timezone": "Asia/Shanghai",
                },
            )
            r.raise_for_status()
            cur = r.json()["current"]
        desc = WMO_DESC.get(cur["weather_code"], f"天气码{cur['weather_code']}")
        return (
            f"{name} 当前天气：{desc}，"
            f"温度 {cur['temperature_2m']}°C（体感 {cur['apparent_temperature']}°C），"
            f"湿度 {cur['relative_humidity_2m']}%，风速 {cur['wind_speed_10m']} m/s"
        )
    except Exception as exc:
        return render_tool_fallback("天气查询", exc)


@mcp.tool()
async def get_weather_forecast(city: str, days: int = 3) -> str:
    """获取城市未来 N 天天气预报（最高/最低温、天气概况），days 最大 7"""
    try:
        days = min(days, 7)
        lat, lon, name = await _geocode(city)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                WEATHER_URL,
                params={
                    "latitude": lat, "longitude": lon,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": days, "timezone": "Asia/Shanghai",
                },
            )
            r.raise_for_status()
            daily = r.json()["daily"]
        lines = []
        for i in range(days):
            desc = WMO_DESC.get(daily["weather_code"][i], "-")
            rain = daily["precipitation_probability_max"][i]
            lines.append(
                f"  {daily['time'][i]}: {desc}，"
                f"{daily['temperature_2m_min'][i]}~{daily['temperature_2m_max'][i]}°C，"
                f"降水概率 {rain}%"
            )
        return f"{name} 未来 {days} 天预报：\n" + "\n".join(lines)
    except Exception as exc:
        return render_tool_fallback("天气预报", exc)


if __name__ == "__main__":
    mcp.run()
    mcp.run()
