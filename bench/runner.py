"""Benchmark Runner for Laya Browser Agent.

Executes test tasks across tiers, evaluates state and tool oracles,
computes 95% Wilson confidence intervals, and records results into
bench/results/<date>_<commit>.json and .md.
"""

import argparse
import json
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .loader import load_benchmark_tasks
from .oracles import BenchmarkOracle
from .server import get_fixture_server
from .stats import wilson_interval


def get_git_commit() -> str:
    """Returns short git commit hash or 'unknown'."""
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"


def resolve_task_routing(site: str, fixture_base_url: str) -> Tuple[str, str, str]:
    """Returns (oracle_base_url, site_route, target_url) for a task's `site`.

    External sites (T3/T4, absolute URL) have no fixture route; oracles query the site root.
    """
    if site.startswith("http"):
        return site.rstrip("/"), "", site
    route = site.replace("fixture/", "").strip("/")
    return fixture_base_url, route, f"{fixture_base_url.rstrip('/')}/{route}"


def generate_markdown_summary(summary: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
    """Formats benchmark results into GitHub-flavored Markdown."""
    commit = summary.get("commit", "unknown")
    tier = summary.get("tier", "T2")
    p1 = summary.get("task_success@1", 0.0)
    p1_ci = summary.get("task_success@1_ci", [0.0, 0.0])
    pass3_str = f"{summary.get('pass^3', 0.0) * 100:.1f}%" if summary.get("pass^3") is not None else "N/A (repeat < 3)"
    fs_rate = summary.get("false_success_rate", 0.0)
    laya_share = summary.get("laya_share", 0.0)
    median_lat = summary.get("median_decision_latency_ms", 0.0)
    llm_calls = summary.get("avg_llm_calls_per_task", 0.0)

    lines = [
        f"# Benchmark Run Report — Tier {tier} ({commit})",
        "",
        f"- **Date**: {summary.get('date')}",
        f"- **Total Tasks**: {summary.get('tasks_count')}",
        f"- **Total Runs**: {summary.get('total_runs')} (repeat={summary.get('repeat')})",
        "",
        "## Summary Scorecard",
        "",
        "| Metric | Measured Result | Production Target |",
        "| :--- | :--- | :--- |",
        f"| `task_success@1` | **{p1 * 100:.1f}%** (95% CI: [{p1_ci[0] * 100:.1f}%, {p1_ci[1] * 100:.1f}%]) | ≥ 90% |",
        f"| `pass^3` (reliability) | **{pass3_str}** | ≥ 80% |",
        f"| `false_success_rate` | **{fs_rate * 100:.1f}%** | ≤ 1% |",
        f"| `laya_share` | **{laya_share * 100:.1f}%** | ≥ 80% |",
        f"| Median decision latency (excl. actuation) | **{median_lat:.1f} ms** | ≤ 50 ms (Laya) / ≤ 2500 ms (LLM) |",
        f"| Avg LLM calls / task | **{llm_calls:.1f} calls** | ≤ 1 + 0.15 × steps |",
        "",
        "## Detailed Task Breakdown",
        "",
        "| Task ID | Split | Agent Status | Oracle | Steps | Laya % | Duration |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for r in results:
        t_id = r["task_id"]
        split = r.get("split", "heldout")
        status = r["agent_status"]
        oracle = "PASS" if r["oracle_passed"] else "FAIL"
        steps = r["steps"]
        l_steps = r["laya_steps"]
        pct = f"{(l_steps / max(1, steps)) * 100:.0f}%"
        dur = f"{r['elapsed_s']:.1f}s"
        lines.append(f"| `{t_id}` | {split} | {status} | **{oracle}** | {steps} | {pct} | {dur} |")

    return "\n".join(lines) + "\n"


def run_benchmark(
    tier: str = "T2",
    repeat: int = 1,
    split: str = "all",
    headed: bool = False,
    max_tasks: Optional[int] = None,
    output_path: Optional[str] = None,
    task_ids: Optional[List[str]] = None,
    task_timeout_s: float = 180.0,
) -> Dict[str, Any]:
    """Runs benchmark tasks across declared tiers and returns summary metrics."""
    from laya_browser_agent.agent import LayaAgent

    server = None
    if tier.upper() == "T2":
        server = get_fixture_server()
        server.start()

    tasks = load_benchmark_tasks(tier=tier, split=split)
    if task_ids:
        wanted = {t.strip() for t in task_ids if t.strip()}
        tasks = [t for t in tasks if t.id in wanted]
    if max_tasks and max_tasks > 0:
        tasks = tasks[:max_tasks]

    print(f"\n=======================================================")
    print(f"  Laya Benchmark Runner — Tier {tier.upper()} ({len(tasks)} tasks, repeat={repeat})")
    print(f"=======================================================")

    results: List[Dict[str, Any]] = []
    out_dir = Path("bench/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    # One JSON line per finished task, written immediately, so an interrupted run still leaves evidence.
    partial_path = out_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{get_git_commit()}.partial.jsonl"
    print(f"  Live progress file: {partial_path}")
    task_successes: Dict[str, int] = {t.id: 0 for t in tasks}
    first_run_passed: Dict[str, bool] = {t.id: False for t in tasks}
    total_runs = 0
    total_passed = 0
    total_false_success = 0
    total_laya_steps = 0
    total_steps = 0
    all_decision_latencies: List[float] = []
    total_llm_calls = 0

    base_url = server.base_url if server else "http://127.0.0.1:3000"

    for r_idx in range(repeat):
        print(f"\n--- Repeat Run {r_idx + 1}/{repeat} ---")
        for task in tasks:
            # Map site route
            is_external = task.site.startswith("http")
            task_base_url, site_route, target_url = resolve_task_routing(task.site, base_url)

            # Reset fixture state before task run
            if server and not is_external:
                try:
                    httpx.get(f"{base_url}/{site_route}/__reset", timeout=2.0)
                except Exception:
                    pass

            run_id = f"bench_{task.id}_r{r_idx}_{int(time.time())}"
            t0 = time.time()
            agent = LayaAgent(
                target_url=target_url,
                goal=task.goal,
                headed=headed,
                max_steps=task.max_steps,
                max_seconds=task_timeout_s,
                run_id=run_id,
                generate_report=False,
                screenshot_mode="none",
            )

            agent_status = "UNKNOWN"
            try:
                res = agent.run()
                raw_st = res.get("status") if isinstance(res, dict) else ("SUCCESS" if res else "INCOMPLETE")
                agent_status = str(raw_st).upper()
            except Exception as e:
                agent_status = f"ERROR: {e}"

            elapsed = time.time() - t0

            # Evaluate Oracle
            oracle_passed, oracle_msg = BenchmarkOracle.evaluate(
                oracle_spec=task.oracle,
                base_url=task_base_url,
                site_route=site_route,
                agent=agent,
                steps_taken=agent.steps_taken,
                extracted_data=agent.extracted_data,
            )

            # Check False Success (agent claimed success but oracle failed)
            is_false_success = (agent_status == "SUCCESS" and not oracle_passed)
            if is_false_success:
                total_false_success += 1

            if oracle_passed:
                task_successes[task.id] += 1
                if r_idx == 0:
                    first_run_passed[task.id] = True
                total_passed += 1

            total_runs += 1
            step_count = len(agent.steps_taken)
            total_steps += step_count

            # Measured counts: LLM calls come from the client counter, Laya steps are steps with zero LLM calls.
            laya_count = sum(1 for s in agent.steps_taken if not s.llm_fallback)
            task_llm_calls = agent.llm_calls_total
            total_llm_calls += task_llm_calls
            total_laya_steps += laya_count

            for s in agent.steps_taken:
                if s.decision_ms is not None:
                    all_decision_latencies.append(s.decision_ms)

            icon = "✓" if oracle_passed else "✗"
            print(f"  [{icon}] {task.id:<25} ({agent_status:<10}) -> Oracle: {'PASS' if oracle_passed else 'FAIL'} ({elapsed:.1f}s, {step_count} steps)")

            result_row = {
                "task_id": task.id,
                "tier": task.tier,
                "split": task.split,
                "repeat_idx": r_idx,
                "agent_status": agent_status,
                "oracle_passed": oracle_passed,
                "oracle_msg": oracle_msg,
                "is_false_success": is_false_success,
                "steps": step_count,
                "laya_steps": laya_count,
                "llm_calls": task_llm_calls,
                "elapsed_s": round(elapsed, 2),
                "run_id": run_id,
            }
            results.append(result_row)
            with open(partial_path, "a", encoding="utf-8") as pf:
                pf.write(json.dumps(result_row) + "\n")

    # Aggregate Metrics
    pass_at_1_tasks = sum(1 for t in tasks if first_run_passed[t.id])
    p1, p1_low, p1_high = wilson_interval(pass_at_1_tasks, len(tasks))

    p3: Optional[float] = None
    p3_low: Optional[float] = None
    p3_high: Optional[float] = None
    if repeat >= 3:
        pass_cubed_tasks = sum(1 for t in tasks if task_successes[t.id] == repeat)
        p3, p3_low, p3_high = wilson_interval(pass_cubed_tasks, len(tasks))

    fs_rate = round(float(total_false_success) / float(max(1, total_runs)), 4)
    laya_share = round(float(total_laya_steps) / float(max(1, total_steps)), 4)
    median_latency = round(float(statistics.median(all_decision_latencies)), 2) if all_decision_latencies else 0.0
    avg_llm_calls = round(float(total_llm_calls) / float(max(1, total_runs)), 2)

    commit = get_git_commit()
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_output = output_path or str(out_dir / f"{date_str}_{commit}.json")
    md_output = str(Path(final_output).with_suffix(".md"))

    summary = {
        "tier": tier,
        "commit": commit,
        "date": date_str,
        "tasks_count": len(tasks),
        "repeat": repeat,
        "total_runs": total_runs,
        "task_success@1": p1,
        "task_success@1_ci": [p1_low, p1_high],
        "pass^3": p3,
        "pass^3_ci": [p3_low, p3_high] if p3 is not None else None,
        "false_success_rate": fs_rate,
        "laya_share": laya_share,
        "total_steps": total_steps,
        "median_decision_latency_ms": median_latency,
        "avg_llm_calls_per_task": avg_llm_calls,
        "detailed_results": results,
    }

    try:
        with open(final_output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        with open(md_output, "w", encoding="utf-8") as f:
            f.write(generate_markdown_summary(summary, results))
    except Exception as exc:
        print(f"Warning: could not write benchmark output: {exc}")

    print(f"\n=======================================================")
    print(f"  Benchmark Results Summary ({commit})")
    print(f"  task_success@1: {p1 * 100:.1f}% (95% CI: [{p1_low * 100:.1f}%, {p1_high * 100:.1f}%])")
    if p3 is not None and p3_low is not None and p3_high is not None:
        print(f"  pass^3:         {p3 * 100:.1f}% (95% CI: [{p3_low * 100:.1f}%, {p3_high * 100:.1f}%])")
    else:
        print(f"  pass^3:         N/A (repeat={repeat} < 3)")
    print(f"  false_success:  {fs_rate * 100:.1f}%")
    print(f"  laya_share:     {laya_share * 100:.1f}%")
    print(f"  median_latency: {median_latency:.1f} ms")
    print(f"  avg_llm_calls:  {avg_llm_calls:.1f} / task")
    print(f"  JSON results:   {final_output}")
    print(f"  Markdown table: {md_output}")
    print(f"=======================================================\n")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Laya Benchmark Runner")
    parser.add_argument("cmd", nargs="?", default="run", choices=["run", "list", "serve"])
    parser.add_argument("--tier", default="T2", help="Benchmark tier: T1, T2, T3, T4")
    parser.add_argument("--repeat", type=int, default=1, help="Repetitions per task (default: 1)")
    parser.add_argument("--split", default="all", help="Split filter: train, heldout, all")
    parser.add_argument("--headed", action="store_true", default=False)
    parser.add_argument("--max-tasks", type=int, default=None, help="Run only the first N tasks")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated task ids to run, e.g. t2-shop-001,t2-forms-006")
    parser.add_argument("--task-timeout", type=float, default=180.0, help="Per-task wall-clock budget in seconds (default 180)")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    if args.cmd == "serve":
        server = get_fixture_server()
        server.start()
        print(f"Practice sites: {server.base_url}/shop/  {server.base_url}/admin/  {server.base_url}/forms/")
        print("Reset state with <site>/__reset, inspect it with <site>/__state. Ctrl-C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.stop()
        return

    if args.cmd == "list":
        tasks = load_benchmark_tasks(tier=args.tier, split=args.split)
        print(f"Found {len(tasks)} tasks for tier {args.tier} (split: {args.split}):")
        for t in tasks:
            print(f"  [{t.tier}] {t.id} ({t.split}) - {t.goal[:60]}")
        return

    run_benchmark(
        tier=args.tier,
        repeat=args.repeat,
        split=args.split,
        headed=args.headed,
        max_tasks=args.max_tasks,
        output_path=args.output,
        task_ids=args.ids.split(",") if args.ids else None,
        task_timeout_s=args.task_timeout,
    )


if __name__ == "__main__":
    main()
