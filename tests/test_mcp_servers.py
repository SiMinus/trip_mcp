"""MCP Server 单元测试 — 验证工具注册和基本调用"""

import asyncio
import os
import httpx
import pytest
from unittest.mock import AsyncMock, patch

# 设置测试环境变量
os.environ.setdefault("OPENWEATHER_API_KEY", "test_key")
os.environ.setdefault("AMAP_API_KEY", "test_key")


class TestWeatherServer:
    """测试天气 MCP Server 工具注册"""

    def test_server_has_tools(self):
        from mcp_servers.weather_server import mcp

        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "get_current_weather" in tool_names
        assert "get_weather_forecast" in tool_names

    def test_get_current_weather_calls_api(self):
        mock_response = {
            "current": {
                "temperature_2m": 22,
                "apparent_temperature": 21,
                "relative_humidity_2m": 65,
                "wind_speed_10m": 3.5,
                "weather_code": 0,
            }
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value.json = lambda: mock_response
            mock_get.return_value.raise_for_status = lambda: None

            from mcp_servers import weather_server

            with patch.object(weather_server, "_geocode", new_callable=AsyncMock) as mock_geocode:
                mock_geocode.return_value = (30.2741, 120.1551, "Hangzhou")
                result = asyncio.run(weather_server.get_current_weather("Hangzhou"))
            assert "22" in result
            assert "晴" in result

    def test_get_current_weather_uses_friendly_fallback(self):
        from mcp_servers import weather_server

        with patch.object(weather_server, "_geocode", new_callable=AsyncMock) as mock_geocode:
            mock_geocode.side_effect = TimeoutError("timeout")
            result = asyncio.run(weather_server.get_current_weather("Hangzhou"))
        assert "天气查询 请求超时" in result


class TestPOIServer:
    def test_server_has_tools(self):
        from mcp_servers.poi_server import mcp

        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "search_poi" in tool_names
        assert "get_poi_detail" in tool_names

    def test_search_poi_falls_back_to_baidu(self, monkeypatch):
        from mcp_servers import poi_server

        async def fail_amap(*args, **kwargs):
            raise RuntimeError("amap down")

        async def ok_baidu(keyword, city, category, page_size):
            return {
                "provider": "baidu",
                "city": city,
                "keyword": keyword,
                "category": category,
                "results": [{"name": "西湖", "location": "120.1,30.2"}],
            }

        async def fail_tencent(*args, **kwargs):
            raise RuntimeError("should not be called")

        monkeypatch.setattr(poi_server, "AMAP_API_KEY", "amap_key")
        monkeypatch.setattr(poi_server, "BAIDU_MAP_AK", "baidu_key")
        monkeypatch.setattr(poi_server, "TENCENT_MAP_KEY", "tencent_key")
        monkeypatch.setattr(poi_server, "_search_poi_amap", fail_amap)
        monkeypatch.setattr(poi_server, "_search_poi_baidu", ok_baidu)
        monkeypatch.setattr(poi_server, "_search_poi_tencent", fail_tencent)

        result = asyncio.run(poi_server.search_poi("西湖", "杭州"))
        assert '"provider": "baidu"' in result
        assert "西湖" in result

    def test_search_poi_uses_cache_fallback(self, monkeypatch):
        from mcp_servers import poi_server

        class FakeRedis:
            def __init__(self):
                self.store = {}

            def get(self, key):
                return self.store.get(key)

            def setex(self, key, ttl, value):
                self.store[key] = value

        cache = FakeRedis()
        cache_key = poi_server._poi_cache_key("西湖", "杭州", "", 5)
        cache.setex(
            cache_key,
            1800,
            '{"provider":"amap","city":"杭州","keyword":"西湖","category":"","results":[{"name":"西湖"}]}',
        )

        async def fail_provider(*args, **kwargs):
            raise TimeoutError("timeout")

        monkeypatch.setattr(poi_server, "AMAP_API_KEY", "amap_key")
        monkeypatch.setattr(poi_server, "BAIDU_MAP_AK", "baidu_key")
        monkeypatch.setattr(poi_server, "TENCENT_MAP_KEY", "tencent_key")
        monkeypatch.setattr(poi_server, "_get_redis_client", lambda: cache)
        monkeypatch.setattr(poi_server, "_search_poi_amap", fail_provider)
        monkeypatch.setattr(poi_server, "_search_poi_baidu", fail_provider)
        monkeypatch.setattr(poi_server, "_search_poi_tencent", fail_provider)

        result = asyncio.run(poi_server.search_poi("西湖", "杭州"))
        assert '"cache_hit": true' in result
        assert "最近一次可用缓存结果" in result
        assert "西湖" in result

    def test_request_json_retries_then_recovers(self):
        from mcp_servers import poi_server

        attempts = {"count": 0}

        class FakeResponse:
            def __init__(self, payload, status_code=200):
                self._payload = payload
                self.status_code = status_code
                self.request = httpx.Request("GET", "https://example.com")

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "server error",
                        request=self.request,
                        response=httpx.Response(self.status_code, request=self.request),
                    )

            def json(self):
                return self._payload

        async def fake_get(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return FakeResponse({}, status_code=503)
            return FakeResponse({"status": "1", "pois": [{"name": "西湖"}]})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = fake_get
            result = asyncio.run(
                poi_server._request_json("https://example.com", {"q": "西湖"})
            )

        assert attempts["count"] == 2
        assert result["status"] == "1"


class TestTransportServer:
    def test_server_has_tools(self):
        from mcp_servers.transport_server import mcp

        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "plan_walking_route" in tool_names
        assert "plan_driving_route" in tool_names
        assert "plan_transit_route" in tool_names

    def test_resolve_location_falls_back_to_tencent(self, monkeypatch):
        from mcp_servers import transport_server

        async def fail_amap(*args, **kwargs):
            raise RuntimeError("amap down")

        async def fail_baidu(*args, **kwargs):
            raise RuntimeError("baidu down")

        async def ok_tencent(location, city=""):
            return {
                "query": location,
                "name": "西湖",
                "address": "杭州西湖区",
                "location": "120.1,30.2",
                "source": "poi_search",
                "provider": "tencent",
            }

        monkeypatch.setattr(transport_server, "AMAP_API_KEY", "amap_key")
        monkeypatch.setattr(transport_server, "BAIDU_MAP_AK", "baidu_key")
        monkeypatch.setattr(transport_server, "TENCENT_MAP_KEY", "tencent_key")
        monkeypatch.setattr(transport_server, "_resolve_location_amap", fail_amap)
        monkeypatch.setattr(transport_server, "_resolve_location_baidu", fail_baidu)
        monkeypatch.setattr(transport_server, "_resolve_location_tencent", ok_tencent)

        result = asyncio.run(transport_server.resolve_location("西湖", "杭州"))
        assert "provider=腾讯地图" in result
        assert "location=120.1,30.2" in result

    def test_plan_walking_route_falls_back_to_baidu(self, monkeypatch):
        from mcp_servers import transport_server

        async def fake_resolve(location, city=""):
            return {
                "query": location,
                "name": location,
                "address": "",
                "location": "120.1,30.2" if location == "西湖" else "120.2,30.3",
                "source": "poi_search",
                "provider": "amap",
            }

        async def fail_amap(*args, **kwargs):
            raise RuntimeError("amap down")

        async def ok_baidu(*args, **kwargs):
            return "步行路线：西湖 -> 灵隐寺，距离 2.0公里，预计用时 30分钟。距离中等，可步行，但若赶时间可考虑打车或骑行。\n数据源: 百度地图"

        async def fail_tencent(*args, **kwargs):
            raise RuntimeError("should not be called")

        monkeypatch.setattr(transport_server, "AMAP_API_KEY", "amap_key")
        monkeypatch.setattr(transport_server, "BAIDU_MAP_AK", "baidu_key")
        monkeypatch.setattr(transport_server, "TENCENT_MAP_KEY", "tencent_key")
        monkeypatch.setattr(transport_server, "_resolve_location_record", fake_resolve)
        monkeypatch.setattr(transport_server, "_plan_walking_route_amap", fail_amap)
        monkeypatch.setattr(transport_server, "_plan_walking_route_baidu", ok_baidu)
        monkeypatch.setattr(transport_server, "_plan_walking_route_tencent", fail_tencent)

        result = asyncio.run(transport_server.plan_walking_route("西湖", "灵隐寺", "杭州"))
        assert "数据源: 百度地图" in result

    def test_plan_walking_route_uses_friendly_fallback(self, monkeypatch):
        from mcp_servers import transport_server

        async def fail_route(*args, **kwargs):
            raise TimeoutError("timeout")

        monkeypatch.setattr(transport_server, "_plan_route_with_fallback", fail_route)
        result = asyncio.run(transport_server.plan_walking_route("西湖", "灵隐寺", "杭州"))
        assert "步行路线规划 请求超时" in result

    def test_transport_request_json_retries_then_recovers(self):
        from mcp_servers import transport_server

        attempts = {"count": 0}

        class FakeResponse:
            def __init__(self, payload, status_code=200):
                self._payload = payload
                self.status_code = status_code
                self.request = httpx.Request("GET", "https://example.com")

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "server error",
                        request=self.request,
                        response=httpx.Response(self.status_code, request=self.request),
                    )

            def json(self):
                return self._payload

        async def fake_get(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return FakeResponse({}, status_code=502)
            return FakeResponse({"ok": True})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = fake_get
            result = asyncio.run(
                transport_server._request_json("https://example.com", {"q": "route"})
            )

        assert attempts["count"] == 2
        assert result["ok"] is True
