"""
三大指标计算逻辑
  M1 — 工具调用成功率
  M2 — 工具调用准确率
  M3 — 平均工具调用轮次（行程规划效率）
"""
from __future__ import annotations

from typing import Any

# ── 判断工具返回是否为错误 ─────────────────────────────────────────────
_ERROR_KEYWORDS = (
    "工具执行失败",
    "不存在",
    "请求失败",
    "超时",
    "timeout",
    "failed",
    "Exception",
    "error:",
    "Error:",
)


def _tool_result_is_error(output: str) -> bool:
    """只要输出中含有已知错误关键词，就判定为失败。"""
    low = output.lower()
    return any(kw.lower() in low for kw in _ERROR_KEYWORDS)


def _tool_in_category(
    tool_name: str,
    category: str,
    tool_categories: dict[str, list[str]],
) -> bool:
    """精确匹配工具名是否属于期望类别。"""
    return tool_name in tool_categories.get(category, [])


# ── Metric 1: 工具调用成功率 ──────────────────────────────────────────
def compute_tool_success_rate(
    results: list[dict[str, Any]],
) -> tuple[float, list[dict]]:
    """
    对每次 tool_call 事件，检查后续是否存在对应的 tool_result，
    且 tool_result 的输出不含错误关键词。

    返回：
      rate   — 成功比率 [0, 1]
      details — 每个测试用例的明细列表
    """
    total_calls = 0
    successful_calls = 0
    details: list[dict] = []

    for r in results:
        events: list[dict] = r["events"]

        # 以工具名为键索引最后一次 tool_result（并行调用同名工具时取最后一次）
        tool_results: dict[str, dict] = {}
        for e in events:
            if e.get("type") == "tool_result":
                tool_results[e["tool"]] = e

        tool_calls = [e for e in events if e.get("type") == "tool_call"]
        call_details: list[dict] = []
        case_success = 0

        for tc in tool_calls:
            tname = tc["tool"]
            tr = tool_results.get(tname)
            if tr is None:
                status = "no_result"          # 调用后无任何返回
            elif _tool_result_is_error(tr.get("output", "")):
                status = "error"              # 工具返回了错误信息
            else:
                status = "success"
                case_success += 1
            call_details.append({"tool": tname, "status": status})

        total_calls += len(tool_calls)
        successful_calls += case_success
        details.append(
            {
                "case_id": r["case_id"],
                "description": r.get("description", ""),
                "total_calls": len(tool_calls),
                "successful_calls": case_success,
                "call_details": call_details,
            }
        )

    rate = successful_calls / total_calls if total_calls > 0 else 0.0
    return rate, details


# ── Metric 2: 工具调用准确率 ──────────────────────────────────────────
def compute_tool_accuracy(
    results: list[dict[str, Any]],
    tool_categories: dict[str, list[str]],
) -> tuple[float, list[dict]]:
    """
    对每个有 expected_tool_category 的测试用例，检查实际调用的工具中
    是否至少有一个属于期望类别。

    返回：
      rate    — 准确比率 [0, 1]
      details — 每个测试用例的明细列表
    """
    total_cases = 0
    correct_cases = 0
    details: list[dict] = []

    for r in results:
        expected = r.get("expected_tool_category", "")
        if not expected:
            continue  # 没有期望类别的用例跳过

        total_cases += 1
        events: list[dict] = r["events"]
        tool_calls = [e for e in events if e.get("type") == "tool_call"]
        called_names = [e["tool"] for e in tool_calls]

        if not called_names:
            correct = False
            reason = "未调用任何工具"
        else:
            correct = any(
                _tool_in_category(t, expected, tool_categories)
                for t in called_names
            )
            reason = f"实际调用: {called_names}"

        if correct:
            correct_cases += 1

        details.append(
            {
                "case_id": r["case_id"],
                "description": r.get("description", ""),
                "expected_category": expected,
                "correct": correct,
                "called_tools": called_names,
                "reason": reason,
            }
        )

    rate = correct_cases / total_cases if total_cases > 0 else 0.0
    return rate, details


# ── Metric 3: 平均工具调用轮次 ────────────────────────────────────────
def compute_tool_rounds(
    results: list[dict[str, Any]],
) -> tuple[float, list[dict]]:
    """
    统计每次行程规划中 tool_call 事件的总数，作为"工具调用轮次"的
    度量（LLM 每发起一轮工具调用即计一次；并行调用的多个工具各计一次）。

    还按 langgraph_node 分组展示，便于分析是哪个子智能体驱动了更多调用。

    返回：
      avg_calls — 每次规划平均工具调用次数
      details   — 每个测试用例的明细列表
    """
    details: list[dict] = []

    for r in results:
        events: list[dict] = r["events"]
        tool_calls = [e for e in events if e.get("type") == "tool_call"]

        # 按所属节点分组，便于观察多智能体分工
        by_node: dict[str, list[str]] = {}
        for tc in tool_calls:
            node = tc.get("node") or "unknown"
            by_node.setdefault(node, []).append(tc["tool"])

        details.append(
            {
                "case_id": r["case_id"],
                "description": r.get("description", ""),
                "total_tool_calls": len(tool_calls),
                "tools_by_node": by_node,
                "tool_sequence": [tc["tool"] for tc in tool_calls],
            }
        )

    avg = (
        sum(d["total_tool_calls"] for d in details) / len(details)
        if details
        else 0.0
    )
    return avg, details
