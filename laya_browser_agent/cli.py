"""Modern Terminal UI and CLI for Laya Browser Agent."""

import argparse
import sys
import time
from typing import Any, Dict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt

from .agent import AgentStepEvent, LayaAgent

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
    table.add_row("Laya Decision", f"{action_label} (Confidence: [bold]{conf_pct}[/bold], Latency: [yellow]{event.duration_ms:.1f}ms[/yellow])")

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


def main():
    """CLI entrypoint supporting direct flags or guided interactive mode."""
    parser = argparse.ArgumentParser(
        description="Laya Browser Agent: Autonomous WebMCP agent for any web application"
    )
    parser.add_argument("--url", type=str, default=None, help="Target initial website URL")
    parser.add_argument("--task", "--goal", dest="task", type=str, default=None, help="Goal or task description")
    parser.add_argument("--headed", dest="headed", action="store_true", default=None, help="Run browser in visible (headed) mode")
    parser.add_argument("--headless", dest="headless", action="store_true", default=None, help="Run browser in headless (background) mode")
    parser.add_argument("--steps", type=int, default=15, help="Maximum number of decision steps (default: 15)")
    parser.add_argument("--model", type=str, default="aac6fef/laya-mlx", help="Hugging Face model ID for Laya MLX")
    parser.add_argument("--ollama-model", type=str, default="llama3.1:8b", help="Local Ollama model for parameter extraction")

    args = parser.parse_args()

    print_banner()

    # 1. Determine URL
    target_url = args.url
    if not target_url:
        target_url = Prompt.ask(
            "[bold cyan]?[/bold cyan] Enter Target URL",
            default="http://localhost:3000/login",
        )

    # 2. Determine Task / Goal
    goal = args.task
    if not goal:
        goal = Prompt.ask(
            "[bold cyan]?[/bold cyan] Enter Agent Goal / Task",
            default="Login as user (user/User@123), search products, buy 1 unit, and logout",
        )

    # 3. Determine Headed vs Headless Mode
    if args.headless:
        headed = False
    elif args.headed:
        headed = True
    else:
        # Interactive prompt for browser visibility mode
        console.print("\n[bold]Select Browser Mode:[/bold]")
        console.print("  [1] [bold green]Headed[/bold green] (visible browser window - recommended for watching agent)")
        console.print("  [2] [bold blue]Headless[/bold blue] (runs in background without browser window)")
        choice = Prompt.ask("Choose mode", choices=["1", "2"], default="1")
        headed = (choice == "1")

    # Display Task Configuration Panel
    config_table = Table(show_header=False, box=None)
    config_table.add_column("Key", style="bold cyan", width=18)
    config_table.add_column("Value")
    config_table.add_row("Target URL", target_url)
    config_table.add_row("Goal", goal)
    config_table.add_row("Browser Mode", "[green]Headed (Visible)[/green]" if headed else "[blue]Headless (Background)[/blue]")
    config_table.add_row("Decision Model", args.model)
    config_table.add_row("Max Step Budget", str(args.steps))
    console.print(Panel(config_table, title="[bold]Session Configuration[/bold]", border_style="blue"))

    # Instantiate Agent
    agent = LayaAgent(
        target_url=target_url,
        goal=goal,
        headed=headed,
        max_steps=args.steps,
        model_id=args.model,
        ollama_model=args.ollama_model,
        event_callback=render_step_event,
    )

    console.print("\n[bold yellow]🚀 Launching agent session...[/bold yellow]\n")

    try:
        receipt = agent.run()

        # Display Final Summary Receipt
        receipt_table = Table(show_header=False, box=None)
        receipt_table.add_column("Metric", style="bold white", width=22)
        receipt_table.add_column("Receipt")

        is_success = receipt.get("status") == "success"
        status_text = "[bold green]COMPLETED (SUCCESS)[/bold green]" if is_success else "[bold yellow]MAX STEPS REACHED[/bold yellow]"
        receipt_table.add_row("Status", status_text)
        receipt_table.add_row("Total Steps", str(receipt.get("steps", 0)))
        receipt_table.add_row("Execution Time", f"{receipt.get('total_time_seconds', 0.0):.2f}s")
        receipt_table.add_row("API Cost", "[bold green]$0.00 (Zero Cloud Calls)[/bold green]")
        receipt_table.add_row("Extracted Slots", str(agent.extracted_slots))

        console.print("\n")
        console.print(Panel(receipt_table, title="[bold green]Execution Summary Receipt[/bold green]", border_style="green"))

        if headed:
            console.print("\n[dim]Press Enter to close browser and exit...[/dim]")
            input()
            agent.close()

    except KeyboardInterrupt:
        console.print("\n[bold red]Agent cancelled by user.[/bold red]")
        agent.close()
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Error during agent execution:[/bold red] {e}")
        agent.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
