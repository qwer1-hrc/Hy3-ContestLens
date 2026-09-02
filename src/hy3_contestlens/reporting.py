from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .utils import atomic_write


def run_failure_summary(result: Any) -> str | None:
    """Public, fixed-vocabulary explanation; never echo model/provider error text."""
    if not isinstance(result, dict) or not result.get("error_code"):
        return None
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    phase = {
        "problem_analyst": "分析题目", "solver": "生成解法", "algorithm_critic": "算法审查",
        "code_critic": "代码审查", "code_critic_recheck": "代码复查", "code_repair_agent": "修复代码",
    }.get(str(details.get("role")), "运行")
    kind = str(details.get("failure_kind"))
    cause = {
        "schema_validation": "模型输出字段校验失败", "json_decode": "模型返回的 JSON 无效",
        "response_shape": "模型响应结构异常", "output_truncated": "模型输出达到上限而被截断",
        "http_error": "模型接口返回错误", "transport_error": "模型连接中断或超时",
        "stream_incomplete": "模型连接提前结束，未收到完整结果", "stream_error": "模型在生成过程中返回错误",
        "refusal": "模型拒绝生成结果",
    }.get(kind, "运行未完成")
    if kind == "transport_error" and (details.get("exception_type") == "RemoteProtocolError" or "RemoteProtocolError" in str(details.get("reason", ""))):
        cause = "模型连接被提前关闭（RemoteProtocolError）"
    if result.get("error_code") == "HY3_NOT_CONFIGURED":
        cause = "尚未配置主模型"
    elif result.get("error_code") == "PROBLEM_ANALYSIS_INCOMPLETE":
        cause = "题面分析为空或不完整，已阻止生成占位程序"
    elif result.get("error_code") == "PROBLEM_DOCUMENT_EMPTY":
        cause = "未读取到题面文字，已停止求解"
    attempts = details.get("attempts")
    suffix = f"，尝试 {attempts} 次后停止" if type(attempts) is int and 1 <= attempts <= 5 else ""
    return f"{phase}：{cause}{suffix}"


def has_evaluation_report(result: Any) -> bool:
    """A failure diagnostic alone is not a completed evaluation report."""
    if not isinstance(result, dict):
        return False
    if any(not isinstance(result.get(key), str) for key in ("run_id", "problem_id", "stop_reason")):
        return False
    for key in ("initial_submission_result", "best_submission_result"):
        evaluation = result.get(key)
        if not isinstance(evaluation, dict):
            return False
        diagnosis = evaluation.get("diagnosis")
        if not isinstance(diagnosis, dict) or not isinstance(diagnosis.get("error_type"), str):
            return False
        if not isinstance(diagnosis.get("process_correct"), bool):
            return False
        check = evaluation.get("check")
        if check is not None:
            if not isinstance(check, dict):
                return False
            tests = check.get("tests", [])
            if not isinstance(tests, list) or any(not isinstance(test, dict) for test in tests):
                return False
    return True


def render_run_report(result: dict[str, Any]) -> str:
    if "initial_submission_result" not in result:
        return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Hy3-ContestLens failure report</title>
<style>body{{font:15px/1.6 system-ui;max-width:900px;margin:48px auto;padding:0 24px;color:#17202a}}h1{{color:#103b66}}pre{{background:#0b2031;color:#d7eaf4;padding:18px;border-radius:10px;white-space:pre-wrap}}</style></head><body>
<h1>评测未进入完整执行阶段</h1><p>错误码：<strong>{html.escape(str(result.get('error_code', 'UNKNOWN')))}</strong></p>
<p>{html.escape(str(result.get('message', 'No message')))}</p><pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre>
<p>该基础设施或配置失败不会计入模型准确率。</p></body></html>"""
    initial = result["initial_submission_result"]
    best = result["best_submission_result"]
    initial_check = initial.get("check") or {}
    best_check = best.get("check") or {}
    rows = "".join(
        f"<tr><td>{html.escape(str(item.get('test_id')))}</td><td>{html.escape(str(item.get('verdict')))}</td>"
        f"<td>{item.get('cpu_ms', 0)}</td><td>{item.get('wall_ms', 0)}</td><td>{item.get('peak_rss_mb', 0)}</td></tr>"
        for item in best_check.get("tests", [])
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>Hy3-ContestLens report</title>
<style>body{{font:15px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#17202a}}h1,h2{{color:#103b66}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd6e0;padding:7px;text-align:left}}th{{background:#edf5fb}}code{{background:#eef2f5;padding:2px 5px}}</style></head><body>
<h1>Hy3-ContestLens 评测报告</h1><p>Run <code>{html.escape(result['run_id'])}</code> · 题目 {html.escape(result['problem_id'])} · 停止原因 {html.escape(result['stop_reason'])}</p>
<h2>能力分离</h2><table><tr><th>阶段</th><th>通过点</th><th>总点数</th><th>过程正确</th><th>错误类型</th></tr>
<tr><td>初次提交</td><td>{initial_check.get('passed', 0)}</td><td>{initial_check.get('total', 0)}</td><td>{initial['diagnosis']['process_correct']}</td><td>{html.escape(initial['diagnosis']['error_type'])}</td></tr>
<tr><td>最佳提交</td><td>{best_check.get('passed', 0)}</td><td>{best_check.get('total', 0)}</td><td>{best['diagnosis']['process_correct']}</td><td>{html.escape(best['diagnosis']['error_type'])}</td></tr></table>
<h2>最佳版本逐点结果</h2><table><tr><th>测试点</th><th>Verdict</th><th>CPU ms</th><th>Wall ms</th><th>峰值 MB</th></tr>{rows}</table>
<h2>诊断</h2><pre>{html.escape(json.dumps(best['diagnosis'], ensure_ascii=False, indent=2))}</pre>
<p>洛谷难度：{html.escape(str(result.get('difficulty') or '待用户标注'))}。项目不会自行推断缺失难度。</p></body></html>"""


def write_run_report(path: Path, result: dict[str, Any]) -> None:
    atomic_write(path, render_run_report(result).encode("utf-8"))


def render_run_markdown(result: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;")
    if "initial_submission_result" not in result:
        return f"# Hy3-ContestLens 运行失败\n\n- 错误码：`{esc(result.get('error_code', 'UNKNOWN'))}`\n"
    initial, best = result["initial_submission_result"], result["best_submission_result"]
    lines = [
        "# Hy3-ContestLens 评测报告", "",
        f"- Run：`{esc(result['run_id'])}`", f"- 题目：`{esc(result['problem_id'])}`",
        f"- 停止原因：`{esc(result['stop_reason'])}`", "", "## 能力分离", "",
        "| 阶段 | 通过点 | 总点数 | 过程正确 | 错误类型 |", "| --- | ---: | ---: | --- | --- |",
    ]
    for title, evaluation in (("初次提交", initial), ("最佳提交", best)):
        check = evaluation.get("check") or {}
        diagnosis = evaluation["diagnosis"]
        lines.append(f"| {title} | {check.get('passed', 0)} | {check.get('total', 0)} | {'是' if diagnosis['process_correct'] else '否'} | {esc(diagnosis['error_type'])} |")
    lines += ["", "## 最佳版本逐点结果", "", "| 测试点 | Verdict | CPU ms | Wall ms | 峰值 MB |", "| --- | --- | ---: | ---: | ---: |"]
    for item in (best.get("check") or {}).get("tests", []):
        lines.append(f"| {esc(item.get('test_id'))} | {esc(item.get('verdict'))} | {item.get('cpu_ms', 0)} | {item.get('wall_ms', 0)} | {item.get('peak_rss_mb', 0)} |")
    diagnosis = best["diagnosis"]
    lines += ["", "## 诊断", "", f"- 错误类型：`{esc(diagnosis['error_type'])}`", f"- 首个错误步骤：`{esc(diagnosis.get('first_error_step_id') or '未定位')}`", "", "### 证据", ""]
    lines += [f"- {esc(item)}" for item in diagnosis.get("evidence", [])] or ["- 无"]
    if diagnosis.get("repair_suggestion"):
        lines += ["", "### 修复建议", "", esc(diagnosis["repair_suggestion"])]
    return "\n".join(lines) + "\n"


def aggregate_runs(results: list[dict[str, Any]]) -> dict[str, Any]:
    initial_total = sum((item["initial_submission_result"].get("check") or {}).get("total", 0) for item in results)
    initial_passed = sum((item["initial_submission_result"].get("check") or {}).get("passed", 0) for item in results)
    final_total = sum((item["best_submission_result"].get("check") or {}).get("total", 0) for item in results)
    final_passed = sum((item["best_submission_result"].get("check") or {}).get("passed", 0) for item in results)
    process_correct = sum(item["best_submission_result"]["diagnosis"]["process_correct"] for item in results)
    errors = Counter(item["best_submission_result"]["diagnosis"]["error_type"] for item in results)
    labeled = [item for item in results if item.get("difficulty")]
    return {
        "run_count": len(results),
        "initial_final_answer_accuracy": initial_passed / initial_total if initial_total else None,
        "final_answer_accuracy": final_passed / final_total if final_total else None,
        "process_correct_rate": process_correct / len(results) if results else None,
        "error_distribution": dict(errors),
        "repair_success_rate": sum(item.get("repair_success", False) for item in results) / len(results) if results else None,
        "difficulty_analysis": "AVAILABLE" if len(labeled) == len(results) and results else "DIFFICULTY_DATA_INCOMPLETE",
    }
