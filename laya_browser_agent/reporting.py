import hashlib
import json
import os
import time
from typing import Any, Optional, Tuple


def evaluate_run_status(agent: Any, execution_error: Optional[str] = None) -> Tuple[str, str, str, str]:
    """
    Evaluates real truthfulness status of an agent run.
    Returns: (status_code, markdown_badge, html_badge_text, html_badge_color)
    """
    if execution_error:
        return "error", f"❌ FAILED (ERROR: {execution_error})", f"FAILED: {execution_error}", "#ef4444"

    # 1. Did any subtask produce an unfulfillable or failed audit finding?
    failed_findings = [
        item for item in (getattr(agent, "extracted_data", []) or [])
        if item.get("status") in ("unfulfillable", "failed")
    ]
    if failed_findings:
        first = failed_findings[0]
        reason = first.get("reason") or first.get("summary") or "Action could not be completed on page"
        badge = f"⚠️ UNFULFILLABLE ({reason[:65]})"
        return "unfulfillable", badge, "UNFULFILLABLE", "#f59e0b"

    # 2. Did the agent abort before completing all subtasks?
    subtasks = getattr(agent, "subtasks", []) or []
    current_idx = getattr(agent, "current_subtask_idx", 0)
    if subtasks and current_idx < len(subtasks):
        badge = f"⚠️ INCOMPLETE ({current_idx} of {len(subtasks)} subtasks finished)"
        return "incomplete", badge, f"INCOMPLETE ({current_idx}/{len(subtasks)})", "#f59e0b"


    # 3. Did the last step terminate with assertion, input, or tool failure?
    steps = getattr(agent, "steps_taken", []) or []
    if steps:
        last_step = steps[-1]
        res = getattr(last_step, "result", None)
        if isinstance(res, dict) and any(k in res for k in ("assertion_failed", "input_error", "tool_error")):
            err_msg = res.get("assertion_failed") or res.get("input_error") or res.get("tool_error")
            return "failed", f"❌ FAILED ({str(err_msg)[:60]})", "FAILED", "#ef4444"

    # 4. Max steps reached without calling finish
    if not steps or steps[-1].chosen_action != "finish":
        return "max_steps", "⚠️ MAX STEPS REACHED", "MAX STEPS REACHED", "#f59e0b"

    # 5. Success
    return "success", "✅ COMPLETED (SUCCESS)", "COMPLETED", "#10b981"


def generate_markdown_report(
    agent: Any,
    output_path: Optional[str] = None,
    final_screenshot: Optional[str] = None,
    final_url: Optional[str] = None,
    execution_error: Optional[str] = None,
) -> str:
    """Generates a detailed execution report in Markdown format."""
    path = output_path or agent.report_file
    total_duration = sum(s.duration_ms for s in agent.steps_taken) / 1000.0
    avg_step_ms = (sum(s.duration_ms for s in agent.steps_taken) / len(agent.steps_taken)) if agent.steps_taken else 0.0
    _, status_badge, _, _ = evaluate_run_status(agent, execution_error)

    lines = [
        "# ⚡️ Laya Browser Agent Execution Report",
        "",
        "## 📌 Executive Summary",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Final Status** | **{status_badge}** |",
        f"| **Initial Target URL** | `{agent.target_url}` |",
        f"| **Final URL Reached** | `{final_url or (agent.steps_taken[-1].url if agent.steps_taken else agent.target_url)}` |",
        f"| **User Goal** | {agent.goal} |",
        f"| **Total Steps Taken** | `{len(agent.steps_taken)}` / `{agent.max_steps}` |",
        f"| **Total Duration** | `{total_duration:.2f}s` |",
        f"| **Avg Decision Latency** | `{avg_step_ms:.1f}ms / step` |",
        f"| **Decision Policy** | `{agent.model_id}` (Apple Silicon Metal GPU) |",
        f"| **Local Planner** | `{agent.ollama_model}` |",
        f"| **Cloud API Cost** | **$0.00** (100% Local Inference, Zero Cloud Tokens) |",
    ]

    if agent.extracted_data:
        summary_item = next(
            (item for item in reversed(agent.extracted_data) if item.get("metric") == "Comparative Data Audit" or item.get("status") == "COMPLETED"),
            agent.extracted_data[-1]
        )
        lines.append(f"| **Key Findings** | **{summary_item.get('summary', '')}** |")

    # Session Telemetry (Minimalist Monospace Table)
    telemetry = agent.get_session_telemetry() if hasattr(agent, "get_session_telemetry") else {}
    in_tok = telemetry.get("input_tokens", 0)
    out_tok = telemetry.get("output_tokens", 0)
    tot_tok = telemetry.get("total_tokens", in_tok + out_tok)
    c_reads = telemetry.get("cache_reads", 0)
    c_writes = telemetry.get("cache_writes", 0)
    saved_s = (telemetry.get("latency_saved_ms", 0.0)) / 1000.0

    lines.extend([
        "",
        "## 📊 Session Telemetry",
        "| Metric | Count / Duration |",
        "| :--- | :--- |",
        f"| Input Tokens (Prompt Eval) | `{in_tok:,}` |",
        f"| Output Tokens (Completion) | `{out_tok:,}` |",
        f"| Total Tokens Processed | `{tot_tok:,}` |",
        f"| Cache Reads (Hits) | `{c_reads}` |",
        f"| Cache Writes (New Entries) | `{c_writes}` |",
        f"| Estimated Cache Latency Saved | `{saved_s:.2f}s` |",
        f"| Total Session Duration | `{total_duration:.2f}s` |",
        f"| Avg Decision Latency | `{avg_step_ms:.1f}ms / step` |",
        "| Cloud API Cost | `$0.00 (100% Local Inference)` |",
        "",
        "## 📋 Structured Plan & Entity Resolution",
    ])

    if agent.extracted_slots:
        safe_slots = dict(agent.extracted_slots)
        for k in list(safe_slots.keys()):
            if any(w in k.lower() for w in ("pass", "pwd", "secret")):
                safe_slots[k] = "••••••••"
        lines.append("### Extracted Entities")
        lines.append("| Slot | Resolved Value |")
        lines.append("| :--- | :--- |")
        for k, v in safe_slots.items():
            lines.append(f"| `{k}` | `{v}` |")
        lines.append("")

    if agent.extracted_data:
        lines.append("### 📊 Extracted Findings & Data Audit")
        lines.append("| Metric / Query | Result / Count | Source | Verification Summary |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for item in agent.extracted_data:
            m_name = item.get("metric") or item.get("subtask") or "Metric"
            cnt = item.get("status", item.get("count", 0)) if item.get("status") in ("unfulfillable", "remediated", "failed") else item.get("count", 0)
            src = item.get("source", "DOM")
            summ = item.get("summary") or item.get("reason") or ""
            lines.append(f"| **{m_name}** | `{cnt}` | `{src}` | {summ} |")
        lines.append("")

    if agent.subtasks:
        lines.append("### Sequential Subtasks Progress")
        for idx, st in enumerate(agent.subtasks, 1):
            is_unfulfillable = any(
                item.get("subtask") == st and item.get("status") in ("unfulfillable", "failed")
                for item in (getattr(agent, "extracted_data", []) or [])
            )
            if is_unfulfillable:
                marker = "✗"
                suffix = " — *(Unfulfillable / Action Not Available)*"
            elif idx <= agent.current_subtask_idx:
                marker = "✓"
                suffix = ""
            else:
                marker = "○"
                suffix = " — *(Pending)*"
            lines.append(f"{idx}. [{marker}] {st}{suffix}")
        lines.append("")

    lines.append("## ⏱ Step-by-Step Action Audit Trail")
    lines.append("| Step | Page URL | Subtask | Action Chosen | Parameters | Engine / Source | Confidence | Latency | Result |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for event in agent.steps_taken:
        safe_params = dict(event.tool_params)
        for pk in list(safe_params.keys()):
            if any(w in pk.lower() for w in ("pass", "pwd", "secret")):
                safe_params[pk] = "••••••••"
        param_str = json.dumps(safe_params) if safe_params else "{}"
        if len(param_str) > 40:
            param_str = param_str[:37] + "..."
        param_str = param_str.replace("|", "\\|")

        res_str = str(event.tool_result) if event.tool_result is not None else "None"
        if len(res_str) > 50:
            res_str = res_str[:47] + "..."
        res_str = res_str.replace("|", "\\|")
        sub_clean = event.subtask.replace("|", "\\|")
        url_clean = event.url.split("://")[-1] if event.url else ""
        if len(url_clean) > 30:
            url_clean = "..." + url_clean[-27:]
        conf_str = f"{event.confidence * 100:.1f}%"
        lines.append(
            f"| **{event.step}** | `{url_clean}` | {sub_clean} | `{event.chosen_action}` | `{param_str}` | {event.engine_info} | {conf_str} | {event.duration_ms:.1f}ms | {res_str} |"
        )

    lines.append("")

    if agent.engine and hasattr(agent.engine, "site_cache") and agent.engine.site_cache:
        lines.append("## 🗺 Discovered Web Application Affordances")
        lines.append("*Dynamically discovered interactive capabilities across visited application routes:*")
        lines.append("")
        lines.append("| Route Key | Discovered Capabilities |")
        lines.append("| :--- | :--- |")
        for r_key, r_cands in agent.engine.site_cache.items():
            cand_names = [k for k in r_cands.keys() if k != "finish"]
            sample = ", ".join([f"`{c}`" for c in cand_names[:5]])
            if len(cand_names) > 5:
                sample += f" *(+{len(cand_names)-5} more)*"
            lines.append(f"| `{r_key}` | {sample or 'None'} |")
        lines.append("")

    def _file_hash(p: str) -> str:
        try:
            with open(p, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return p

    all_step_screenshots = [
        (s.step, s.subtask, s.chosen_action, s.screenshot_path or (s.tool_params.get("path") if s.chosen_action == "browser_take_screenshot" else None))
        for s in agent.steps_taken
    ]
    seen_hashes = set()
    deduped_screenshots = []
    for st, sub, act, sc in all_step_screenshots:
        if sc and os.path.exists(sc):
            h = _file_hash(sc)
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped_screenshots.append((st, sub, act, sc))

    if deduped_screenshots:
        lines.append("## 📷 Step-by-Step Visual Execution Log")
        lines.append("*Visual screenshot captured at every execution step:*")
        lines.append("")
        for step_idx, sub, act, sc_p in deduped_screenshots:
            lines.append(f"### Step {step_idx}: {sub}")
            lines.append(f"- **Action**: `{act}`")
            lines.append(f"![Step {step_idx}]({os.path.basename(sc_p)})")
            lines.append("")

    if final_screenshot and os.path.exists(final_screenshot):
        final_h = _file_hash(final_screenshot)
        if final_h not in seen_hashes:
            rel_screenshot = os.path.basename(final_screenshot)
            lines.append("## 📸 Final Visual Verification Proof")
            lines.append(f"![Final Application State]({rel_screenshot})")
            lines.append("")

    lines.append("---")
    lines.append(f"*Generated automatically by Laya Browser Agent on {time.strftime('%Y-%m-%d %H:%M:%S')}*")

    report_content = "\n".join(lines)
    if path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(report_content)
        except Exception:
            pass

    return report_content


def generate_html_dashboard(
    agent: Any,
    output_path: Optional[str] = None,
    final_screenshot: Optional[str] = None,
    final_url: Optional[str] = None,
    execution_error: Optional[str] = None,
) -> str:
    """Generates a modern, responsive HTML execution report dashboard."""
    path = output_path or agent.report_file.rsplit(".", 1)[0] + ".html"
    total_duration = sum(s.duration_ms for s in agent.steps_taken) / 1000.0
    avg_step_ms = (sum(s.duration_ms for s in agent.steps_taken) / len(agent.steps_taken)) if agent.steps_taken else 0.0
    _, _, status_text, status_color = evaluate_run_status(agent, execution_error)
    if status_color == "#10b981":
        status_bg = "rgba(16, 185, 129, 0.15)"
    elif status_color == "#ef4444":
        status_bg = "rgba(239, 68, 68, 0.15)"
    else:
        status_bg = "rgba(245, 158, 11, 0.15)"

    safe_slots = dict(agent.extracted_slots or {})
    for k in list(safe_slots.keys()):
        if any(w in k.lower() for w in ("pass", "pwd", "secret")):
            safe_slots[k] = "••••••••"

    finding_summary_html = ""
    if agent.extracted_data:
        comp_item = next(
            (item for item in reversed(agent.extracted_data) if item.get("metric") == "Comparative Data Audit" or item.get("status") == "COMPLETED"),
            agent.extracted_data[-1]
        )
        if comp_item.get("summary"):
            finding_summary_html = f'<div class="metric-row"><span class="metric-label">Key Finding</span><span class="metric-val highlight">{comp_item.get("summary")}</span></div>'

    subtasks_html = ""
    for idx, st in enumerate(agent.subtasks, 1):
        is_unfulfillable = any(
            item.get("subtask") == st and item.get("status") in ("unfulfillable", "failed")
            for item in (getattr(agent, "extracted_data", []) or [])
        )
        if is_unfulfillable:
            icon = "✗"
            cls = "failed"
        elif idx <= agent.current_subtask_idx:
            icon = "✓"
            cls = "done"
        else:
            icon = "○"
            cls = "pending"
        subtasks_html += f'<li class="subtask-item {cls}"><span class="badge">{icon}</span> {st}</li>\n'

    findings_html = ""
    if agent.extracted_data:
        findings_html += """
        <div class="card">
            <h2>📊 Extracted Findings & Data Audit</h2>
            <div class="table-responsive">
            <table>
                <thead><tr><th>Metric</th><th>Count</th><th>Source</th><th>Summary</th></tr></thead>
                <tbody>
        """
        for item in agent.extracted_data:
            pill_cls = "pill-warning" if item.get("status") in ("unfulfillable", "remediated", "failed") else "pill-success"
            cnt_val = item.get("status") if item.get("status") in ("unfulfillable", "remediated", "failed") else item.get("count", 0)
            m_name = item.get("metric") or item.get("subtask") or "Metric"
            summ = item.get("summary") or item.get("reason") or ""
            findings_html += f"<tr><td><strong>{m_name}</strong></td><td><span class='pill {pill_cls}'>{cnt_val}</span></td><td>{item.get('source', '')}</td><td>{summ}</td></tr>\n"
        findings_html += "</tbody></table></div></div>"

    audit_rows_html = ""
    for event in agent.steps_taken:
        safe_params = dict(event.tool_params)
        for pk in list(safe_params.keys()):
            if any(w in pk.lower() for w in ("pass", "pwd", "secret")):
                safe_params[pk] = "••••••••"
        param_str = json.dumps(safe_params) if safe_params else "{}"
        if len(param_str) > 35:
            param_str = param_str[:32] + "..."

        res_str = str(event.tool_result) if event.tool_result is not None else "None"
        if len(res_str) > 40:
            res_str = res_str[:37] + "..."

        url_clean = event.url.split("://")[-1] if event.url else ""
        if len(url_clean) > 25:
            url_clean = "..." + url_clean[-22:]

        audit_rows_html += f"""
        <tr>
            <td><strong>{event.step}</strong></td>
            <td><code>{url_clean}</code></td>
            <td>{event.subtask}</td>
            <td><span class="action-tag">{event.chosen_action}</span></td>
            <td><code>{param_str}</code></td>
            <td><span class="engine-badge">{event.engine_info}</span></td>
            <td>{event.confidence * 100:.1f}%</td>
            <td>{event.duration_ms:.1f}ms</td>
            <td><span class="result-cell">{res_str}</span></td>
        </tr>
        """

    def _file_hash(p: str) -> str:
        try:
            with open(p, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return p

    telemetry = agent.get_session_telemetry() if hasattr(agent, "get_session_telemetry") else {}
    in_tok = telemetry.get("input_tokens", 0)
    out_tok = telemetry.get("output_tokens", 0)
    tot_tok = telemetry.get("total_tokens", in_tok + out_tok)
    c_reads = telemetry.get("cache_reads", 0)
    c_writes = telemetry.get("cache_writes", 0)
    saved_s = (telemetry.get("latency_saved_ms", 0.0)) / 1000.0

    telemetry_html = f"""
    <div class="card">
        <h2>📊 Session Telemetry</h2>
        <div class="table-responsive">
        <table>
            <thead><tr><th>Metric</th><th>Count / Duration</th></tr></thead>
            <tbody>
                <tr><td>Input Tokens (Prompt Eval)</td><td><code>{in_tok:,}</code></td></tr>
                <tr><td>Output Tokens (Completion)</td><td><code>{out_tok:,}</code></td></tr>
                <tr><td>Total Tokens Processed</td><td><code>{tot_tok:,}</code></td></tr>
                <tr><td>Cache Reads (Hits)</td><td><code>{c_reads}</code></td></tr>
                <tr><td>Cache Writes (New Entries)</td><td><code>{c_writes}</code></td></tr>
                <tr><td>Estimated Cache Latency Saved</td><td><code>{saved_s:.2f}s</code></td></tr>
                <tr><td>Total Duration</td><td><code>{total_duration:.2f}s</code></td></tr>
                <tr><td>Avg Decision Latency</td><td><code>{avg_step_ms:.1f}ms / step</code></td></tr>
                <tr><td>Cloud API Cost</td><td><code>$0.00 (100% Local Inference)</code></td></tr>
            </tbody>
        </table>
        </div>
    </div>
    """

    gallery_html = ""
    all_step_screenshots = [
        (s.step, s.subtask, s.chosen_action, s.screenshot_path or (s.tool_params.get("path") if s.chosen_action == "browser_take_screenshot" else None))
        for s in agent.steps_taken
    ]
    seen_hashes = set()
    deduped_screenshots = []
    for st, sub, act, sc in all_step_screenshots:
        if sc and os.path.exists(sc):
            h = _file_hash(sc)
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped_screenshots.append((st, sub, act, sc))

    if deduped_screenshots:
        gallery_html = """
        <div class="card">
            <h2>📷 Step-by-Step Visual Execution Log</h2>
            <div class="gallery-grid">
        """
        for step_idx, sub, act, sc_p in deduped_screenshots:
            rel_p = os.path.basename(sc_p)
            gallery_html += f"""
            <div class="gallery-card">
                <div class="gallery-card-header">Step {step_idx}: {sub}</div>
                <div class="gallery-card-body">
                    <img src="{rel_p}" alt="Step {step_idx}" loading="lazy" />
                    <div class="gallery-card-footer">Action: <code>{act}</code></div>
                </div>
            </div>
            """
        gallery_html += "</div></div>"

    final_sc_html = ""
    if final_screenshot and os.path.exists(final_screenshot):
        final_h = _file_hash(final_screenshot)
        if final_h not in seen_hashes:
            rel_sc = os.path.basename(final_screenshot)
            final_sc_html = f"""
            <div class="card">
                <h2>📸 Final Visual Verification Proof</h2>
                <div class="final-screenshot-container">
                    <img src="{rel_sc}" alt="Final Application State" />
                </div>
            </div>
            """

    resolved_final_url = final_url or (agent.steps_taken[-1].url if agent.steps_taken else agent.target_url)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Laya Agent Execution Report</title>
<style>
:root {{
    --bg: #0f172a;
    --card-bg: #1e293b;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #38bdf8;
    --border: #334155;
    --accent: #818cf8;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background-color: var(--bg);
    color: var(--text-main);
    padding: 24px;
    line-height: 1.5;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
}}
.logo-title {{ display: flex; align-items: center; gap: 12px; }}
.logo-title h1 {{ font-size: 24px; font-weight: 700; color: var(--text-main); }}
.status-pill {{
    padding: 6px 14px;
    border-radius: 9999px;
    font-size: 13px;
    font-weight: 700;
    color: {status_color};
    background: {status_bg};
    border: 1px solid {status_color};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
}}
@media (max-width: 768px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
.card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 24px;
}}
.card h2 {{
    font-size: 16px;
    font-weight: 600;
    color: var(--text-main);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.metric-row {{
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    font-size: 14px;
}}
.metric-row:last-child {{ border-bottom: none; }}
.metric-label {{ color: var(--text-muted); }}
.metric-val {{ font-weight: 600; font-family: monospace; }}
.metric-val.highlight {{ color: #10b981; }}
.subtask-list {{ list-style: none; }}
.subtask-item {{
    padding: 8px 12px;
    margin-bottom: 8px;
    border-radius: 6px;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 8px;
    background: rgba(255, 255, 255, 0.02);
}}
.subtask-item.done {{ border-left: 3px solid #10b981; }}
.subtask-item.pending {{ border-left: 3px solid var(--text-muted); color: var(--text-muted); }}
.subtask-item .badge {{ font-weight: bold; }}
.table-responsive {{ overflow-x: auto; }}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    text-align: left;
}}
th {{
    background: rgba(0, 0, 0, 0.2);
    color: var(--text-muted);
    font-weight: 600;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
}}
td {{
    padding: 10px 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}}
tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}
.action-tag {{
    background: rgba(56, 189, 248, 0.15);
    color: var(--primary);
    padding: 2px 8px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 12px;
}}
.engine-badge {{
    background: rgba(129, 140, 248, 0.15);
    color: var(--accent);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
}}
.pill {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 600;
}}
.pill-success {{ background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid #10b981; }}
.pill-warning {{ background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b; }}
.gallery-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
}}
.gallery-card {{
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
}}
.gallery-card-header {{
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(255, 255, 255, 0.03);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.gallery-card-body img {{
    width: 100%;
    height: auto;
    display: block;
}}
.gallery-card-footer {{
    padding: 6px 12px;
    font-size: 11px;
    color: var(--text-muted);
    border-top: 1px solid var(--border);
}}
.final-screenshot-container img {{
    max-width: 100%;
    border-radius: 8px;
    border: 1px solid var(--border);
}}
footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 12px;
    text-align: center;
}}
</style>
</head>
<body>
<div class="container">
<header>
    <div class="logo-title">
        <h1>⚡️ Laya Browser Agent</h1>
    </div>
    <span class="status-pill">{status_text}</span>
</header>

<div class="grid-2">
    <div class="card">
        <h2>📌 Executive Summary</h2>
        <div class="metric-row"><span class="metric-label">Target URL</span><span class="metric-val">{agent.target_url}</span></div>
        <div class="metric-row"><span class="metric-label">Final URL</span><span class="metric-val">{resolved_final_url}</span></div>
        <div class="metric-row"><span class="metric-label">Goal</span><span class="metric-val" style="font-family: inherit;">{agent.goal}</span></div>
        <div class="metric-row"><span class="metric-label">Steps Taken</span><span class="metric-val">{len(agent.steps_taken)} / {agent.max_steps}</span></div>
        <div class="metric-row"><span class="metric-label">Total Duration</span><span class="metric-val">{total_duration:.2f}s</span></div>
        <div class="metric-row"><span class="metric-label">Avg Decision Latency</span><span class="metric-val">{avg_step_ms:.1f}ms / step</span></div>
        <div class="metric-row"><span class="metric-label">Decision Model</span><span class="metric-val">{agent.model_id} (Metal GPU)</span></div>
        <div class="metric-row"><span class="metric-label">Local Planner</span><span class="metric-val">{agent.ollama_model}</span></div>
        <div class="metric-row"><span class="metric-label">Cloud API Cost</span><span class="metric-val highlight">$0.00 (Zero Cloud Tokens)</span></div>
        {finding_summary_html}
    </div>

    <div class="card">
        <h2>📋 Subtasks & Entities</h2>
        <ul class="subtask-list">
            {subtasks_html}
        </ul>
        <div style="margin-top: 16px;">
            <div class="metric-row"><span class="metric-label">Resolved Slots</span><span class="metric-val">{json.dumps(safe_slots) if safe_slots else "None"}</span></div>
        </div>
    </div>
</div>

{findings_html}

{telemetry_html}

<div class="card">
    <h2>⏱ Step-by-Step Action Audit Trail</h2>
    <div class="table-responsive">
    <table>
        <thead>
            <tr>
                <th>Step</th>
                <th>URL</th>
                <th>Subtask</th>
                <th>Action</th>
                <th>Parameters</th>
                <th>Engine</th>
                <th>Confidence</th>
                <th>Latency</th>
                <th>Result</th>
            </tr>
        </thead>
        <tbody>
            {audit_rows_html}
        </tbody>
    </table>
    </div>
</div>

{gallery_html}

{final_sc_html}

<footer>
    Generated autonomously by Laya Browser Agent | 100% Local Inference on Apple Silicon Metal GPU | {time.strftime('%Y-%m-%d %H:%M:%S')}
</footer>
</div>
</body>
</html>
"""
    if path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_content)
        except Exception:
            pass
    return html_content
