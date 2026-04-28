"""航班搜索 MCP Server — 生成携程真实搜索链接"""

import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("flight")

# 主要城市 → 机场三字码
CITY_AIRPORT: dict[str, str] = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "成都": "CTU", "杭州": "HGH", "西安": "XIY", "重庆": "CKG",
    "南京": "NKG", "武汉": "WUH", "厦门": "XMN", "青岛": "TAO",
    "三亚": "SYX", "昆明": "KMG", "大连": "DLC", "哈尔滨": "HRB",
    "长沙": "CSX", "天津": "TSN", "郑州": "CGO", "贵阳": "KWE",
    "济南": "TNA", "合肥": "HFE", "南昌": "KHN", "福州": "FOC",
    "乌鲁木齐": "URC", "西宁": "XNN", "拉萨": "LXA",
}


@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    date: str,
    passengers: int = 1,
) -> str:
    """为用户生成机票搜索链接（跳转携程实时结果）。

    origin: 出发城市（如"北京"）
    destination: 目的地城市（如"杭州"）
    date: 出发日期，格式 YYYY-MM-DD
    passengers: 乘客人数（默认 1）
    """
    origin_code = CITY_AIRPORT.get(origin)
    dest_code = CITY_AIRPORT.get(destination)

    if not origin_code:
        supported = "、".join(CITY_AIRPORT.keys())
        return f"暂不支持出发城市「{origin}」，目前支持：{supported}"
    if not dest_code:
        supported = "、".join(CITY_AIRPORT.keys())
        return f"暂不支持目的地城市「{destination}」，目前支持：{supported}"

    # 若年份早于今年（LLM 幻觉），自动替换为今年
    today = datetime.date.today()
    try:
        d = datetime.date.fromisoformat(date)
        if d.year < today.year:
            date = d.replace(year=today.year).isoformat()
    except ValueError:
        pass

    url = (
        f"https://flights.ctrip.com/online/list/oneway-"
        f"{origin_code.lower()}-{dest_code.lower()}"
        f"?depdate={date}&cabin=y&adult={passengers}"
    )

    return (
        f"已为您生成 **{origin} → {destination}** {date} {passengers}人 的机票搜索链接：\n\n"
        f"[✈️ 点击查看实时航班及价格（携程）]({url})\n\n"
        f"> 链接将直接展示当日所有可订航班与最新票价。"
    )


if __name__ == "__main__":
    mcp.run()



@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    date: str,
    passengers: int = 1,
) -> str:
    """搜索航班并比价。

    origin: 出发城市（如"北京"）
    destination: 目的地城市（如"杭州"）
    date: 出发日期，格式 YYYY-MM-DD
    passengers: 乘客人数（默认 1）

    返回 3 条航班 + 携程预订链接，价格从低到高排序。
    """
    origin_code = CITY_AIRPORT.get(origin)
    dest_code = CITY_AIRPORT.get(destination)

    if not origin_code:
        supported = "、".join(CITY_AIRPORT.keys())
        return f"暂不支持出发城市「{origin}」，目前支持：{supported}"
    if not dest_code:
        supported = "、".join(CITY_AIRPORT.keys())
        return f"暂不支持目的地城市「{destination}」，目前支持：{supported}"

    # 若年份早于今年（LLM 幻觉），自动替换为今年
    today = datetime.date.today()
    try:
        d = datetime.date.fromisoformat(date)
        if d.year < today.year:
            date = d.replace(year=today.year).isoformat()
    except ValueError:
        pass

    random.seed(f"{origin}{destination}{date}{passengers}")

    flights = []
    used_hours: set[int] = set()
    for _ in range(3):
        airline_name, code = random.choice(AIRLINES)
        flight_no = f"{code}{random.randint(1000, 9999)}"

        # 生成不重叠的出发时间
        dep_hour = random.randint(6, 21)
        while dep_hour in used_hours:
            dep_hour = (dep_hour + 1) % 24
        used_hours.add(dep_hour)
        dep_min = random.choice([0, 10, 20, 30, 40, 50])
        dep_time = f"{dep_hour:02d}:{dep_min:02d}"

        duration_min = random.randint(60, 240)
        arr_total = dep_hour * 60 + dep_min + duration_min
        arr_time = f"{(arr_total // 60) % 24:02d}:{arr_total % 60:02d}"

        price = random.randint(380, 1800) * passengers

        # 携程直搜深链
        booking_url = (
            f"https://flights.ctrip.com/online/list/oneway-"
            f"{origin_code.lower()}-{dest_code.lower()}"
            f"?depdate={date}&cabin=y&adult={passengers}&direct=true"
        )

        flights.append(dict(
            airline=airline_name,
            flight_no=flight_no,
            dep_time=dep_time,
            arr_time=arr_time,
            price=price,
            url=booking_url,
        ))

    flights.sort(key=lambda x: x["price"])

    lines = [f"✈️ **{origin} → {destination}**  {date}  {passengers}人\n"]
    for f in flights:
        lines.append(
            f"• {f['airline']} {f['flight_no']}  "
            f"{f['dep_time']} → {f['arr_time']}  "
            f"**¥{f['price']}**  "
            f"[点击预订]({f['url']})"
        )
    lines.append("\n> ⚠️ 以上为模拟数据，实际价格以携程平台为准。点击「预订」链接即可跳转完成支付。")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
