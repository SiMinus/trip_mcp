#!/usr/bin/env python3
"""
智慧旅游 Agent — 全面上线评估
==============================
这不是简单测试，这是判断能否上线的全面评估。

评估三大指标：
  M1  工具调用成功率     目标 ≥ 95%   (每次工具调用是否真正返回有效结果)
  M2  工具调用准确率     目标 ≥ 90%   (天气问题→天气工具，路线问题→路线工具)
  M3  平均工具调用轮次   行程规划效率参考（无阈值，用于优化前后对比）

测试规模：M1/M2 共 10 个用例（5 类工具各 2 条），M3 共 10 个完整规划用例
全部用例共 20 个（M1/M2 共用同一批），合计最多 30 次 API 调用。

用法：
  # 全量评估（必须先启动后端）
  conda run -n mcp python evaluation/run_eval.py

  # 只评测 M1 + M2
  conda run -n mcp python evaluation/run_eval.py --only m1 m2

  # 只评测 M3（行程规划效率）
  conda run -n mcp python evaluation/run_eval.py --only m3

  # 打印工具返回详情
  conda run -n mcp python evaluation/run_eval.py --verbose

  # 自定义后端地址
  conda run -n mcp python evaluation/run_eval.py --base-url http://localhost:8001

退出码：
  0 — M1 & M2 均达标（或未被评测）
  1 — 存在未达标指标 / 后端不可用
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# ── 确保项目根目录在 sys.path ─────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from evaluation.config import (  # noqa: E402
    ACCURACY_CASES,
    M1_THRESHOLD,
    M2_THRESHOLD,
    PLANNING_CASES,
    TOOL_CATEGORIES,
)
from evaluation.metrics import (  # noqa: E402
    compute_tool_accuracy,
    compute_tool_rounds,
    compute_tool_success_rate,
)
from evaluation.sse_client import chat_sse  # noqa: E402


# ── 工具函数 ──────────────────────────────────────────────────────────
def _bar(rate: float, width: int = 30) -> str:
    """ASCII 进度条"""
    filled = int(rate * width)
    return "█" * filled + "░" * (width - filled)


def _sep(char: str = "=", width: int = 64) -> str:
    return char * width


# ── 运行 M1 / M2 用例（工具成功率 + 准确率） ─────────────────────────
async def run_accuracy_cases(base_url: str, verbose: bool) -> list[dict]:
    """串行跑 10 个工具准确率 / 成功率用例，实时打印进度。"""
    print(f"\n{_sep()}")
    print("  指标 M1 / M2 — 工具调用成功率 & 准确率  [ 10 个用例 ]")
    print(_sep())

    results: list[dict] = []
    for case in ACCURACY_CASES:
        cid: int = case["id"]
        desc: str = case["description"]
        expected: str = case["expected_tool_category"]

        print(f"\n  [{cid:02d}/10] {desc}  (期望工具类别: {expected})")
        print(f"         问: {case['message']}")

        t0 = time.time()
        events = await chat_sse(
            message=case["message"],
            travel_state=case.get("travel_state"),
            base_url=base_url,
        )
        elapsed = time.time() - t0

        err_event = next((e for e in events if e.get("type") == "error"), None)
        tool_calls = [e for e in events if e.get("type") == "tool_call"]

        if err_event:
            print(f"         ❌ 请求失败: {err_event.get('error')}")
        elif not tool_calls:
            print(f"         ⚠️  未触发任何工具调用  ({elapsed:.1f}s)")
        else:
            called = [e["tool"] for e in tool_calls]
            print(f"         ✅ 工具调用: {called}  ({elapsed:.1f}s)")

        if verbose:
            for e in events:
                if e.get("type") == "tool_result":
                    snippet = e.get("output", "")[:160].replace("\n", " ")
                    print(f"            └─ [{e['tool']}] {snippet}…")

        results.append(
            {
                "case_id": cid,
                "description": desc,
                "expected_tool_category": expected,
                "events": events,
            }
        )

    return results


# ── 运行 M3 用例（行程规划效率） ─────────────────────────────────────
async def run_planning_cases(base_url: str, verbose: bool) -> list[dict]:
    """串行跑 10 个完整行程规划用例，触发多智能体完整链路。"""
    print(f"\n{_sep()}")
    print("  指标 M3 — 平均工具调用轮次 / 行程规划效率  [ 10 个用例 ]")
    print(_sep())

    results: list[dict] = []
    for case in PLANNING_CASES:
        cid: int = case["id"]
        desc: str = case["description"]

        print(f"\n  [{cid:02d}/10] {desc}")
        print(f"         问: {case['message']}")

        t0 = time.time()
        events = await chat_sse(
            message=case["message"],
            travel_state=case.get("travel_state"),
            base_url=base_url,
        )
        elapsed = time.time() - t0

        err_event = next((e for e in events if e.get("type") == "error"), None)
        tool_calls = [e for e in events if e.get("type") == "tool_call"]

        if err_event:
            print(f"         ❌ 请求失败: {err_event.get('error')}")
        else:
            by_node: dict[str, list[str]] = {}
            for tc in tool_calls:
                n = tc.get("node") or "?"
                by_node.setdefault(n, []).append(tc["tool"])
            print(
                f"         工具调用合计: {len(tool_calls)} 次  "
                f"耗时: {elapsed:.1f}s"
            )
            if verbose:
                for node, tools in by_node.items():
                    print(f"            [{node}] {tools}")

        results.append(
            {
                "case_id": cid,
                "description": desc,
                "events": events,
            }
        )

    return results


# ── 打印最终报告 ──────────────────────────────────────────────────────
def print_report(
    m1_rate: float,
    m1_details: list[dict],
    m2_rate: float,
    m2_details: list[dict],
    m3_avg: float,
    m3_details: list[dict],
    only: list[str],
) -> bool:
    """打印评估报告，返回是否全部通过阈值。"""
    print(f"\n{_sep('═')}")
    print("  ★  上线评估报告  ★")
    print(_sep("═"))

    passed_all = True

    # ── M1 ────────────────────────────────────────────────────────────
    if "m1" in only:
        m1_pct = m1_rate * 100
        m1_pass = m1_rate >= M1_THRESHOLD
        if not m1_pass:
            passed_all = False
        mark = "✅ PASS" if m1_pass else "❌ FAIL"

        total_tc = sum(d["total_calls"] for d in m1_details)
        total_ok = sum(d["successful_calls"] for d in m1_details)

        print(f"\n【M1】工具调用成功率")
        print(f"      {_bar(m1_rate)}  {m1_pct:.1f}%  (目标 ≥ {M1_THRESHOLD * 100:.0f}%)")
        print(f"      结论: {mark}")
        print(
            f"      统计: 共 {total_tc} 次工具调用，"
            f"成功 {total_ok} 次，失败 {total_tc - total_ok} 次"
        )

        # 仅当失败时输出失败明细
        failures = [
            (d, cd)
            for d in m1_details
            for cd in d["call_details"]
            if cd["status"] != "success"
        ]
        if failures:
            print("      失败明细:")
            for d, cd in failures:
                print(
                    f"        · 用例 {d['case_id']:02d}「{d['description']}」"
                    f"  工具={cd['tool']}  状态={cd['status']}"
                )

    # ── M2 ────────────────────────────────────────────────────────────
    if "m2" in only:
        m2_pct = m2_rate * 100
        m2_pass = m2_rate >= M2_THRESHOLD
        if not m2_pass:
            passed_all = False
        mark = "✅ PASS" if m2_pass else "❌ FAIL"

        print(f"\n【M2】工具调用准确率")
        print(f"      {_bar(m2_rate)}  {m2_pct:.1f}%  (目标 ≥ {M2_THRESHOLD * 100:.0f}%)")
        print(f"      结论: {mark}")
        print("      各用例明细:")
        for d in m2_details:
            icon = "✅" if d["correct"] else "❌"
            called_str = (
                ", ".join(d["called_tools"]) if d["called_tools"] else "（未调用）"
            )
            print(
                f"        {icon} [{d['case_id']:02d}] {d['description']:<22s}"
                f"  期望={d['expected_category']:<10s}  实际={called_str}"
            )

    # ── M3 ────────────────────────────────────────────────────────────
    if "m3" in only:
        max_calls = max(
            (d["total_tool_calls"] for d in m3_details), default=1
        ) or 1

        print(f"\n【M3】平均工具调用轮次（行程规划效率）")
        print(f"      每次规划平均调用工具 {m3_avg:.1f} 次  "
              f"（越少表示 Agent 效率越高，合理范围视任务复杂度而定）")
        print("      各用例明细:")
        for d in m3_details:
            bar_w = max(1, int(d["total_tool_calls"] / max_calls * 20))
            bar = "▪" * bar_w
            node_summary = "  ".join(
                f"[{n}]×{len(tools)}" for n, tools in d["tools_by_node"].items()
            )
            print(
                f"        [{d['case_id']:02d}] {d['description']:<28s}"
                f"  {d['total_tool_calls']:3d} 次  {bar}  {node_summary}"
            )

    # ── 综合结论 ──────────────────────────────────────────────────────
    if "m1" in only or "m2" in only:
        print(f"\n{_sep('─')}")
        if passed_all:
            print("  综合结论: ✅ 全部关键指标达标，系统可以上线")
        else:
            print("  综合结论: ❌ 存在未达标指标，暂不建议上线")
            if "m1" in only and m1_rate < M1_THRESHOLD:
                print(
                    f"  · M1 工具调用成功率 {m1_rate * 100:.1f}% "
                    f"< 阈值 {M1_THRESHOLD * 100:.0f}%，需排查工具稳定性"
                )
            if "m2" in only and m2_rate < M2_THRESHOLD:
                print(
                    f"  · M2 工具调用准确率 {m2_rate * 100:.1f}% "
                    f"< 阈值 {M2_THRESHOLD * 100:.0f}%，需优化意图识别与路由"
                )
        print(_sep("─"))

    print()
    return passed_all


# ── 主逻辑 ────────────────────────────────────────────────────────────
async def main(base_url: str, only: list[str], verbose: bool) -> None:
    # 1. 先探活后端
    print(f"\n检查后端: {base_url}/api/health …")
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get(f"{base_url}/api/health")
            resp.raise_for_status()
        print(f"✅ 后端连接正常  {resp.json()}")
    except Exception as e:
        print(f"❌ 无法连接后端: {e}")
        print("   请先启动后端：uvicorn server.main:app --reload --port 8000")
        sys.exit(1)

    # 2. 运行用例
    m1_rate: float = 0.0
    m1_details: list[dict] = []
    m2_rate: float = 0.0
    m2_details: list[dict] = []
    m3_avg: float = 0.0
    m3_details: list[dict] = []

    if "m1" in only or "m2" in only:
        acc_results = await run_accuracy_cases(base_url, verbose)
        if "m1" in only:
            m1_rate, m1_details = compute_tool_success_rate(acc_results)
        if "m2" in only:
            m2_rate, m2_details = compute_tool_accuracy(acc_results, TOOL_CATEGORIES)

    if "m3" in only:
        plan_results = await run_planning_cases(base_url, verbose)
        m3_avg, m3_details = compute_tool_rounds(plan_results)

    # 3. 输出报告
    passed = print_report(
        m1_rate, m1_details,
        m2_rate, m2_details,
        m3_avg, m3_details,
        only,
    )

    sys.exit(0 if passed else 1)


# ── CLI ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="智慧旅游 Agent 上线评估",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python evaluation/run_eval.py
  python evaluation/run_eval.py --only m1 m2
  python evaluation/run_eval.py --only m3
  python evaluation/run_eval.py --verbose
  python evaluation/run_eval.py --base-url http://localhost:8001
""",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL（默认: http://localhost:8000）",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["m1", "m2", "m3"],
        default=["m1", "m2", "m3"],
        metavar="METRIC",
        help="只运行指定指标（m1 m2 m3，可多选，默认全部）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出工具调用结果详情",
    )
    args = parser.parse_args()
    asyncio.run(main(args.base_url, [m.lower() for m in args.only], args.verbose))
