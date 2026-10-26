"""Modern Terminal UI and CLI for Laya Browser Agent."""

import argparse
import os
import re
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt

from .agent import AgentStepEvent, LayaAgent
from .browser import PlaywrightWebMcpDriver

console = Console()


def print_banner():
    """Renders the CLI banner."""
    banner = Text()
    banner.append("⚡️ LAYA BROWSER AGENT\n", style="bold cyan")
    banner.append("Zero-Cost, Sub-Second Autonomous Web Agent\n", style="bold white")
    banner.append("Universal WebMCP + Laya MLX Decision Policy | 100% Local & $0.00 API Cost", style="dim")
    console.print(Panel(banner, border_style="cyan", padding=(1, 2)))


def render_step_event(event: AgentStepEvent):
    """Renders real-time telemetry for an agent decision step."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold dim", width=16)
    table.add_column("Value")

    table.add_row("Page URL", f"[blue]{event.url}[/blue]")
    table.add_row("Page Title", event.title or "[dim]Untitled[/dim]")

    # Tools badge
    tool_names = [t.get("name") for t in event.available_tools]
    if tool_names:
        tools_str = " ".join([f"[reverse cyan] {name} [/reverse cyan]" for name in tool_names])
    else:
        tools_str = "[dim](No tools registered)[/dim]"
    table.add_row("WebMCP Tools", tools_str)

    # Decision & Confidence
    is_finish = event.chosen_action == "finish"
    action_style = "bold green" if is_finish else "bold magenta"
    action_label = f"[{action_style}]{event.chosen_action}[/{action_style}]"
    conf_pct = f"{event.confidence * 100:.1f}%"
    table.add_row("Active Subtask", f"[bold yellow]{event.subtask}[/bold yellow]")
    table.add_row("Action", f"{action_label} (Confidence: [bold]{conf_pct}[/bold], Latency: [yellow]{event.duration_ms:.1f}ms[/yellow])")
    table.add_row("Engine", f"[cyan]{event.engine_info}[/cyan]")

    if event.tool_params:
        table.add_row("Parameters", f"[cyan]{event.tool_params}[/cyan]")

    if event.tool_result is not None:
        result_preview = str(event.tool_result)
        if len(result_preview) > 120:
            result_preview = result_preview[:117] + "..."
        table.add_row("Result", f"[dim]{result_preview}[/dim]")

    step_title = f"Step {event.step} Execution" if not is_finish else f"Step {event.step} Complete"
    border = "green" if is_finish else "magenta"
    console.print(Panel(table, title=f"[{border}]{step_title}[/{border}]", border_style=border))


def extract_url_from_prompt(prompt: str) -> Optional[str]:
    """Infers target website URL from human prompt if explicitly mentioned."""
    non_web = {"md", "py", "sh", "txt", "pdf", "png", "jpg", "jpeg", "gif", "svg", "json", "html", "css", "js", "ts", "zip"}

    # 1. Direct http/https URLs or localhost
    m_direct = re.search(r"https?://[^\s'\"<>]+|localhost:\d*(?:/\S*)?", prompt, flags=re.IGNORECASE)
    if m_direct:
        url = m_direct.group(0).rstrip(".,;)")
        if not url.startswith("http"):
            url = f"http://{url}"
        return url

    # 2. Universal domain names with 2+ char valid TLD
    for m in re.finditer(r"\b(?:www\.)?[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)*\.([a-zA-Z]{2,})(?:/[^\s'\"<>]*)?\b", prompt):
        tld = m.group(1).lower()
        if tld not in non_web:
            candidate = m.group(0).rstrip(".,;)")
            return f"https://{candidate}"

    return None



def prompt_requests_report(prompt: str) -> bool:
    """Checks if the user's prompt explicitly asks for an execution report."""
    low = prompt.lower()
    report_keywords = (
        "report", "generate report", "create report", "execution report",
        "audit report", "summary report", "write report", "save report", "make report"
    )
    return any(k in low for k in report_keywords)


def cli_disambiguation_callback(prompt_msg: str, options: List[Dict[str, Any]]) -> Optional[str]:
    """Interactive CLI callback for resolving multiple variants (products, flights, sizes)."""
    console.print(f"\n[bold yellow]🔍 Multiple options found:[/bold yellow] [bold white]{prompt_msg}[/bold white]")
    for opt in options:
        idx = opt.get("index", 1)
        spec = opt.get("spec", "")
        price = opt.get("price", "")
        title = opt.get("title", "")
        if len(title) > 65:
            title = title[:62] + "..."
        price_str = f" [bold green]({price})[/bold green]" if price else ""
        spec_str = f" [cyan][{spec}][/cyan]" if spec and spec != "Standard Base" else ""
        console.print(f"  [bold cyan][{idx}][/bold cyan] {title}{spec_str}{price_str}")
    try:
        choice = Prompt.ask(
            "  [bold yellow]Select an option[/bold yellow] (number or Enter for default [1])",
            default="1",
        ).strip()
        return choice
    except (EOFError, KeyboardInterrupt):
        return "1"


def execute_session(
    goal: str,
    target_url: Optional[str] = None,
    headed: bool = True,
    steps: int = 15,
    model: str = "aac6fef/laya-mlx",
    ollama_model: Optional[str] = None,
    report: Optional[bool] = None,
    report_dir: str = "reports",
    report_file: Optional[str] = None,
    screenshot_mode: Optional[str] = None,
    session_context: Optional[Dict[str, Any]] = None,
    shared_driver: Optional[PlaywrightWebMcpDriver] = None,
):
    """Executes a single autonomous browser agent session with optional report and screenshot modes."""
    # 1. Target URL Resolution:
    # A. Explicit URL in prompt takes highest priority
    prompt_url = extract_url_from_prompt(goal)
    if prompt_url:
        target_url = prompt_url
    elif not target_url:
        # B. Active browser page or session context
        if shared_driver and shared_driver._page and not shared_driver._page.is_closed():
            target_url = shared_driver.get_active_url()
        elif session_context and session_context.get("active_url"):
            target_url = session_context["active_url"]
        elif session_context and session_context.get("target_url"):
            target_url = session_context["target_url"]
        else:
            target_url = "http://localhost:3000"

    # Instantiate Agent
    agent = LayaAgent(
        target_url=target_url,
        goal=goal,
        headed=headed,
        max_steps=steps,
        model_id=model,
        ollama_model=ollama_model,
        event_callback=render_step_event,
        generate_report=bool(report) if report is not None else False,
        report_dir=report_dir,
        report_file=report_file or os.path.join(report_dir, "execution_report.md"),
        driver=shared_driver,
        disambiguation_callback=cli_disambiguation_callback,
    )

    if session_context is not None and shared_driver and hasattr(shared_driver, "get_tabs"):
        try:
            if shared_driver._page and not shared_driver._page.is_closed():
                open_tabs = shared_driver.get_tabs()
                if open_tabs:
                    session_context["open_tabs"] = open_tabs
                    session_context["active_url"] = shared_driver.get_active_url()
                    session_context["active_title"] = shared_driver.get_page_title()
        except Exception:
            pass

    # Evict transient exploratory prompt cache entries from previous turns to prevent doom loop replays
    from laya_browser_agent.cache import global_prompt_cache
    global_prompt_cache.invalidate_recent_prompts(["screen_locator"], limit=10)

    # 1. Structure initial human input upfront using local LLM
    with console.status("[bold cyan]🧠 Organizing and structuring human request...[/bold cyan]"):
        plan = agent.prepare_plan(session_context=session_context)

    # 2. Interactive clarification loop if details are incomplete
    while plan.clarifying_question and plan.missing_details:
        console.print(f"\n[bold yellow]?[/bold yellow] [bold white]{plan.clarifying_question}[/bold white]")
        try:
            extra_info = Prompt.ask("  [bold cyan]Provide details[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Task cancelled.[/dim]")
            return
        if not extra_info or extra_info.lower() in ("cancel", "abort", "exit", "q", "quit", "skip", "nevermind"):
            console.print("[dim]Task cancelled by user.[/dim]")
            return

        # Bind user clarification response directly to the target missing slot
        first_missing = (plan.missing_details[0] if plan.missing_details else "").lower()
        if session_context is None:
            session_context = {}
        session_slots = session_context.setdefault("slots", {})

        if "password" in first_missing:
            session_slots["password"] = extra_info
            if plan.slots.get("username"):
                session_slots["username"] = plan.slots["username"]
            goal = f"{goal} (password: '{extra_info}')"
        elif "username" in first_missing and "credential" not in first_missing:
            session_slots["username"] = extra_info
            goal = f"{goal} (username: '{extra_info}')"
        elif "credential" in first_missing or "auth" in first_missing:
            if "/" in extra_info:
                parts = extra_info.split("/", 1)
                session_slots["username"] = parts[0].strip()
                session_slots["password"] = parts[1].strip()
                goal = f"{goal} ({session_slots['username']} / {session_slots['password']})"
            elif any(sep in extra_info for sep in (":", "=")):
                goal = f"{goal}. {extra_info}"
            else:
                goal = f"{goal}. credentials: {extra_info}"
        elif any(w in first_missing for w in ("status", "option", "dropdown", "filter")):
            session_slots["status"] = extra_info
            session_slots["option"] = extra_info
            goal = f"{goal} (status: '{extra_info}')"
        elif any(w in first_missing for w in ("quantity", "units", "items", "count")):
            try:
                session_slots["quantity"] = int(extra_info)
            except ValueError:
                session_slots["quantity"] = extra_info
            goal = f"{goal} (quantity: {extra_info})"
        elif any(w in first_missing for w in ("variant", "size", "spec", "color", "storage", "class")):
            session_slots["variant"] = extra_info
            session_slots["selected_variant"] = extra_info
            goal = f"{goal} (variant: '{extra_info}')"
        else:
            goal = f"{goal}. {extra_info}"

        agent.goal = goal
        found_url = extract_url_from_prompt(extra_info)
        if found_url:
            target_url = found_url
        agent.plan = None
        with console.status("[bold cyan]🧠 Updating plan with new details...[/bold cyan]"):
            plan = agent.prepare_plan(session_context=session_context)

    # 3. Report & Screenshot Configuration (Enabled by default)
    if report is None:
        report = True

    if screenshot_mode is None:
        if any(w in goal.lower() for w in ("every step", "each step", "all steps")):
            screenshot_mode = "every_step"
        else:
            screenshot_mode = "major_steps"

    effective_report_file = report_file or os.path.join(report_dir, "execution_report.md")
    agent.generate_report_enabled = bool(report)
    agent.screenshot_mode = screenshot_mode
    agent.report_file = effective_report_file

    if session_context is not None:
        session_context["report"] = report
        session_context["screenshot_mode"] = screenshot_mode

    # Display Session Configuration Panel
    config_table = Table(show_header=False, box=None)
    config_table.add_column("Key", style="bold cyan", width=18)
    config_table.add_column("Value")
    config_table.add_row("Target URL", target_url)
    config_table.add_row("Goal", goal)
    config_table.add_row("Browser Mode", "[green]Headed (Visible)[/green]" if headed else "[blue]Headless (Background)[/blue]")
    config_table.add_row("Decision Model", model)
    config_table.add_row("Local LLM", agent.ollama_model)
    config_table.add_row("Max Step Budget", str(steps))
    effective_html_file = effective_report_file.rsplit(".", 1)[0] + ".html"
    config_table.add_row("Execution Report", f"[green]Enabled[/green] ({effective_report_file} + {effective_html_file})" if report else "[dim]Disabled[/dim]")
    sc_mode_label = {
        "major_steps": "[green]Major steps only (Default)[/green]",
        "every_step": "[yellow]Every step[/yellow]",
        "none": "[dim]Disabled[/dim]",
    }.get(screenshot_mode, screenshot_mode)
    config_table.add_row("Screenshot Mode", sc_mode_label)
    console.print(Panel(config_table, title="[bold]Session Configuration[/bold]", border_style="blue"))

    # Display Organized Plan
    plan_table = Table(show_header=False, box=None, padding=(0, 1))
    plan_table.add_column("Key", style="bold green", width=18)
    plan_table.add_column("Value")
    plan_table.add_row("Structured Intent", f"[bold white]{plan.intent}[/bold white]")
    plan_table.add_row("Planner Engine", f"[cyan]{plan.source}[/cyan]")

    if plan.slots:
        display_slots = dict(plan.slots)
        if "password" in display_slots:
            display_slots["password"] = "••••••••"
        slots_str = ", ".join([f"[bold]{k}[/bold]={repr(v)}" for k, v in display_slots.items()])
        plan_table.add_row("Extracted Slots", slots_str)

    subtasks_str = "\n".join([f"  [bold cyan]{i+1}.[/bold cyan] {step}" for i, step in enumerate(plan.subtasks)])
    plan_table.add_row("Sequential Plan", subtasks_str)
    if plan.confident_subtasks and len(plan.confident_subtasks) < len(plan.subtasks):
        plan_table.add_row("Confident Horizon", f"[cyan]{len(plan.confident_subtasks)} initial steps (~100% confident)[/cyan]")
        plan_table.add_row("Discovery Mode", "[yellow]Progressive (Grounds remaining intent on live screen)[/yellow]")
    console.print(Panel(plan_table, title="[bold green]📋 Structured Execution Plan[/bold green]", border_style="green"))

    console.print("\n[bold yellow]🚀 Launching agent session...[/bold yellow]\n")

    try:
        receipt = agent.run()

        # Display Final Summary Receipt
        receipt_table = Table(show_header=False, box=None)
        receipt_table.add_column("Metric", style="bold white", width=22)
        receipt_table.add_column("Receipt")

        status_code = receipt.get("status")
        if status_code == "error":
            status_text = f"[bold red]FAILED (ERROR: {receipt.get('error', 'Execution Error')})[/bold red]"
        elif status_code == "unfulfillable":
            status_text = "[bold yellow]⚠️ UNFULFILLABLE[/bold yellow]"
        elif status_code == "incomplete":
            status_text = "[bold yellow]⚠️ INCOMPLETE[/bold yellow]"
        elif status_code == "success":
            status_text = "[bold green]✅ COMPLETED (SUCCESS)[/bold green]"
        else:
            status_text = "[bold yellow]⚠️ MAX STEPS REACHED[/bold yellow]"
        receipt_table.add_row("Status", status_text)
        receipt_table.add_row("Total Steps", str(receipt.get("steps", 0)))
        receipt_table.add_row("Execution Time", f"{receipt.get('total_time_seconds', 0.0):.2f}s")
        receipt_table.add_row("API Cost", "[bold green]$0.00 (Zero Cloud Calls)[/bold green]")
        if receipt.get("telemetry"):
            tel = receipt["telemetry"]
            receipt_table.add_row("Tokens (In / Out)", f"{tel.get('input_tokens', 0):,} in / {tel.get('output_tokens', 0):,} out ({tel.get('total_tokens', 0):,} total)")
            receipt_table.add_row("Cache Hits / Writes", f"{tel.get('cache_reads', 0)} hits / {tel.get('cache_writes', 0)} writes ({tel.get('latency_saved_ms', 0.0)/1000.0:.2f}s saved)")
        receipt_table.add_row("Extracted Slots", str(agent.extracted_slots))
        if receipt.get("extracted_data"):
            findings_str = "; ".join([f"[bold]{f.get('metric', 'Metric')}[/bold]: [green]{f.get('count', 0)}[/green]" for f in receipt["extracted_data"]])
            receipt_table.add_row("Extracted Data", findings_str)
        if receipt.get("report_file"):
            receipt_table.add_row("Execution Report", f"[cyan]{receipt.get('report_file')}[/cyan]")
        if receipt.get("screenshot"):
            receipt_table.add_row("Visual Proof", f"[cyan]{receipt.get('screenshot')}[/cyan]")

        console.print("\n")
        console.print(Panel(receipt_table, title="[bold green]Execution Summary Receipt[/bold green]", border_style="green"))

        # Persist session state across multi-turn conversational chat
        if session_context is not None:
            if agent.extracted_slots:
                session_context.setdefault("slots", {}).update(agent.extracted_slots)
            session_context["last_goal"] = goal
            current_active = agent.driver.get_active_url() if (agent.driver and agent.driver._page and not agent.driver._page.is_closed()) else target_url
            session_context["active_url"] = current_active
            session_context["target_url"] = current_active
            session_context.setdefault("history", []).append(goal)
            session_context["history"] = session_context["history"][-5:]

            # Persist rich turn outcome for subsequent turns
            open_tabs = []
            if agent.driver and hasattr(agent.driver, "get_tabs"):
                try:
                    open_tabs = agent.driver.get_tabs()
                except Exception:
                    pass
            turn_record = {
                "turn": len(session_context.get("turn_outcomes", [])) + 1,
                "goal": goal,
                "status": receipt.get("status", "completed"),
                "active_url": current_active,
                "open_tabs": open_tabs,
                "extracted_slots": dict(agent.extracted_slots),
                "extracted_data": list(receipt.get("extracted_data") or agent.extracted_data or []),
                "completed_subtasks": [s.subtask for s in agent.steps_taken if s.chosen_action != "action_blocked"],
                "action_blockers": [d for d in (receipt.get("extracted_data") or []) if d.get("status") == "unfulfillable"],
            }
            session_context.setdefault("turn_outcomes", []).append(turn_record)
            session_context["last_outcome"] = turn_record

    except KeyboardInterrupt:
        console.print("\n[bold red]Agent cancelled by user.[/bold red]")
    except Exception as e:
        console.print(f"\n[bold red]Error during agent execution:[/bold red] {e}")
    finally:
        agent.close()



def handle_conversational_basics(user_prompt: str, session_state: Dict[str, Any]) -> bool:
    """Interprets and responds to basic conversational inputs (greetings, help, status, reset, thanks).

    Returns True if handled conversationally without launching browser automation.
    """
    clean = re.sub(r"[^\w\s\?]", "", user_prompt.strip().lower())
    words = clean.split()
    if not words:
        return True

    # 1. Greetings
    GREETINGS = {
        "hi", "hello", "hey", "hiya", "howdy", "sup", "yo", "hola",
        "good morning", "good afternoon", "good evening", "greetings",
        "whats up", "what's up", "hey there", "hello there", "hi there",
    }
    is_greeting = clean in GREETINGS or (
        len(words) <= 2
        and words[0] in ("hi", "hello", "hey")
        and not any(w in words for w in ("login", "go", "cancel", "buy", "search", "click", "open", "find", "order", "orders", "product", "products"))
    )
    if is_greeting:
        known_user = session_state.get("slots", {}).get("username")
        user_greeting = f", {known_user}" if known_user else ""
        content = (
            f"[bold cyan]⚡️ Hello{user_greeting}![/bold cyan] I am [bold white]Laya Browser Agent[/bold white].\n\n"
            "Tell me what task you would like to run on the web. For example:\n"
            "  • [green]Go to https://news.ycombinator.com and check the top 3 stories[/green]\n"
            "  • [green]Go to https://www.amazon.in and search for laptops[/green]\n"
            "  • [green]login as user (user/User@123), go to my account, update profile and logout[/green]\n\n"
            "[dim]Quick commands: [bold]help[/bold] (show commands), [bold]status[/bold] (session info), [bold]reset[/bold] (clear session), [bold]exit[/bold] (quit)[/dim]"
        )
        console.print(Panel(content, title="[bold cyan]Laya Assistant[/bold cyan]", border_style="cyan"))
        return True

    # 2. Help / Capabilities
    HELP_QUERIES = {"help", "?", "what can you do", "commands", "usage", "options", "capabilities", "info"}
    if clean in HELP_QUERIES or (len(words) <= 3 and any(w in clean for w in ("what can you do", "help me", "how to use"))):
        help_table = Table(show_header=True, header_style="bold cyan", box=None)
        help_table.add_column("Command / Action", style="bold green", width=26)
        help_table.add_column("Description")
        help_table.add_row("<Any web goal>", "Execute automation task (e.g. login, shop, cancel, approve, filter, navigate)")
        help_table.add_row("status / session", "Display active authenticated user, current route, and remembered slots")
        help_table.add_row("cache / cache stats", "Display local prompt & context cache hit statistics and saved latency")
        help_table.add_row("clear cache", "Clear persistent local prompt cache")
        help_table.add_row("reset / clear", "Clear current session memory (credentials, history, route anchors)")
        help_table.add_row("exit / quit / q", "Exit Laya Browser Agent CLI")
        console.print(Panel(help_table, title="[bold cyan]📖 Available Commands & Usage[/bold cyan]", border_style="cyan"))
        return True

    # 3. Status / Session Info
    STATUS_QUERIES = {"status", "session", "whoami", "state", "info session", "session state"}
    if clean in STATUS_QUERIES:
        slots = session_state.get("slots", {})
        known_user = slots.get("username", "None (Unauthenticated)")
        target_url = session_state.get("active_url") or session_state.get("target_url") or "None (No active site)"
        history = session_state.get("history", [])
        last_goal = session_state.get("last_goal", "None")

        status_table = Table(show_header=False, box=None)
        status_table.add_column("Key", style="bold cyan", width=18)
        status_table.add_column("Value")
        status_table.add_row("Target URL", target_url)
        status_table.add_row("Authenticated User", f"[green]{known_user}[/green]" if known_user != "None (Unauthenticated)" else "[dim]None[/dim]")
        status_table.add_row("Last Goal", last_goal)
        status_table.add_row("Recent Turns", str(len(history)))
        if slots:
            disp_slots = {k: ("••••••••" if "pass" in k.lower() else v) for k, v in slots.items()}
            status_table.add_row("Saved Slots", str(disp_slots))
        console.print(Panel(status_table, title="[bold green]Current Session State[/bold green]", border_style="green"))
        return True

    # 4. Reset / Clear Session Memory
    RESET_QUERIES = {"reset", "clear", "restart", "new session", "clear state", "reset state"}
    if clean in RESET_QUERIES:
        session_state["slots"].clear()
        session_state["history"].clear()
        session_state["last_goal"] = ""
        console.print("[bold green]✓ Session state and credentials reset cleanly.[/bold green] Ready for a new workflow.\n")
        return True

    # 5. Polite Acknowledgements / Pleasantries
    THANKS_QUERIES = {"thanks", "thank you", "thx", "ok", "okay", "cool", "great", "awesome", "nice", "got it", "perfect", "bye"}
    if clean in THANKS_QUERIES:
        console.print("[cyan]You're welcome! What would you like to automate next?[/cyan]\n")
        return True

    # 6. Show / View Report
    if clean in ("show report", "view report", "report", "last report", "open report"):
        report_path = os.path.join("reports", "execution_report.md")
        if os.path.exists(report_path):
            console.print(f"[bold green]📄 Latest Execution Report:[/bold green] [cyan]{report_path}[/cyan]\n")
            try:
                with open(report_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                    summary_lines = [l for l in lines if l.startswith("|")][:12]
                    if summary_lines:
                        console.print("\n".join(summary_lines))
            except Exception:
                pass
        else:
            console.print("[dim]No execution report found yet. Run an automation task first.[/dim]\n")
        return True

    # 7. Local Prompt & Context Cache Management
    if clean in ("cache", "show cache", "cache stats", "cache status", "prompt cache"):
        from laya_browser_agent.cache import global_prompt_cache
        stats = global_prompt_cache.get_stats()
        c_table = Table(title="⚡️ Laya Local Prompt & Context Cache", border_style="cyan")
        c_table.add_column("Metric", style="bold cyan")
        c_table.add_column("Value")
        c_table.add_row("Total Cached Prompts", str(stats.get("total_cached_prompts", 0)))
        c_table.add_row("Prompt Cache Hits", f"[green]{stats.get('total_cache_hits', 0)}[/green]")
        c_table.add_row("Cached Subtasks (Macros)", str(stats.get("total_cached_subtasks", 0)))
        c_table.add_row("Subtask Macro Hits", f"[green]{stats.get('total_subtask_hits', 0)}[/green]")
        c_table.add_row("Estimated Latency Saved", f"[green]{stats.get('estimated_time_saved_seconds', 0.0)}s[/green]")
        c_table.add_row("Storage Engine", f"SQLite WAL ({stats.get('storage_path')})")
        console.print(c_table)
        return True

    if clean in ("clear cache", "reset cache", "purge cache"):
        from laya_browser_agent.cache import global_prompt_cache
        global_prompt_cache.clear()
        console.print("[bold green]✓ Local prompt cache cleared cleanly.[/bold green]\n")
        return True

    return False


def main():
    """CLI entrypoint supporting conversational chat mode or direct flag execution."""
    parser = argparse.ArgumentParser(
        description="Laya Browser Agent: Autonomous WebMCP agent for any web application"
    )
    parser.add_argument("--url", type=str, default=None, help="Target initial website URL")
    parser.add_argument("--task", "--goal", dest="task", type=str, default=None, help="Goal or task description")
    parser.add_argument("--headed", dest="headed", action="store_true", default=None, help="Run browser in visible (headed) mode")
    parser.add_argument("--headless", dest="headless", action="store_true", default=None, help="Run browser in headless (background) mode")
    parser.add_argument("--steps", type=int, default=25, help="Maximum number of decision steps (default: 25)")
    parser.add_argument("--model", type=str, default="aac6fef/laya-mlx", help="Hugging Face model ID for Laya MLX")
    parser.add_argument("--ollama-model", type=str, default=None, help="Local Ollama model (default: $OLLAMA_MODEL or qwen2.5:3b)")
    parser.add_argument("--report", dest="report", action="store_true", default=None, help="Generate detailed Markdown execution report")
    parser.add_argument("--no-report", dest="report", action="store_false", help="Disable generating execution report")
    parser.add_argument("--screenshot-mode", type=str, choices=["major_steps", "every_step", "none"], default=None, help="Screenshot capture frequency (default: major_steps)")
    parser.add_argument("--report-dir", type=str, default="reports", help="Directory for execution reports and visual artifacts (default: reports)")
    parser.add_argument("--report-file", type=str, default=None, help="Path to write execution report (default: <report-dir>/execution_report.md)")

    args = parser.parse_args()

    print_banner()

    headed = False if args.headless else True

    session_state: Dict[str, Any] = {
        "target_url": args.url or "",
        "active_url": args.url or "",
        "slots": {},
        "history": [],
        "last_goal": "",
    }

    # 1. Direct one-shot mode if --task is passed
    if args.task:
        if handle_conversational_basics(args.task, session_state):
            return
        execute_session(
            goal=args.task,
            target_url=args.url,
            headed=headed,
            steps=args.steps,
            model=args.model,
            ollama_model=args.ollama_model,
            report=args.report,
            report_dir=args.report_dir,
            report_file=args.report_file,
            screenshot_mode=args.screenshot_mode,
            session_context=session_state,
        )
        return

    # 2. Conversational Chat Mode - Persistent Browser Context across turns
    console.print("[bold cyan]Ready for your prompt.[/bold cyan] Type what you want to do on the web.")
    console.print("[dim]Commands: 'help' (usage), 'status' (session), 'retry' (rerun), 'reset' (clear), 'exit' (quit).[/dim]\n")

    shared_driver = PlaywrightWebMcpDriver(headed=headed)
    try:
        while True:
            try:
                user_prompt = console.input("[bold green]laya > [/bold green]").strip()
                if not user_prompt:
                    continue
                if user_prompt.lower() in ("exit", "quit", "q", ":q"):
                    console.print("[cyan]Goodbye![/cyan]")
                    break

                # Handle automatic re-try / rerun of previous task
                if user_prompt.lower() in ("retry", "try again", "redo", "run again", "run it again"):
                    last_goal = session_state.get("last_goal")
                    if last_goal:
                        console.print(f"[bold cyan]⚡️ Retrying previous goal:[/bold cyan] [italic]{last_goal}[/italic]\n")
                        user_prompt = last_goal
                    else:
                        console.print("[dim]No previous task to retry.[/dim]\n")
                        continue

                # Handle basic conversational prompts (greetings, help, status, reset, thanks)
                if handle_conversational_basics(user_prompt, session_state):
                    continue

                # Dialogue Intent Classification & Automated Cache Self-Healing
                from .dialogue import classify_dialogue_turn
                turn = classify_dialogue_turn(
                    user_prompt,
                    session_context=session_state,
                    ollama_model=session_state.get("ollama_model") or args.ollama_model,
                )

                if turn.category == "query_status":
                    handle_conversational_basics("report", session_state)
                    continue
                elif turn.category == "conversational_basic":
                    handle_conversational_basics(user_prompt, session_state)
                    continue
                elif turn.category == "feedback_correction":
                    console.print(f"[bold yellow]⚠️  Outcome Discrepancy / Feedback Detected:[/bold yellow] [dim]{turn.reason}[/dim]")
                    if turn.evicted_macros > 0 or turn.evicted_prompts > 0:
                        console.print(f"[dim]⚡️ Cache Self-Healing: Evicted {turn.evicted_macros} route macros and {turn.evicted_prompts} recent prompt caches.[/dim]")
                    console.print("[cyan]🔍 Entering Live Inspection & Remediation Mode...[/cyan]\n")

                execute_session(
                    goal=user_prompt,
                    target_url=session_state.get("active_url") or session_state.get("target_url") or args.url,
                    headed=headed,
                    steps=args.steps,
                    model=args.model,
                    ollama_model=args.ollama_model,
                    report=args.report,
                    report_dir=args.report_dir,
                    report_file=args.report_file,
                    screenshot_mode=args.screenshot_mode,
                    session_context=session_state,
                    shared_driver=shared_driver,
                )
                console.print("\n[dim]Ready for next task.[/dim]\n")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[cyan]Session ended.[/cyan]")
                break
    finally:
        if shared_driver:
            shared_driver.close()


if __name__ == "__main__":
    main()

