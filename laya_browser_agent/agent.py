"""Universal Autonomous Browser Agent powered by Laya MLX decision models, Screen Perception, and WebMCP."""

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .browser import PlaywrightWebMcpDriver
from .extractor import SlotExtractor, StructuredPlan
from .laya_engine import LayaEngine, normalize_route_key


@dataclass
class AgentStepEvent:
    """Telemetry event emitted on each step of the agent execution."""

    step: int
    subtask: str
    url: str
    title: str
    available_tools: List[Dict[str, Any]]
    chosen_action: str
    confidence: float
    probabilities: Dict[str, float]
    tool_params: Dict[str, Any]
    tool_result: Any
    duration_ms: float
    engine_info: str = "Laya MLX Fast Policy"


class LayaAgent:
    """
    Universal autonomous browser agent.
    Combines sub-15ms Laya MLX decision policy, direct screen coordinate actuation,
    and WebMCP semantic tool execution.
    """

    def __init__(
        self,
        target_url: str,
        goal: str,
        headed: bool = True,
        max_steps: int = 15,
        model_id: str = "aac6fef/laya-mlx",
        ollama_model: str = "llama3.1:8b",
        event_callback: Optional[Callable[[AgentStepEvent], None]] = None,
        generate_report: bool = True,
        report_dir: str = "reports",
        report_file: Optional[str] = None,
    ):
        self.target_url = target_url
        self.goal = goal
        self.headed = bool(headed)
        self.max_steps = max_steps
        self.model_id = model_id
        self.ollama_model = ollama_model
        self.event_callback = event_callback
        self.generate_report_enabled = bool(generate_report)
        self.report_dir = report_dir
        self.report_file = report_file or os.path.join(report_dir, "execution_report.md")

        self.driver: Optional[PlaywrightWebMcpDriver] = None
        self.engine: Optional[LayaEngine] = None
        self.extractor: SlotExtractor = SlotExtractor(ollama_model=self.ollama_model)

        self.plan: Optional[StructuredPlan] = None
        self.extracted_slots: Dict[str, Any] = {}
        self.subtasks: List[str] = []
        self.current_subtask_idx = 0
        self.history: List[str] = []
        self.last_result: Any = None
        self.steps_taken: List[AgentStepEvent] = []

    def prepare_plan(self) -> StructuredPlan:
        """Structures and organizes the human input into slots and sequential subtasks."""
        if not self.plan:
            self.plan = self.extractor.extract(self.goal, initial_url=self.target_url)
            self.extracted_slots = self.plan.slots
            self.subtasks = self.plan.subtasks
        return self.plan

    def run(self) -> Dict[str, Any]:
        """Runs the autonomous decision and action loop."""
        start_time = time.time()

        if not self.engine:
            self.engine = LayaEngine(
                model_id=self.model_id,
                ollama_model=self.ollama_model,
            )
        if not self.driver:
            self.driver = PlaywrightWebMcpDriver(headed=self.headed)

        # 1. Structure the initial human request into slots and sequential subtasks
        self.prepare_plan()

        # 2. Launch browser at target URL (headed or headless)
        self.driver.start(self.target_url)

        failed_actions: set[str] = set()
        last_url: str = ""

        try:
            for step_num in range(1, self.max_steps + 1):
                step_start = time.time()

                # Settle page and discover registered tools & visible screen elements
                self.driver.wait_for_settled(timeout_ms=2500)
                current_url = self.driver.get_active_url()
                page_title = self.driver.get_page_title()
                tools = self.driver.discover_tools()
                screen_elements = self.driver.get_screen_elements()

                # Reset failed actions if page navigated
                if current_url != last_url:
                    failed_actions.clear()
                    last_url = current_url

                # Skip subtasks that describe navigating to where the browser already is, or passive verification steps
                while self.current_subtask_idx < len(self.subtasks):
                    st = self.subtasks[self.current_subtask_idx].lower()
                    # Skip passive verification subtasks that have no concrete browser action
                    if any(st.startswith(v) for v in ["verify", "confirm", "check", "ensure"]):
                        self.current_subtask_idx += 1
                        continue

                    # Never skip login subtasks on unauthenticated login page
                    if any(w in st for w in ("login", "signin", "authenticate", "credential")):
                        break

                    is_nav_only = (
                        any(w in st for w in ["navigate", "go to", "got to", "goto", "visit", "head to"])
                        or (any(w in st for w in ["tab", "link", "page", "section"]) and any(v in st for v in ["click", "select", "press"]))
                    ) and not ("enter" in st or "credential" in st or "password" in st or "form" in st or "fill" in st or "type" in st or "submit" in st)
                    
                    route_tokens = [t for t in current_url.split("?")[0].split("/") if len(t) > 2 and t not in ("http:", "https:", "localhost:3000")]
                    is_already_on_page = any(tok.lower() in st for tok in route_tokens)
                    if is_nav_only and is_already_on_page:
                        self.current_subtask_idx += 1
                    else:
                        break

                # Active subtask
                if self.current_subtask_idx < len(self.subtasks):
                    active_subtask = self.subtasks[self.current_subtask_idx]
                else:
                    active_subtask = "Finish goal"

                # 3. Laya MLX Decision Policy with Screen Perception and Dynamic Intent Decomposition
                chosen, confidence, fallback_params, engine_info = self.engine.decide_next_action(
                    subtask=active_subtask,
                    goal=self.goal,
                    current_url=current_url,
                    available_tools=tools,
                    screen_elements=screen_elements,
                    recent_history=self.history,
                    last_result=self.last_result,
                    extracted_slots=self.extracted_slots,
                    excluded_actions=list(failed_actions),
                )

                # Goal finished
                if chosen == "finish" or active_subtask == "Finish goal":
                    duration = (time.time() - step_start) * 1000
                    event = AgentStepEvent(
                        step=step_num,
                        subtask=active_subtask,
                        url=current_url,
                        title=page_title,
                        available_tools=tools,
                        chosen_action="finish",
                        confidence=confidence,
                        probabilities={},
                        tool_params={},
                        tool_result="Goal completed successfully",
                        duration_ms=duration,
                        engine_info=engine_info,
                    )
                    self.steps_taken.append(event)
                    if self.event_callback:
                        self.event_callback(event)
                    break

                # 4. Action Actuation (WebMCP tool, Form Input, or Screen Coordinate Click)
                result = None
                params: Dict[str, Any] = {}

                tool_name = chosen.replace("tool_", "")
                is_tool = chosen.startswith("tool_") or any(t.get("name") == tool_name for t in tools)

                if is_tool:
                    chosen = f"tool_{tool_name}"
                    tool_def = next((t for t in tools if t.get("name") == tool_name), None)
                    params = self._resolve_tool_params(
                        tool_def, screen_elements=screen_elements, fallback_params=fallback_params
                    )

                    try:
                        raw_result = self.driver.call_tool(tool_name, params)
                        result = self._parse_tool_result(raw_result)
                    except Exception as e:
                        # Fallback: if tool execution failed, mark failed action and attempt clicking matching element
                        result = {"tool_error": str(e)}
                        failed_actions.add(chosen)
                        failed_actions.add(tool_name)
                        if self.engine:
                            self.engine.invalidate_cache(current_url)
                        match_el = self._find_matching_screen_element(tool_name, screen_elements)
                        if match_el:
                            self.driver.click_coordinate(match_el["x"], match_el["y"])
                            result["fallback"] = f"Clicked '{match_el['text']}' at ({match_el['x']}, {match_el['y']})"

                elif chosen.startswith("input_"):
                    # Support for web applications without WebMCP (form inputs)
                    field = fallback_params.get("_field") or chosen.replace("input_", "")
                    input_type = fallback_params.get("_input_type", "text")
                    coords = fallback_params.get("_coords")
                    
                    target_x, target_y = None, None
                    if coords and len(coords) >= 2 and coords[0] is not None and coords[1] is not None:
                        try:
                            target_x, target_y = float(coords[0]), float(coords[1])
                        except (ValueError, TypeError):
                            target_x, target_y = None, None

                    if target_x is None or target_y is None:
                        match_el = next((e for e in screen_elements if field.lower() in e.get("text", "").lower() or field.lower() in e.get("name", "").lower()), None)
                        if match_el and match_el.get("x") is not None and match_el.get("y") is not None:
                            target_x, target_y = float(match_el["x"]), float(match_el["y"])

                    # Resolve input value from slots or active subtask
                    val = None
                    clean_f = field.lower().replace("_", "").replace("-", "")
                    if any(u in clean_f for u in ["user", "login", "email", "account"]):
                        val = self.extracted_slots.get("username")
                    elif any(p in clean_f for p in ["pass", "pwd", "secret"]):
                        val = self.extracted_slots.get("password")
                    elif any(s in clean_f for s in ["search", "query", "find", "product", "sku"]):
                        val = self.extracted_slots.get("search") or self.extracted_slots.get("query")
                    elif any(q in clean_f for q in ["quantity", "qty", "count"]):
                        val = self.extracted_slots.get("quantity", 1)

                    if val is None:
                        for sk, sv in self.extracted_slots.items():
                            if sk.lower() in clean_f or clean_f in sk.lower():
                                val = sv
                                break

                    if val is None:
                        match_q = re.search(r"['\"](.*?)['\"]", active_subtask)
                        if match_q:
                            val = match_q.group(1)

                    if val is not None and target_x is not None and target_y is not None:
                        is_search = "search" in clean_f or "search" in active_subtask.lower()
                        self.driver.fill_input_at(target_x, target_y, str(val), press_enter=is_search)
                        result = {"filled_input": field, "value": "••••••••" if "pass" in clean_f else str(val)}
                        params = {"field": field, "x": target_x, "y": target_y}
                    else:
                        result = {"input_error": f"Could not resolve value or coordinate for input '{field}'"}

                elif chosen.startswith("click_"):
                    label = chosen.replace("click_", "")
                    clean_label = label.replace("_", " ").lower().strip()
                    tok_match = re.search(r"\b([a-f0-9]{6,36})\b", clean_label)
                    tok = tok_match.group(1) if tok_match else ""
                    if not tok and self.extracted_slots:
                        tok = str(self.extracted_slots.get("orderId") or self.extracted_slots.get("order_id") or self.extracted_slots.get("id") or "")

                    coords = fallback_params.get("_coords") if fallback_params else None
                    target_x, target_y = None, None
                    if coords and isinstance(coords, (tuple, list)) and len(coords) >= 2 and coords[0] is not None and coords[1] is not None:
                        try:
                            target_x, target_y = float(coords[0]), float(coords[1])
                        except (ValueError, TypeError):
                            target_x, target_y = None, None

                    if target_x is None or target_y is None:
                        match_el = next((e for e in screen_elements if clean_label in e.get("text", "").lower()), None)
                        if not match_el and tok:
                            match_el = next((e for e in screen_elements if tok in e.get("text", "").lower()), None)
                        if not match_el:
                            match_el = next((e for e in screen_elements if e.get("text", "").lower() in clean_label), None)

                        if match_el and match_el.get("x") is not None and match_el.get("y") is not None:
                            target_x, target_y = float(match_el["x"]), float(match_el["y"])
                        elif tok:
                            scrolled = self.driver.scroll_into_view_and_click(tok)
                            if scrolled:
                                target_x, target_y = float(scrolled[0]), float(scrolled[1])

                        if target_x is None or target_y is None:
                            if screen_elements and screen_elements[0].get("x") is not None:
                                target_x, target_y = float(screen_elements[0]["x"]), float(screen_elements[0]["y"])
                            else:
                                target_x, target_y = 500.0, 300.0

                    self.driver.click_coordinate(target_x, target_y, query=tok)
                    result = {"clicked_coordinate": (target_x, target_y)}
                    params = {"x": target_x, "y": target_y}

                self.last_result = result
                self.history.append(f"{chosen}({json.dumps(params)})")

                # Check for errors and invalidate cache if needed
                if isinstance(result, dict) and any(k in result for k in ("tool_error", "input_error")):
                    failed_actions.add(chosen)
                    if self.engine:
                        self.engine.invalidate_cache(current_url)

                # Route settling after actuation
                time.sleep(0.5)
                self.driver.wait_for_settled(timeout_ms=2000)
                new_url = self.driver.get_active_url()

                # Check if login subtask actually succeeded (navigated away from /login)
                is_login_subtask = any(w in active_subtask.lower() for w in ("login", "signin", "authenticate"))
                if is_login_subtask and new_url.rstrip("/").endswith("/login"):
                    try:
                        self.driver.page.wait_for_url(lambda u: not u.rstrip("/").endswith("/login"), timeout=3000)
                        new_url = self.driver.get_active_url()
                    except Exception:
                        pass

                if is_login_subtask and new_url.rstrip("/").endswith("/login"):
                    failed_actions.add(chosen)
                    if self.engine:
                        self.engine.invalidate_cache(current_url)
                    # Do not advance subtask since we are still on login page
                    continue

                # Advance subtask only if action succeeded or produced a valid fallback
                if not (isinstance(result, dict) and "tool_error" in result and "fallback" not in result):
                    # Intermediate navigation check: if we clicked a navigation link/tool to reach the section needed
                    coords = params.get("_coords") or (params.get("x"), params.get("y"))
                    cx = coords[0] if (coords and len(coords) >= 2) else None
                    cy = coords[1] if (coords and len(coords) >= 2) else None

                    cand_meta = {}
                    if self.engine and current_url:
                        cand_meta = self.engine.site_cache.get(normalize_route_key(current_url), {}).get(chosen, {})

                    is_nav_step = (
                        chosen == "tool_navigate_to_page"
                        or cand_meta.get("type") == "navigate"
                        or bool(cand_meta.get("path"))
                        or bool(params.get("path"))
                        or any(
                            bool(el.get("href"))
                            for el in screen_elements
                            if (cx is not None and cy is not None and abs(el.get("x", 0) - cx) < 5 and abs(el.get("y", 0) - cy) < 5)
                            or (chosen.startswith("click_") and el.get("text", "").lower() in chosen.lower())
                        )
                    )
                    is_pure_nav_subtask = any(p in active_subtask.lower() for p in ("go to", "navigate to", "visit", "open page", "head to"))
                    is_login_action = "login" in chosen.lower()
                    subtask_is_login = any(w in active_subtask.lower() for w in ("login", "signin", "authenticate", "credential"))

                    operational_keywords = (
                        "buy", "purchase", "place_order",
                        "search", "find", "query", "catalog",
                        "cancel", "delete", "remove",
                        "approve", "reject",
                        "update", "edit", "modify",
                        "fill", "enter", "type", "submit",
                        "login", "signin", "authenticate",
                        "logout", "signout", "log out", "sign out",
                    )
                    is_operational_subtask = not is_pure_nav_subtask and any(w in active_subtask.lower() for w in operational_keywords)

                    is_batch_subtask = any(w in active_subtask.lower().split() for w in ("all", "every", "each"))

                    if is_login_action and not subtask_is_login:
                        # Retain active operational subtask for post-login execution
                        pass
                    elif is_nav_step and is_operational_subtask:
                        # Navigation is only an intermediate move to reach the page where the operation lives;
                        # never advance operational subtasks on mere navigation clicks!
                        pass
                    elif is_batch_subtask:
                        # For batch subtasks (e.g. "cancel all open orders"), check if more actionable elements remain on screen
                        if self.engine:
                            self.engine.invalidate_cache(new_url)
                        fresh_elements = self.driver.get_screen_elements()
                        action_verb = next((v for v in ("cancel", "delete", "remove", "approve", "reject") if v in active_subtask.lower()), "")
                        more_exist = False
                        if action_verb:
                            more_exist = any(
                                action_verb in el.get("text", "").lower()
                                for el in fresh_elements
                                if el.get("tag") in ("button", "a")
                            )
                        if more_exist:
                            # Keep subtask active to process next item in batch
                            pass
                        else:
                            self.current_subtask_idx += 1
                    else:
                        self.current_subtask_idx += 1

                duration = (time.time() - step_start) * 1000
                event = AgentStepEvent(
                    step=step_num,
                    subtask=active_subtask,
                    url=current_url,
                    title=page_title,
                    available_tools=tools,
                    chosen_action=chosen,
                    confidence=confidence,
                    probabilities={},
                    tool_params=params,
                    tool_result=result,
                    duration_ms=duration,
                    engine_info=engine_info,
                )
                self.steps_taken.append(event)
                if self.event_callback:
                    self.event_callback(event)

            total_elapsed = time.time() - start_time
            is_success = bool(self.steps_taken and self.steps_taken[-1].chosen_action == "finish")

            final_url = self.driver.get_active_url() if self.driver else self.target_url
            report_out_path = self.report_file
            report_out_dir = os.path.dirname(report_out_path) or self.report_dir
            os.makedirs(report_out_dir, exist_ok=True)

            screenshot_path = os.path.join(report_out_dir, "final_execution_state.png")
            screenshot_taken = False
            try:
                if self.driver and self.driver._page:
                    self.driver._page.screenshot(path=screenshot_path)
                    screenshot_taken = True
            except Exception:
                screenshot_taken = False

            report_file_written = None
            if self.generate_report_enabled:
                self.generate_report(
                    report_out_path,
                    final_screenshot=screenshot_path if screenshot_taken else None,
                    final_url=final_url,
                )
                report_file_written = report_out_path

            return {
                "status": "success" if is_success else "max_steps_reached",
                "steps": len(self.steps_taken),
                "total_time_seconds": total_elapsed,
                "history": self.history,
                "last_result": self.last_result,
                "report_file": report_file_written,
                "screenshot": screenshot_path if screenshot_taken else None,
            }

        finally:
            if not self.headed:
                self.driver.close()

    def close(self):
        """Closes browser driver session."""
        if self.driver:
            self.driver.close()

    def generate_report(
        self,
        output_path: Optional[str] = None,
        final_screenshot: Optional[str] = None,
        final_url: Optional[str] = None,
    ) -> str:
        """Generates a detailed execution report in Markdown format."""
        path = output_path or self.report_file
        total_duration = sum(s.duration_ms for s in self.steps_taken) / 1000.0
        avg_step_ms = (sum(s.duration_ms for s in self.steps_taken) / len(self.steps_taken)) if self.steps_taken else 0.0
        is_success = bool(self.steps_taken and self.steps_taken[-1].chosen_action == "finish")

        status_badge = "✅ COMPLETED (SUCCESS)" if is_success else "⚠️ MAX STEPS REACHED"

        lines = [
            "# ⚡️ Laya Browser Agent Execution Report",
            "",
            "## 📌 Executive Summary",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| **Final Status** | **{status_badge}** |",
            f"| **Initial Target URL** | `{self.target_url}` |",
            f"| **Final URL Reached** | `{final_url or (self.steps_taken[-1].url if self.steps_taken else self.target_url)}` |",
            f"| **User Goal** | {self.goal} |",
            f"| **Total Steps Taken** | `{len(self.steps_taken)}` / `{self.max_steps}` |",
            f"| **Total Duration** | `{total_duration:.2f}s` |",
            f"| **Avg Decision Latency** | `{avg_step_ms:.1f}ms / step` |",
            f"| **Decision Policy** | `{self.model_id}` (Apple Silicon Metal GPU) |",
            f"| **Local Planner** | `{self.ollama_model}` |",
            f"| **Cloud API Cost** | **$0.00** (100% Local Inference, Zero Cloud Tokens) |",
            "",
            "## 📋 Structured Plan & Entity Resolution",
        ]

        if self.extracted_slots:
            safe_slots = dict(self.extracted_slots)
            for k in list(safe_slots.keys()):
                if any(w in k.lower() for w in ("pass", "pwd", "secret")):
                    safe_slots[k] = "••••••••"
            lines.append("### Extracted Entities")
            lines.append("| Slot | Resolved Value |")
            lines.append("| :--- | :--- |")
            for k, v in safe_slots.items():
                lines.append(f"| `{k}` | `{v}` |")
            lines.append("")

        if self.subtasks:
            lines.append("### Sequential Subtasks Progress")
            for idx, st in enumerate(self.subtasks, 1):
                marker = "✓" if idx <= self.current_subtask_idx else "○"
                lines.append(f"{idx}. [{marker}] {st}")
            lines.append("")

        lines.append("## ⏱ Step-by-Step Action Audit Trail")
        lines.append("| Step | Page URL | Subtask | Action Chosen | Parameters | Engine / Source | Confidence | Latency | Result |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

        for event in self.steps_taken:
            # Mask sensitive params
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

        # Discovered Web Application Affordances
        if self.engine and self.engine.site_cache:
            lines.append("## 🗺 Discovered Web Application Affordances")
            lines.append("*Dynamically discovered interactive capabilities across visited application routes:*")
            lines.append("")
            lines.append("| Route Key | Discovered Capabilities |")
            lines.append("| :--- | :--- |")
            for r_key, r_cands in self.engine.site_cache.items():
                cand_names = [k for k in r_cands.keys() if k != "finish"]
                sample = ", ".join([f"`{c}`" for c in cand_names[:5]])
                if len(cand_names) > 5:
                    sample += f" *(+{len(cand_names)-5} more)*"
                lines.append(f"| `{r_key}` | {sample or 'None'} |")
            lines.append("")

        if final_screenshot and os.path.exists(final_screenshot):
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

    def _find_matching_screen_element(self, tool_name: str, elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Finds screen element that semantically matches a tool name for visual fallback."""
        tool_clean = tool_name.lower().replace("_", " ")
        for el in elements:
            text = el.get("text", "").lower()
            if text and (text in tool_clean or tool_clean in text):
                return el
        return None

    def _resolve_tool_params(
        self,
        tool_def: Optional[Dict[str, Any]],
        screen_elements: Optional[List[Dict[str, Any]]] = None,
        fallback_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Universally maps slots, schema properties, and prior step results to tool parameters."""
        if not tool_def:
            return {}

        schema = tool_def.get("inputSchema") or {}
        properties: Dict[str, Any] = schema.get("properties") or {}
        resolved: Dict[str, Any] = {}

        if not properties:
            return {}

        for prop_name, prop_meta in properties.items():
            prop_type = prop_meta.get("type", "string") if isinstance(prop_meta, dict) else "string"
            prop_desc = (prop_meta.get("description", "") if isinstance(prop_meta, dict) else "").lower()

            value = None

            # 0. Check explicit fallback_params from decomposition or locator
            if fallback_params and prop_name in fallback_params and fallback_params[prop_name] not in (None, ""):
                value = fallback_params[prop_name]

            # A. Exact or normalized slot match
            if value is None:
                norm_prop = prop_name.lower().replace("_", "").replace("-", "")
                for slot_k, slot_v in self.extracted_slots.items():
                    norm_slot = slot_k.lower().replace("_", "").replace("-", "")
                    if norm_slot == norm_prop:
                        value = slot_v
                        break

            # B. Common aliases
            if value is None:
                aliases = {
                    "username": ["user", "login", "email", "account"],
                    "password": ["pass", "pwd", "secret"],
                    "search": ["query", "q", "filter", "keyword", "product", "sku", "item"],
                    "query": ["search", "q", "filter", "keyword", "product", "sku", "item"],
                    "quantity": ["qty", "count", "amount", "number", "units", "unit"],
                    "productid": ["product_id", "product", "id", "item_id"],
                    "orderid": ["order_id", "order", "id", "orderid"],
                }
                for canonical, synonyms in aliases.items():
                    if prop_name.lower() == canonical or prop_name.lower() in synonyms:
                        for syn in [canonical] + synonyms:
                            if syn in self.extracted_slots:
                                value = self.extracted_slots[syn]
                                break
                    if value is not None:
                        break

            # C. Contextual entity binding from previous tool output (e.g. IDs, tokens)
            if value is None and self.last_result is not None:
                value = self._find_contextual_entity(prop_name, prop_desc, self.last_result)

            # D. Dynamic navigation paths discovered from page links or user prompt
            if value is None and prop_name in ("path", "url", "route"):
                value = self._infer_path_from_subtask(screen_elements=screen_elements)

            # E. Short ID prefix resolution (e.g. expanding "360f30c4" to full UUID)
            if value and isinstance(value, str) and len(value) < 36 and ("id" in prop_name.lower() or "id" in prop_desc):
                full_id = self._expand_id_prefix(value, prop_name)
                if full_id:
                    value = full_id

            # F. Enum validation & Type casting
            if value is not None:
                enums = prop_meta.get("enum") if isinstance(prop_meta, dict) else None
                if enums and value not in enums:
                    value = None

            if value is not None:
                try:
                    if prop_type == "integer":
                        value = int(value)
                    elif prop_type == "number":
                        value = float(value)
                    elif prop_type == "boolean":
                        value = bool(value)
                    elif prop_type == "string":
                        value = str(value)
                except (ValueError, TypeError):
                    pass
                resolved[prop_name] = value

        return resolved

    def _find_contextual_entity(self, prop_name: str, prop_desc: str, context: Any) -> Any:
        """Finds entity identifiers or fields from previous tool output."""
        # Never bind generic RPC response envelope fields
        if prop_name.lower() in ("status", "message", "currentpath", "tool", "error"):
            return None

        is_id_field = "id" in prop_name.lower() or "id" in prop_desc

        if isinstance(context, list) and context:
            # Check for item matching searched SKU or name
            target_sku = str(self.extracted_slots.get("search") or self.extracted_slots.get("product") or "").lower()
            for item in context:
                if isinstance(item, dict):
                    item_sku = str(item.get("sku", "")).lower()
                    item_name = str(item.get("name", "")).lower()
                    if target_sku and (target_sku in item_sku or target_sku in item_name):
                        if is_id_field and "id" in item:
                            return item["id"]
                        if prop_name in item:
                            return item[prop_name]
            for item in context:
                if isinstance(item, dict):
                    if is_id_field and "id" in item:
                        if item.get("quantity", 1) > 0:
                            return item["id"]
                    if prop_name in item:
                        return item[prop_name]
            if is_id_field and isinstance(context[0], dict) and "id" in context[0]:
                return context[0]["id"]

        elif isinstance(context, dict):
            # Check nested lists (e.g. {"items": [...]}, {"products": [...]})
            for v in context.values():
                if isinstance(v, list) and v:
                    nested_res = self._find_contextual_entity(prop_name, prop_desc, v)
                    if nested_res is not None:
                        return nested_res

            if is_id_field and "id" in context:
                return context["id"]
            if is_id_field and "product" in context and isinstance(context["product"], dict) and "id" in context["product"]:
                return context["product"]["id"]
            if prop_name in context:
                return context[prop_name]
            for val in context.values():
                if isinstance(val, dict):
                    if is_id_field and "id" in val:
                        return val["id"]
                    if prop_name in val:
                        return val[prop_name]

        return None

    def _infer_path_from_subtask(self, screen_elements: Optional[List[Dict[str, Any]]] = None) -> str:
        """Dynamically discovers destination path from page links matching the subtask or goal."""
        subtask_text = self.subtasks[self.current_subtask_idx] if (self.subtasks and self.current_subtask_idx < len(self.subtasks)) else self.goal

        # 1. Dynamic discovery from screen elements on the active page (highest priority)
        if screen_elements:
            subtask_tokens = [w for w in re.findall(r"\w+", subtask_text.lower()) if len(w) > 2 and w not in ("the", "and", "for", "got", "get")]
            for el in screen_elements:
                href = el.get("href", "")
                if href and href.startswith("/"):
                    el_text = el.get("text", "").lower()
                    if any(token in el_text or token in href.lower() for token in subtask_tokens):
                        return href

        # 2. Check for explicit path token in subtask (must not be inside credentials like user/User@123)
        match = re.search(r"(?<![a-zA-Z0-9])(/[a-zA-Z0-9_\-]+)(?![@a-zA-Z0-9])", subtask_text)
        if match:
            return match.group(1)

        # 3. Check for explicit path token in overall goal (e.g. /products, /orders, /settings)
        for token in re.findall(r"(?<![a-zA-Z0-9])(/[a-zA-Z0-9_\-]+)(?![@a-zA-Z0-9])", self.goal):
            if "@" not in token and not any(token.lower().endswith(str(u).lower()) for u in self.extracted_slots.values() if u):
                return token

        return "/"

    def _expand_id_prefix(self, prefix: str, prop_name: str) -> Optional[str]:
        """Resolves a short ID prefix (e.g. '360f30c4') to full 36-char UUID via getter tools or DOM."""
        clean_prefix = str(prefix).strip().lower()
        if not clean_prefix:
            return None

        # 1. Try resolving from prior tool results
        if self.last_result:
            matched = self._find_matching_id_in_data(self.last_result, clean_prefix)
            if matched:
                return matched

        # 2. Try calling query/getter tool dynamically discovered for this entity
        entity = re.sub(r"[_\-]id$", "", prop_name.lower()).replace("id", "")
        available = self.driver.discover_tools() if self.driver else []
        getter_tools = [
            t.get("name") for t in available
            if t.get("name", "").startswith(("get_", "list_", "fetch_", "search_"))
            and (not entity or entity in t.get("name", "").lower())
        ]
        if not getter_tools and entity:
            getter_tools = [
                t.get("name") for t in available
                if t.get("name", "").startswith(("get_", "list_"))
            ]

        for getter_tool in getter_tools:
            if not getter_tool:
                continue
            try:
                raw = self.driver.call_tool(getter_tool, {})
                items = self._parse_tool_result(raw)
                matched = self._find_matching_id_in_data(items, clean_prefix)
                if matched:
                    return matched
            except Exception:
                pass

        return None

    def _find_matching_id_in_data(self, data: Any, prefix: str) -> Optional[str]:
        """Recursively searches for any 'id' field starting with prefix."""
        if isinstance(data, list):
            for item in data:
                res = self._find_matching_id_in_data(item, prefix)
                if res:
                    return res
        elif isinstance(data, dict):
            if "id" in data and str(data["id"]).lower().startswith(prefix):
                return str(data["id"])
            for v in data.values():
                if isinstance(v, (dict, list)):
                    res = self._find_matching_id_in_data(v, prefix)
                    if res:
                        return res
        return None

    def _parse_tool_result(self, raw_result: Any) -> Any:
        """Parses tool return value into structured JSON if returned as a string."""
        if isinstance(raw_result, str):
            try:
                return json.loads(raw_result)
            except Exception:
                return raw_result
        return raw_result
