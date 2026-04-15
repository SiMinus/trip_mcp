"""MCP Server 单元测试 — 验证工具注册和基本调用"""

import os
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

    @pytest.mark.asyncio
    async def test_get_current_weather_calls_api(self):
        mock_response = {
            "name": "Hangzhou",
            "main": {"temp": 22, "feels_like": 21, "humidity": 65},
            "weather": [{"description": "晴"}],
            "wind": {"speed": 3.5},
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = lambda: None

            from mcp_servers.weather_server import get_current_weather

            result = await get_current_weather("Hangzhou")
            assert "22" in result
            assert "晴" in result


class TestPOIServer:
    def test_server_has_tools(self):
        from mcp_servers.poi_server import mcp

        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "search_poi" in tool_names
        assert "get_poi_detail" in tool_names


class TestTransportServer:
    def test_server_has_tools(self):
        from mcp_servers.transport_server import mcp

        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "plan_walking_route" in tool_names
        assert "plan_driving_route" in tool_names
        assert "plan_transit_route" in tool_names
