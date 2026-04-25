"""工具层统一兜底：错误分类 + 用户友好文案。"""

from __future__ import annotations

import httpx


def classify_tool_error(error: Exception) -> str:
    message = str(error).lower()

    if isinstance(error, TimeoutError) or "timeout" in message:
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code in (401, 403):
            return "auth"
        if code == 404:
            return "not_found"
        if code == 429:
            return "rate_limit"
        if code >= 500:
            return "upstream"
        return "bad_request"
    if isinstance(error, httpx.RequestError):
        return "network"
    if "not found" in message or "未找到" in message:
        return "not_found"
    if "key not configured" in message or "未配置" in message:
        return "config"
    if "429" in message or "rate limit" in message:
        return "rate_limit"
    return "unknown"


def render_tool_fallback(tool_name: str, error: Exception, cache_hit: bool = False) -> str:
    category = classify_tool_error(error)
    cache_suffix = " 已为你切换到最近一次可用缓存结果。" if cache_hit else ""

    templates = {
        "timeout": f"{tool_name} 请求超时，实时服务响应较慢，请稍后重试。{cache_suffix}".strip(),
        "network": f"{tool_name} 暂时无法连接上游服务，请检查网络或稍后再试。{cache_suffix}".strip(),
        "auth": f"{tool_name} 当前不可用，服务鉴权配置需要检查。{cache_suffix}".strip(),
        "not_found": f"{tool_name} 暂时没有查到匹配结果，请换个关键词或补充城市信息。{cache_suffix}".strip(),
        "rate_limit": f"{tool_name} 当前访问较多，已触发限流，请稍后再试。{cache_suffix}".strip(),
        "upstream": f"{tool_name} 的上游服务暂时不稳定，请稍后再试。{cache_suffix}".strip(),
        "bad_request": f"{tool_name} 请求参数不符合接口要求，请调整后重试。{cache_suffix}".strip(),
        "config": f"{tool_name} 暂时不可用，相关服务配置尚未完成。{cache_suffix}".strip(),
        "unknown": f"{tool_name} 暂时不可用，请稍后重试。{cache_suffix}".strip(),
    }
    return templates.get(category, templates["unknown"])
