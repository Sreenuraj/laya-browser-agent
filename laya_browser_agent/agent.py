"""Universal Autonomous Browser Agent powered by Laya MLX decision models, Screen Perception, and WebMCP."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from urllib.parse import urlparse
from .browser import PlaywrightWebMcpDriver
from .extractor import SlotExtractor, StructuredPlan, resolve_ollama_model
from .laya_engine import LayaEngine, normalize_route_key
from .reporting import evaluate_run_status, generate_html_dashboard, generate_markdown_report
from .resolver import (
    expand_id_prefix,
    find_contextual_entity,
    find_matching_id_in_data,
    find_matching_screen_element,
    infer_path_from_subtask,
    parse_tool_result,
    resolve_tool_params,
)
from .variants import (
    extract_screen_variants,
    resolve_screen_variant,
    find_page_variant_buttons,
)
from .trajectory import TrajectoryLogger
from .llm import configure_llm_client


def assert_step_transition(
    subtask: str = "",
    action: str = "",
    params: Optional[Dict[str, Any]] = None,
    result: Any = None,
    before_url: str = "",
    after_url: str = "",
    before_elements: Optional[List[Dict[str, Any]]] = None,
    after_elements: Optional[List[Dict[str, Any]]] = None,
    extracted_slots: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Step-baked execution verification.
    Validates physical action execution integrity (driver failures, tool errors,
    missing affordances) without brittle hardcoded domain assumptions.
    """
    act_low = (action or "").lower()
    if result is False:
        return False, "Action execution returned failure (False)"
    if isinstance(result, dict):
        if result.get("success") is False:
            return False, f"Action execution failed: {result}"
        if "tool_error" in result and "fallback" not in result:
            return False, f"Tool error: {result.get('tool_error')}"
        if "input_error" in result:
            return False, f"Input error: {result.get('input_error')}"
        if result.get("status") in ("failed", "error", "no_matching_affordance"):
            return False, f"Action status error: {result.get('status')}"
    if act_low == "other":
        return False, "No matching affordance on screen for active subtask"
    return True, "Physical execution passed"


logger = logging.getLogger("laya.agent")


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
    llm_fallback: bool = False
    llm_calls: int = 0
    decision_ms: Optional[float] = None
    screenshot_path: Optional[str] = None


class LayaAgent:
    """
    Universal autonomous browser agent operating under Loop Engineering.
    Combines sub-15ms Laya MLX decision policy, direct screen coordinate actuation,
    and WebMCP semantic tool execution.
    """

    def __init__(
        self,
        target_url: str,
        goal: str,
        headed: bool = True,
        max_steps: int = 25,
        max_seconds: Optional[float] = None,
        model_id: str = "aac6fef/laya-mlx",
        ollama_model: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        run_id: Optional[str] = None,
        trajectory_logger: Optional[TrajectoryLogger] = None,
        event_callback: Optional[Callable[[AgentStepEvent], None]] = None,
        generate_report: bool = True,
        report_dir: str = "reports",
        report_file: Optional[str] = None,
        screenshot_mode: str = "major_steps",
        driver: Optional[PlaywrightWebMcpDriver] = None,
        disambiguation_callback: Optional[Callable[[str, List[Dict[str, Any]]], Optional[str]]] = None,
    ):
        self.target_url = target_url
        self.goal = goal
        self.headed = bool(headed)
        self.max_steps = max_steps
        self.max_seconds = max_seconds
        self.model_id = model_id
        self.ollama_url = (ollama_url or "http://localhost:11434").rstrip("/")
        self.ollama_model = resolve_ollama_model(preferred=ollama_model, base_url=self.ollama_url)
        self._llm_client = configure_llm_client(base_url=self.ollama_url, model=self.ollama_model)
        self._step_llm_base: int = 0
        self._step_decision_ms: Optional[float] = None
        self.trajectory_logger: TrajectoryLogger = trajectory_logger or TrajectoryLogger(run_id=run_id)
        self.event_callback = event_callback
        self.disambiguation_callback = disambiguation_callback
        self.generate_report_enabled = bool(generate_report)
        self.report_dir = report_dir
        self.report_file = report_file or os.path.join(report_dir, "execution_report.md")
        self.screenshot_mode = screenshot_mode.lower()

        self._owns_driver = driver is None
        self.driver: Optional[PlaywrightWebMcpDriver] = driver
        self.engine: Optional[LayaEngine] = None
        self.extractor: SlotExtractor = SlotExtractor(ollama_url=self.ollama_url, ollama_model=self.ollama_model)

        self.plan: Optional[StructuredPlan] = None
        self.extracted_slots: Dict[str, Any] = {}
        self.extracted_data: List[Dict[str, Any]] = []
        self.subtasks: List[str] = []
        self.current_subtask_idx = 0
        self.history: List[str] = []
        self.last_result: Any = None
        self.steps_taken: List[AgentStepEvent] = []
        self.progressive_discovery_enabled: bool = True
        self._discovery_expanded: bool = False
        self.deferred_goal: Optional[str] = None
        self._subtask_failures: Dict[int, int] = {}
        self._subtask_step_counts: Dict[int, int] = {}
        self.telemetry: Dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_reads": 0,
            "cache_writes": 0,
            "latency_saved_ms": 0.0,
            "planning_duration_ms": 0.0,
        }

    def _record_step(
        self,
        event: AgentStepEvent,
        candidates: Optional[Dict[str, Any]] = None,
        screen_summary: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Appends step event, notifies callback, and writes structured record to trajectory logger."""
        # Measured, not guessed: LLM calls issued by the client since this step began.
        event.llm_calls = self._llm_call_total() - self._step_llm_base
        event.llm_fallback = event.llm_calls > 0
        event.decision_ms = self._step_decision_ms
        self.steps_taken.append(event)
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception:
                pass
        try:
            self.trajectory_logger.log_step(
                step=event.step,
                subtask=event.subtask,
                goal=self.goal,
                url=event.url,
                candidates=candidates or {t.get("name", ""): t for t in event.available_tools},
                chosen_action=event.chosen_action,
                confidence=event.confidence,
                action_params=event.tool_params,
                duration_ms=event.duration_ms,
                engine_info=event.engine_info,
                llm_fallback=event.llm_fallback,
                screen_summary=screen_summary or [],
                post_condition_result=event.tool_result,
                verified=None,  # unknown until Phase 1 post-condition verification
            )
        except Exception:
            pass

    def _llm_call_total(self) -> int:
        return int(getattr(self._llm_client, "llm_call_count", 0))

    @property
    def llm_calls_total(self) -> int:
        """Total LLM calls (planner + fallbacks) issued through this agent's client."""
        return self._llm_call_total()

    def get_session_telemetry(self) -> Dict[str, Any]:
        """Aggregates telemetry across planner, decision engine, and macro cache."""
        in_tok = self.telemetry.get("input_tokens", 0)
        out_tok = self.telemetry.get("output_tokens", 0)
        c_reads = self.telemetry.get("cache_reads", 0)
        c_writes = self.telemetry.get("cache_writes", 0)
        lat_saved = self.telemetry.get("latency_saved_ms", 0.0)

        if hasattr(self.extractor, "telemetry"):
            in_tok += self.extractor.telemetry.get("input_tokens", 0)
            out_tok += self.extractor.telemetry.get("output_tokens", 0)
            c_reads += self.extractor.telemetry.get("cache_reads", 0)
            c_writes += self.extractor.telemetry.get("cache_writes", 0)
            lat_saved += self.extractor.telemetry.get("latency_saved_ms", 0.0)

        if self.engine and hasattr(self.engine, "telemetry"):
            in_tok += self.engine.telemetry.get("input_tokens", 0)
            out_tok += self.engine.telemetry.get("output_tokens", 0)
            c_reads += self.engine.telemetry.get("cache_reads", 0)
            c_writes += self.engine.telemetry.get("cache_writes", 0)
            lat_saved += self.engine.telemetry.get("latency_saved_ms", 0.0)

        return {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "cache_reads": c_reads,
            "cache_writes": c_writes,
            "latency_saved_ms": lat_saved,
            "planning_duration_ms": self.telemetry.get("planning_duration_ms", 0.0),
        }

    def _get_active_url(self) -> str:
        """Safely retrieves active page URL without raising AttributeError."""
        if not self.driver:
            return ""
        if hasattr(self.driver, "get_active_url"):
            try:
                return self.driver.get_active_url() or ""
            except Exception:
                return ""
        return getattr(self.driver, "current_url", "") or ""

    def _extract_universal_dom_price(self) -> Optional[str]:
        """Universal DOM currency extraction with variant perception, disambiguation, and consistency anchoring."""
        if not self.driver or not self.driver._page:
            return None
        try:
            query_str = str(
                self.extracted_slots.get("search")
                or self.extracted_slots.get("query")
                or self.extracted_slots.get("product")
                or ""
            ).lower()

            # 1. Structured screen variant extraction with cross-site anchoring & disambiguation
            variants = extract_screen_variants(
                page=self.driver._page,
                query_str=query_str,
                target_variant=self.extracted_slots.get("selected_variant"),
            )
            if variants:
                resolved_var, resolved_spec = resolve_screen_variant(
                    variants=variants,
                    query_str=query_str,
                    target_variant=self.extracted_slots.get("selected_variant"),
                    disambiguation_callback=self.disambiguation_callback,
                )
                if resolved_var and resolved_var.price:
                    if resolved_spec and "selected_variant" not in self.extracted_slots:
                        self.extracted_slots["selected_variant"] = resolved_spec
                    return resolved_var.price
                return None

            # 2. First, search for product cards / search result items matching the query tokens
            query_tokens = [w for w in re.findall(r"\w+", query_str) if len(w) > 2 and w not in ("price", "cost", "cheaper")]

            if query_tokens:
                card_selectors = [
                    "[data-component-type*='search-result']",
                    "article",
                    "div[class*='product' i]",
                    "div[class*='item' i]",
                    "div[data-id]",
                    "div[class*='card' i]",
                    "li:has([class*='price' i])",
                ]
                for c_sel in card_selectors:
                    try:
                        cards = self.driver._page.locator(c_sel).all()
                        for card in cards[:15]:
                            try:
                                card_txt = card.inner_text().lower()
                                if any(t in card_txt for t in query_tokens):
                                    m = re.search(r"(?:[₹$€£¥]|Rs\.?)\s*[\d,]+(?:\.\d+)?", card_txt)
                                    if m:
                                        return m.group(0).strip()
                            except Exception:
                                continue
                    except Exception:
                        continue

            # 2. Check high-priority price containers
            locs = self.driver._page.locator("[class*='price' i], [id*='price' i], [data-testid*='price' i], [itemprop='price']").all()
            for loc in locs[:25]:
                try:
                    txt = loc.inner_text().strip()
                    m = re.search(r"(?:[₹$€£¥]|Rs\.?)\s*[\d,]+(?:\.\d+)?", txt)
                    if m:
                        return m.group(0).strip()
                except Exception:
                    continue

            # 3. Check full body text
            body_txt = self.driver._page.inner_text("body")
            m = re.search(r"(?:[₹$€£¥]|Rs\.?)\s*[\d,]+(?:\.\d+)?", body_txt)
            if m:
                return m.group(0).strip()
        except Exception:
            pass
        return None

    def prepare_plan(self, session_context: Optional[Dict[str, Any]] = None) -> StructuredPlan:
        """Structures and organizes the human input into slots and sequential subtasks."""
        if not self.plan:
            if session_context is not None and self.driver and hasattr(self.driver, "get_tabs"):
                try:
                    if self.driver._page and not self.driver._page.is_closed():
                        tabs = self.driver.get_tabs()
                        if tabs:
                            session_context.setdefault("open_tabs", tabs)
                            session_context.setdefault("active_url", self.driver.get_active_url())
                            session_context.setdefault("active_title", self.driver.get_page_title())
                except Exception:
                    pass
            t_plan_0 = time.time()
            self.plan = self.extractor.extract(self.goal, initial_url=self.target_url, session_context=session_context)
            self.telemetry["planning_duration_ms"] = (time.time() - t_plan_0) * 1000.0
            self.extracted_slots = self.plan.slots
            # Progressive Discovery Horizon:
            # If the planner identified an uncertain / speculative horizon,
            # initialize self.subtasks with the 100% confident initial steps.
            # Downstream subtasks will be dynamically created and grounded from the live settled screen.
            if self.progressive_discovery_enabled and self.plan.confident_subtasks and len(self.plan.confident_subtasks) < len(self.plan.subtasks):
                self.subtasks = list(self.plan.confident_subtasks)
                self.deferred_goal = self.plan.deferred_goal
            else:
                self.subtasks = list(self.plan.subtasks)
                self.deferred_goal = None
        return self.plan

    def _extract_navigation_target(self, subtask: str, current_url: str = "") -> Optional[str]:
        """
        Extracts a navigation target URL from a subtask description.
        Supports explicit http/https URLs, localhost addresses, and universal domain names with 2+ char TLDs.
        """
        st = subtask.strip()
        # Multi-domain comparison or reporting tasks must never trigger navigation away from the active page
        if any(w in st.lower() for w in ("compare", "difference", "comparison", "determine cheaper", "report lowest", "report where", "which is")):
            return None

        # 1. Explicit http/https URL
        m_url = re.search(r"https?://[^\s,\)\"]+", st)
        if m_url:
            return m_url.group(0).rstrip(".,;)")

        # 2. Localhost with port
        m_local = re.search(r"\blocalhost:\d*(?:/\S*)?", st, flags=re.IGNORECASE)
        if m_local:
            loc = m_local.group(0).rstrip(".,;)")
            return f"http://{loc}"

        # 3. Universal domain names in navigation contexts (e.g. "Go to Amazon.in", "Visit flipkart.com")
        is_nav_context = re.search(r"\b(?:go to|goto|navigate|visit|open|head to|on)\b", st.lower()) is not None
        if is_nav_context:
            non_web = {"md", "py", "sh", "txt", "pdf", "png", "jpg", "jpeg", "gif", "svg", "json", "html", "css", "js", "ts", "zip"}
            domains = []
            # (?<![@\w.\-]) keeps email addresses (user@host.tld) from being parsed as site names
            for m in re.finditer(r"(?<![@\w.\-])(?:[a-zA-Z0-9\-]+\.)+([a-zA-Z]{2,})(?:/[^\s,\)\"]*)?\b", st):
                tld = m.group(1).lower()
                if tld not in non_web:
                    domains.append(m.group(0).rstrip(".,;)"))
            # If multiple domains are mentioned (e.g. checking/comparing across sites), it is not a single navigation
            # The planner may restate an email host as a site ("register on work.org" from "david@work.org").
            # A domain the user only ever wrote as part of an email address is never a navigation target.
            goal = (getattr(self, "goal", "") or "").lower()
            email_hosts = set(re.findall(r"@((?:[a-z0-9\-]+\.)+[a-z]{2,})", goal))
            standalone = {
                h for h in email_hosts
                if re.search(r"(?<![@\w.\-])" + re.escape(h) + r"\b", goal)
            }
            domains = [d for d in domains if d.lower().split("/")[0] not in (email_hosts - standalone)]
            if len(domains) == 1:
                return f"https://{domains[0]}"

        return None

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
            self._owns_driver = True

        # 1. Structure the initial human request into slots and sequential subtasks
        self.prepare_plan()

        # 2. Launch browser at target URL (headed or headless)
        if not self._owns_driver and self.driver._page and not self.driver._page.is_closed():
            # Shared driver already active across turns: do not navigate away from active page
            pass
        else:
            self.driver.start(self.target_url)

        failed_actions: set[str] = set()
        last_url: str = ""
        consecutive_scrolls: int = 0
        state_action_history: Dict[str, int] = {}

        execution_error: Optional[str] = None
        consecutive_step_errors: int = 0
        try:
            for step_num in range(1, self.max_steps + 1):
                if self.max_seconds and (time.time() - start_time) > self.max_seconds:
                    execution_error = f"Wall-clock budget exceeded ({self.max_seconds:.0f}s) before step {step_num}"
                    break
                step_start = time.time()
                self._step_llm_base = self._llm_call_total()
                self._step_decision_ms = None
                try:

                    # Settle page and discover registered tools, visible screen elements, and scroll state
                    self.driver.wait_for_settled(timeout_ms=1000)
                    current_url = self.driver.get_active_url()
                    page_title = self.driver.get_page_title()
                    tools = self.driver.discover_tools()
                    screen_elements = self.driver.get_screen_elements()
                    scroll_state = self.driver.get_scroll_state()

                    # Reset failed actions if page navigated
                    if current_url != last_url:
                        failed_actions.clear()
                        state_action_history.clear()
                        last_url = current_url
                        consecutive_scrolls = 0

                    # Skip subtasks that describe navigating to where the browser already is, or passive verification steps
                    while self.current_subtask_idx < len(self.subtasks):
                        st = self.subtasks[self.current_subtask_idx].lower()
                        # Skip passive verification subtasks only if they do NOT request data inspection or counting
                        is_inspection = any(w in st for w in ("count", "pending", "status", "total", "how many", "report", "audit", "inspect", "inventory", "price", "cheaper"))
                        if not is_inspection and any(st.startswith(v) for v in ["verify", "confirm", "ensure"]):
                            self.current_subtask_idx += 1
                            continue

                        # Live affordance validation for conditional subtasks
                        if any(st.startswith(p) for p in ("if there are", "if multiple", "if any", "in case")):
                            cond_words = [w for w in re.findall(r"\w+", st) if len(w) > 3 and w not in ("there", "multiple", "pages", "items", "then", "also")]
                            has_matching_affordance = any(
                                any(cw in el.get("text", "").lower() or cw in (el.get("aria_label") or "").lower() for cw in cond_words)
                                for el in screen_elements
                            )
                            if not has_matching_affordance:
                                self.current_subtask_idx += 1
                                continue

                        # Never skip login subtasks on unauthenticated login page
                        if any(w in st for w in ("login", "log in", "signin", "sign in", "authenticate", "credential", "auth")):
                            break

                        is_nav_only = (
                            any(w in st for w in ["navigate", "go to", "got to", "goto", "visit", "head to"])
                            or (any(w in st for w in ["tab", "link", "page", "section"]) and any(v in st for v in ["click", "select", "press"]))
                        ) and not ("enter" in st or "credential" in st or "password" in st or "form" in st or "fill" in st or "type" in st or "submit" in st)

                        route_tokens = [t for t in urlparse(current_url).path.split("/") if len(t) > 2]
                        is_already_on_page = any(tok.lower() in st for tok in route_tokens)
                        if is_nav_only and is_already_on_page:
                            self.current_subtask_idx += 1
                        # Check if batch mutation subtask (e.g. "cancel all open orders") has no items left on target page
                        is_batch_mutation = any(w in st for w in ("cancel all", "delete all", "remove all", "approve all", "reject all", "all open", "all pending", "all orders in pending", "cancel remaining"))
                        action_verb = next((v for v in ("cancel", "delete", "remove", "approve", "reject") if v in st), "")
                        if is_batch_mutation and action_verb and self.driver and self.driver._page:
                            is_on_target_route = any(tok.lower() in st or any(p in st for p in tok.lower().split("-")) for tok in route_tokens)
                            if is_on_target_route and action_verb in ("cancel", "approve", "reject"):
                                btn_count = self.driver._page.locator(
                                    f"button:has-text('{action_verb.capitalize()}'), button[aria-label*='{action_verb}' i], button[title*='{action_verb}' i], a:has-text('{action_verb.capitalize()}')"
                                ).count()
                                if btn_count == 0:
                                    self.current_subtask_idx += 1
                                    continue

                        break

                    # Active subtask
                    if self.current_subtask_idx < len(self.subtasks):
                        active_subtask = self.subtasks[self.current_subtask_idx]
                    elif self.deferred_goal and not self._discovery_expanded:
                        # Progressive Discovery Horizon: Confident initial horizon is complete!
                        # Ground remaining user intent by validating live screen affordances
                        new_subtasks = self.extractor.expand_plan_from_screen(
                            goal=self.goal,
                            deferred_goal=self.deferred_goal,
                            completed_subtasks=self.subtasks,
                            current_url=current_url,
                            screen_elements=screen_elements,
                            tools=tools,
                            extracted_slots=self.extracted_slots,
                        )
                        self._discovery_expanded = True
                        if new_subtasks:
                            self.subtasks.extend(new_subtasks)
                            active_subtask = self.subtasks[self.current_subtask_idx]
                            self.history.append(f"expand_plan_from_screen({json.dumps(new_subtasks)})")
                        else:
                            active_subtask = "Finish goal"
                    else:
                        active_subtask = "Finish goal"

                    # Direct multi-tab browser actions (switch tab, open new tab, close tab)
                    low_st = active_subtask.lower().strip()
                    m_tab_sw = re.search(r"\bswitch\s+(?:to\s+)?(?:the\s+)?tab\s*(\d+)\b", low_st)
                    m_dom_sw = re.search(r"\bswitch\s+(?:to\s+)?(?:the\s+)?(?:https?://)?(?:www\.)?([a-zA-Z0-9_\-\.]+)/?\s+tab\b", low_st)
                    is_open_new_tab_only = any(low_st.startswith(p) for p in ("open new tab", "open a new tab", "create new tab", "create a new tab")) and not self._extract_navigation_target(active_subtask, current_url)
                    m_close_tab = re.search(r"\bclose\s+(?:the\s+)?tab\s*(\d+)?\b", low_st)

                    if (m_tab_sw or m_dom_sw or is_open_new_tab_only or m_close_tab) and self.driver:
                        prim_start = time.time()
                        tab_action = "browser_switch_tab"
                        tab_param: Dict[str, Any] = {}
                        if m_tab_sw and hasattr(self.driver, "switch_tab"):
                            target_tab = int(m_tab_sw.group(1))
                            self.driver.switch_tab(target_tab)
                            tab_param = {"tab": target_tab}
                        elif m_dom_sw and hasattr(self.driver, "switch_tab") and m_dom_sw.group(1) not in ("the", "this", "active", "current"):
                            target_dom = m_dom_sw.group(1).rstrip("/")
                            self.driver.switch_tab(target_dom)
                            tab_param = {"target": target_dom}
                        elif is_open_new_tab_only and hasattr(self.driver, "new_tab"):
                            self.driver.new_tab()
                            tab_action = "browser_new_tab"
                        elif m_close_tab and hasattr(self.driver, "close_tab"):
                            if not any(w in self.goal.lower() for w in ("do not close", "dont close", "keep open")):
                                close_idx = int(m_close_tab.group(1)) if m_close_tab.group(1) is not None else None
                                self.driver.close_tab(close_idx)
                                tab_action = "browser_close_tab"
                                tab_param = {"tab": close_idx}
                            else:
                                tab_action = "browser_noop"
                        else:
                            tab_action = "browser_switch_tab"

                        self.driver.wait_for_settled(1000)
                        self.current_subtask_idx += 1
                        failed_actions.clear()
                        consecutive_scrolls = 0
                        duration = (time.time() - prim_start) * 1000
                        event = AgentStepEvent(
                            step=step_num,
                            subtask=active_subtask,
                            url=self.driver.get_active_url(),
                            title=self.driver.get_page_title(),
                            available_tools=self.driver.discover_tools(),
                            chosen_action=tab_action,
                            confidence=1.0,
                            probabilities={},
                            tool_params=tab_param,
                            tool_result={"action": tab_action, **tab_param},
                            duration_ms=duration,
                            engine_info=f"Universal Tab Management ({tab_action})",
                            screenshot_path=None,
                        )
                        self._record_step(event)
                        continue

                    # Direct fast path for explicit URL navigation or cross-site domain navigation
                    target_nav_url = self._extract_navigation_target(active_subtask, current_url)
                    if target_nav_url:
                        cur_parsed = urlparse(current_url)
                        tgt_parsed = urlparse(target_nav_url)
                        cur_netloc = cur_parsed.netloc.lower().lstrip("www.")
                        tgt_netloc = tgt_parsed.netloc.lower().lstrip("www.")

                        is_diff_origin = bool(tgt_netloc and cur_netloc != tgt_netloc)
                        is_diff_path = bool(tgt_parsed.path and tgt_parsed.path.rstrip("/") != cur_parsed.path.rstrip("/"))

                        is_new_tab_requested = any(
                            w in active_subtask.lower()
                            for w in ("new tab", "different tab", "another tab", "new browser tab", "different browser tab", "in a tab", "separate tab")
                        ) or any(
                            w in self.goal.lower()
                            for w in ("different tabs", "separate tabs", "different tab", "both website in different tabs", "both websites in different tabs", "both sites in different tabs")
                        )

                        if is_diff_origin or (is_diff_path and cur_netloc == tgt_netloc) or is_new_tab_requested:
                            prim_start = time.time()
                            nav_action = "browser_navigate"

                            # Multi-tab logic: check if tab for target already exists
                            open_tabs = self.driver.get_tabs() if hasattr(self.driver, "get_tabs") else []
                            matching_tab = None
                            for t in open_tabs:
                                t_net = urlparse(t["url"]).netloc.lower().lstrip("www.")
                                if tgt_netloc and t_net == tgt_netloc:
                                    matching_tab = t
                                    break

                            if matching_tab and hasattr(self.driver, "switch_tab"):
                                # Tab already open for target domain: switch to it
                                self.driver.switch_tab(matching_tab["index"])
                                nav_action = "browser_switch_tab"
                            elif (
                                is_new_tab_requested
                                and hasattr(self.driver, "new_tab")
                                and len(open_tabs) >= 1
                                and any(t.get("url") and "about:blank" not in t["url"] for t in open_tabs)
                            ):
                                # Subtask or user goal specifically requested new tab, and initial tab is in use
                                self.driver.new_tab(target_nav_url)
                                nav_action = "browser_new_tab"
                            else:
                                self.driver.navigate_to(target_nav_url)

                            self.driver.wait_for_settled(1200)
                            try:
                                if hasattr(self.driver, "wait_for_page_interactive"):
                                    self.driver.wait_for_page_interactive(1200)
                            except Exception:
                                pass

                            # Advance subtask if it was pure navigation, else stay on target page to execute on-page sub-actions
                            is_pure_nav_only = not any(
                                w in active_subtask.lower()
                                for w in ("search", "find", "click", "select", "enter", "fill", "type", "buy", "purchase", "order", "cancel", "approve", "reject", "delete", "check", "count", "audit", "inspect", "locate")
                            )
                            if is_pure_nav_only:
                                self.current_subtask_idx += 1

                            failed_actions.clear()
                            consecutive_scrolls = 0
                            duration = (time.time() - prim_start) * 1000
                            event = AgentStepEvent(
                                step=step_num,
                                subtask=active_subtask,
                                url=self.driver.get_active_url(),
                                title=self.driver.get_page_title(),
                                available_tools=self.driver.discover_tools(),
                                chosen_action=nav_action,
                                confidence=1.0,
                                probabilities={},
                                tool_params={"url": target_nav_url},
                                tool_result={"action": nav_action, "url": target_nav_url},
                                duration_ms=duration,
                                engine_info=f"Universal Navigation ({nav_action}: {target_nav_url})",
                                screenshot_path=None,
                            )
                            self._record_step(event)
                            continue

                    # Automatic cross-domain tab alignment gate:
                    # When multi-tab sessions are active, align the active tab with the domain targeted by the subtask
                    if hasattr(self.driver, "get_tabs") and hasattr(self.driver, "switch_tab"):
                        open_tabs = self.driver.get_tabs()
                        if len(open_tabs) > 1:
                            st_low = active_subtask.lower()
                            matched_tab = None
                            for t in open_tabs:
                                t_net = urlparse(t["url"]).netloc.lower().lstrip("www.")
                                t_root = t_net.split(".")[0] if "." in t_net else t_net
                                if t_root and len(t_root) > 2 and (
                                    f"on {t_root}" in st_low
                                    or f"in {t_root}" in st_low
                                    or f"{t_root} " in st_low
                                    or f"at {t_root}" in st_low
                                ):
                                    matched_tab = t
                                    break
                            if matched_tab and not matched_tab.get("active"):
                                prim_start = time.time()
                                self.driver.switch_tab(matched_tab["index"])
                                self.driver.wait_for_settled(800)
                                cur_url = self.driver.get_active_url()
                                event = AgentStepEvent(
                                    step=step_num,
                                    subtask=active_subtask,
                                    url=cur_url,
                                    title=self.driver.get_page_title(),
                                    available_tools=self.driver.discover_tools(),
                                    chosen_action="browser_switch_tab",
                                    confidence=1.0,
                                    probabilities={},
                                    tool_params={"tab_index": matched_tab["index"], "url": cur_url},
                                    tool_result={"switched_to_tab": matched_tab["index"], "url": cur_url},
                                    duration_ms=(time.time() - prim_start) * 1000,
                                    engine_info=f"Cross-Domain Tab Alignment ({matched_tab['url']})",
                                    screenshot_path=None,
                                )
                                self._record_step(event)
                                continue


                    # Direct fast path for browser primitives (100% deterministic, ~10ms execution, zero model calls)
                    st_low = active_subtask.lower().strip()
                    is_pagination_loop = any(w in st_low for w in ("pagination", "all pages", "each page", "every page", "multiple pages", "different pages", "next page", "next button"))
                    is_sc = not is_pagination_loop and any(w in st_low for w in ("take screenshot", "take a screenshot", "capture screen", "save screenshot", "take snapshot", "screenshot"))
                    is_sc_down = not is_pagination_loop and any(w in st_low for w in ("scroll down", "scroll page down", "scroll to bottom"))
                    is_sc_up = not is_pagination_loop and any(w in st_low for w in ("scroll up", "scroll page up", "scroll to top"))
                    is_back = not is_pagination_loop and st_low in ("go back", "back", "navigate back", "previous page")
                    is_reload = not is_pagination_loop and st_low in ("refresh", "reload", "refresh page", "reload page")

                    if is_sc or is_sc_down or is_sc_up or is_back or is_reload:
                        prim_start = time.time()
                        prim_action = ""
                        prim_result = None
                        prim_params = {}
                        report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                        os.makedirs(report_out_dir, exist_ok=True)
                        sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                        saved_path = None

                        take_sc = (
                            is_sc
                            or self.screenshot_mode == "every_step"
                            or (self.screenshot_mode == "major_steps" and (is_back or is_reload))
                        )

                        if is_sc:
                            prim_action = "browser_take_screenshot"
                            saved_path = self.driver.take_screenshot(sc_path)
                            prim_result = {"screenshot": saved_path}
                            prim_params = {"path": saved_path}
                        elif is_sc_down:
                            prim_action = "browser_scroll_down"
                            self.driver.mousewheel(0, 600)
                            if take_sc:
                                saved_path = self.driver.take_screenshot(sc_path)
                            prim_result = {"status": "scrolled_down", "amount": 600}
                        elif is_sc_up:
                            prim_action = "browser_scroll_up"
                            self.driver.mousewheel(0, -600)
                            if take_sc:
                                saved_path = self.driver.take_screenshot(sc_path)
                            prim_result = {"status": "scrolled_up", "amount": 600}
                        elif is_back:
                            prim_action = "browser_go_back"
                            self.driver.go_back()
                            if take_sc:
                                saved_path = self.driver.take_screenshot(sc_path)
                            prim_result = {"status": "navigated_back"}
                        elif is_reload:
                            prim_action = "browser_reload"
                            self.driver.reload()
                            if take_sc:
                                saved_path = self.driver.take_screenshot(sc_path)
                            prim_result = {"status": "reloaded"}

                        prim_dur = (time.time() - prim_start) * 1000
                        event = AgentStepEvent(
                            step=step_num,
                            subtask=active_subtask,
                            url=current_url,
                            title=page_title,
                            available_tools=tools,
                            chosen_action=prim_action,
                            confidence=1.0,
                            probabilities={},
                            tool_params=prim_params,
                            tool_result=prim_result,
                            duration_ms=prim_dur,
                            engine_info="Browser Native Primitive (10ms)",
                            screenshot_path=saved_path,
                        )
                        self._record_step(event)
                        if prim_action:
                            self.history.append(f"{prim_action}({json.dumps(prim_params)})")
                        self.current_subtask_idx += 1
                        continue

                    # 2.5 Autonomous Multi-Page Pagination Loop Handler
                    if is_pagination_loop:
                        # If pagination controls are below the fold, scroll down to reveal them
                        scroll_st = self.driver.get_scroll_state()
                        if scroll_st.get("can_scroll_down"):
                            self.driver.mousewheel(0, 500)
                            self.driver.wait_for_settled(200)
                            screen_elements = self.driver.get_screen_elements()

                        next_btn = next((
                            el for el in screen_elements
                            if el.get("tag") in ("button", "a") and (
                                el.get("text", "").strip().lower() in ("next", "next >", ">", "next page")
                                or "next" in (el.get("aria_label") or "").lower()
                            )
                        ), None)
                        has_next = bool(next_btn and not next_btn.get("disabled"))
                        wants_sc = any(w in st_low for w in ("screenshot", "snapshot", "capture")) or any(w in (self.goal or "").lower() for w in ("screenshot", "snapshot", "capture"))
                        last_was_sc = bool(
                            (self.history and any("browser_take_screenshot" in h for h in self.history[-1:]))
                            or (self.steps_taken and self.steps_taken[-1].chosen_action == "browser_take_screenshot")
                        )

                        if wants_sc and not last_was_sc:
                            # Capture full-page screenshot of the current page in the pagination flow
                            report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                            os.makedirs(report_out_dir, exist_ok=True)
                            sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                            saved_path = self.driver.take_screenshot(sc_path, full_page=True)
                            prim_dur = (time.time() - step_start) * 1000
                            event = AgentStepEvent(
                                step=step_num,
                                subtask=active_subtask,
                                url=current_url,
                                title=page_title,
                                available_tools=tools,
                                chosen_action="browser_take_screenshot",
                                confidence=1.0,
                                probabilities={},
                                tool_params={"path": saved_path},
                                tool_result={"screenshot": saved_path},
                                duration_ms=prim_dur,
                                engine_info="Pagination Loop: Page Screenshot",
                            )
                            self._record_step(event)
                            self.history.append(f"browser_take_screenshot({json.dumps({'path': saved_path})})")
                            if not has_next:
                                while self.current_subtask_idx + 1 < len(self.subtasks):
                                    nxt = self.subtasks[self.current_subtask_idx + 1].lower()
                                    if any(w in nxt for w in ("pagination", "all pages", "each page", "every page", "take screenshot", "take a screenshot")):
                                        self.current_subtask_idx += 1
                                    else:
                                        break
                                self.current_subtask_idx += 1
                            continue

                        elif has_next:
                            # Click Next to advance to the next page
                            nx = next_btn.get("x")
                            ny = next_btn.get("y")
                            self.driver.click(target="Next", x=nx, y=ny)
                            self.driver.wait_for_settled(timeout_ms=1000)
                            if self.engine:
                                self.engine.invalidate_cache(current_url)
                            prim_dur = (time.time() - step_start) * 1000
                            event = AgentStepEvent(
                                step=step_num,
                                subtask=active_subtask,
                                url=current_url,
                                title=page_title,
                                available_tools=tools,
                                chosen_action="click_next",
                                confidence=1.0,
                                probabilities={},
                                tool_params={"target": "Next", "x": nx, "y": ny},
                                tool_result={"clicked": "Next", "success": True},
                                duration_ms=prim_dur,
                                engine_info="Pagination Loop: Next Page",
                            )
                            self._record_step(event)
                            self.history.append("click_next({'target': 'Next'})")
                            # Keep active subtask to process the newly opened page
                            continue

                        else:
                            # Pagination complete
                            while self.current_subtask_idx + 1 < len(self.subtasks):
                                nxt = self.subtasks[self.current_subtask_idx + 1].lower()
                                if any(w in nxt for w in ("pagination", "all pages", "each page", "every page", "take screenshot", "take a screenshot")):
                                    self.current_subtask_idx += 1
                                else:
                                    break
                            self.current_subtask_idx += 1
                            continue

                    # 2.6 Autonomous Data Inspection & Counting Handler (100% deterministic, zero cloud calls)
                    is_inspection_task = (
                        any(w in st_low for w in ("check and count", "count ", "check if there are any", "how many", "inspect count", "check count", "get count", "check pending", "check open", "count pending", "count open", "count all"))
                        or any(w in st_low for w in ("compare", "cheaper", "lowest price", "determine cheaper", "price difference", "note the price", "inspect price", "note price", "find price", "report lowest", "report where", "which is cheaper"))
                        or (any(w in st_low for w in ("check", "count", "audit", "inspect", "report", "compare")) and any(k in st_low for k in ("pending", "active", "status", "price", "count", "total", "available", "inventory", "record", "entry", "row", "element", "finding", "result")))
                    )

                    if is_inspection_task and self.driver and self.driver._page:
                        inspect_start = time.time()
                        target_status = None
                        for candidate_status in ("pending", "cancelled", "approved", "active", "out of stock", "in stock", "low stock", "rejected"):
                            if candidate_status in st_low or candidate_status in str(self.extracted_slots.get("status", "")).lower():
                                target_status = candidate_status
                                break

                        found_count = 0
                        total_records = 0
                        source_label = "DOM Table Inspection"
                        used_tool = None

                        # Extract target words representing the subject entity
                        target_words = [
                            w for w in re.findall(r"\w+", st_low)
                            if len(w) > 3 and w not in ("check", "count", "verify", "inspect", "audit", "find", "there", "were", "have", "with", "status", "page", "table", "report", "total", "from", "current", "items", "records")
                        ]

                        # A. Try WebMCP read-only query tool matching target words
                        read_tools = [t for t in tools if any(t.get("name", "").startswith(p) for p in ("get_", "list_", "search_", "query_")) and t.get("name") not in ("get_current_user_profile",)]
                        entity_tool = next((t for t in read_tools if any(tw in t.get("name", "").lower() for tw in target_words)), None) if target_words else (read_tools[0] if read_tools else None)

                        if entity_tool:
                            try:
                                t_name = entity_tool.get("name")
                                t_res = self.driver.call_tool(t_name, {})
                                if isinstance(t_res, list):
                                    total_records = len(t_res)
                                    if target_status:
                                        matching = [
                                            item for item in t_res
                                            if isinstance(item, dict) and str(item.get("status", "")).strip().upper() == target_status.upper()
                                        ]
                                        found_count = len(matching)
                                    else:
                                        found_count = len(t_res)
                                    used_tool = t_name
                                    source_label = f"WebMCP Tool ({t_name})"
                            except Exception:
                                used_tool = None

                        # B. Fallback: DOM Table / List / Structured Finding Inspection
                        is_comparative_task = any(w in active_subtask.lower() for w in ("compare", "difference", "comparison", "determine cheaper", "report lowest", "report where", "lowest price", "cheaper"))
                        is_value_extraction_task = any(w in active_subtask.lower() for w in ("price", "cost", "rate", "version", "status", "score", "count", "value"))
                        if used_tool is None and is_comparative_task:
                            curr_url = self._get_active_url()
                            curr_host = urlparse(curr_url).netloc
                            if curr_host and f"val_{curr_host}" not in self.extracted_slots and f"price_{curr_host}" not in self.extracted_slots:
                                active_val = self._extract_universal_dom_price()
                                if active_val:
                                    self.extracted_slots[f"val_{curr_host}"] = active_val

                            host_vals: Dict[str, Tuple[float, str]] = {}
                            for host_key, v_str in self.extracted_slots.items():
                                if not ((host_key.startswith("price_") or host_key.startswith("val_")) and v_str):
                                    continue
                                host_name = re.sub(r"^(?:price_|val_)", "", host_key)
                                digits = re.sub(r"[^\d.]", "", str(v_str))
                                try:
                                    num_val = float(digits)
                                except ValueError:
                                    num_val = 0.0
                                if host_name not in host_vals or (num_val > 0 and host_vals[host_name][0] == 0):
                                    host_vals[host_name] = (num_val, str(v_str))

                            parsed_vals = [(h, n, s) for h, (n, s) in host_vals.items()]

                            if len(parsed_vals) >= 2:
                                parsed_vals.sort(key=lambda x: x[1])
                                lead_host, lead_num, lead_str = parsed_vals[0]
                                other_host, other_num, other_str = parsed_vals[1]
                                spec_note = f" (Variant: {self.extracted_slots['selected_variant']})" if self.extracted_slots.get("selected_variant") else ""
                                if lead_num == other_num:
                                    summary = f"Identical price on {lead_host} and {other_host} ({lead_str}){spec_note}"
                                else:
                                    summary = f"{lead_host} ({lead_str}) vs {other_host} ({other_str}){spec_note}"
                                finding = {
                                    "subtask": active_subtask,
                                    "metric": "Comparative Data Audit",
                                    "count": len(parsed_vals),
                                    "status": "COMPLETED",
                                    "source": "Multi-site Data Audit",
                                    "summary": summary,
                                }
                            elif len(parsed_vals) == 1:
                                host_name, _, v_str = parsed_vals[0]
                                summary = f"Recorded finding on {host_name} ({v_str}), but data from second source was not available"
                                finding = {
                                    "subtask": active_subtask,
                                    "metric": "Comparative Data Audit",
                                    "count": 1,
                                    "status": "unfulfillable",
                                    "source": "Multi-site Data Audit",
                                    "summary": summary,
                                }
                            else:
                                summary = "Could not extract values from requested sources to perform audit"
                                finding = {
                                    "subtask": active_subtask,
                                    "metric": "Comparative Data Audit",
                                    "count": 0,
                                    "status": "unfulfillable",
                                    "source": "Multi-site Data Audit",
                                    "summary": summary,
                                }
                            self.extracted_data.append(finding)
                        elif used_tool is None and is_value_extraction_task and self.driver and self.driver._page:
                            val_match = self._extract_universal_dom_price()
                            curr_url = self._get_active_url()
                            curr_host = urlparse(curr_url).netloc
                            metric_desc = "Value" if not any(w in active_subtask.lower() for w in ("price", "cost", "rate")) else "Price"
                            if val_match:
                                finding = {
                                    "subtask": active_subtask,
                                    "metric": f"{metric_desc} on {curr_host}",
                                    "count": 1,
                                    "status": "RECORDED",
                                    "source": "Universal Structured DOM",
                                    "summary": f"Found {metric_desc.lower()} {val_match} on {curr_host}",
                                }
                                self.extracted_slots[f"val_{curr_host}"] = val_match
                                self.extracted_slots[f"price_{curr_host}"] = val_match
                            else:
                                finding = {
                                    "subtask": active_subtask,
                                    "metric": f"{metric_desc} on {curr_host}",
                                    "count": 0,
                                    "status": "unfulfillable",
                                    "source": "Universal Structured DOM",
                                    "summary": f"No {metric_desc.lower()} or matching target found on {curr_host}",
                                }
                            self.extracted_data.append(finding)
                        elif used_tool is None:
                            try:
                                if target_status:
                                    found_count = self.driver._page.locator(f"table tr:has-text('{target_status.upper()}'), [data-row]:has-text('{target_status.upper()}')").count()
                                else:
                                    found_count = self.driver._page.locator("table tbody tr, table tr").count()
                                total_records = self.driver._page.locator("table tbody tr, table tr").count()
                                source_label = "DOM Page Inspection"

                                # Universal non-table fallback: inspect cards, list items, or articles
                                if total_records == 0:
                                    for list_selector in (
                                        "[role='listitem']",
                                        "article, [role='article']",
                                        "tr.athing",
                                        "ul > li:has(a), ol > li:has(a)",
                                        "div[class*='card' i]",
                                        "div[class*='item' i]",
                                    ):
                                        cnt = self.driver._page.locator(list_selector).count()
                                        if cnt > 0:
                                            total_records = cnt
                                            if target_status:
                                                found_count = self.driver._page.locator(f"{list_selector}:has-text('{target_status}')").count()
                                            else:
                                                found_count = cnt
                                            source_label = f"DOM Card/List Inspection ({list_selector})"
                                            break
                            except Exception:
                                found_count = 0

                            entity_name = target_words[0].capitalize() if target_words else "Items"
                            status_prefix = f"{target_status.capitalize()} " if target_status else "Total "
                            metric_title = f"{status_prefix}{entity_name}"

                            summary_txt = f"Found {found_count} {metric_title.lower()} on current page"
                            if total_records > 0:
                                summary_txt += f" (out of {total_records} total records)"

                            finding = {
                                "subtask": active_subtask,
                                "metric": metric_title,
                                "count": found_count,
                                "total": total_records,
                                "status": (target_status or "ALL").upper(),
                                "source": source_label,
                                "summary": summary_txt,
                            }
                            self.extracted_data.append(finding)

                        report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                        os.makedirs(report_out_dir, exist_ok=True)
                        sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                        saved_path = None
                        if self.screenshot_mode in ("every_step", "major_steps"):
                            try:
                                saved_path = self.driver.take_screenshot(sc_path)
                            except Exception:
                                pass

                        inspect_dur = (time.time() - inspect_start) * 1000
                        metric_label = finding.get("metric", "Data Inspection") if isinstance(finding, dict) else "Data Inspection"
                        finding_count = finding.get("count", 0) if isinstance(finding, dict) else 0
                        source_label = finding.get("source", "Data Inspection") if isinstance(finding, dict) else "Data Inspection"

                        event = AgentStepEvent(
                            step=step_num,
                            subtask=active_subtask,
                            url=current_url,
                            title=page_title,
                            available_tools=tools,
                            chosen_action="inspect_data",
                            confidence=1.0,
                            probabilities={},
                            tool_params={"metric": metric_label, "status": target_status or "all"},
                            tool_result=finding,
                            duration_ms=inspect_dur,
                            engine_info=f"Data Inspection Gate ({source_label})",
                            screenshot_path=saved_path,
                        )
                        self._record_step(event)
                        self.history.append(f"inspect_data({json.dumps({'metric': metric_label, 'count': finding_count})})")
                        self.last_result = finding

                        self.current_subtask_idx += 1
                        continue

                    # 3. Laya MLX Decision Policy with Screen Perception and Dynamic Intent Decomposition
                    decision_start = time.time()
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
                        scroll_state=scroll_state,
                    )
                    self._step_decision_ms = (time.time() - decision_start) * 1000

                    # Goal finished
                    if chosen == "finish" or active_subtask == "Finish goal":
                        saved_path = None
                        if self.screenshot_mode in ("major_steps", "every_step"):
                            report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                            os.makedirs(report_out_dir, exist_ok=True)
                            sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                            try:
                                if self.driver and self.driver._page:
                                    saved_path = self.driver.take_screenshot(sc_path)
                            except Exception:
                                pass

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
                            screenshot_path=saved_path,
                        )
                        self._record_step(event)
                        break

                    # 4. Action Actuation (WebMCP tool, Playwright Browser Primitives, Form Input, or Click)
                    result = None
                    params: Dict[str, Any] = {}

                    if chosen == "action_blocked":
                        block_reason = fallback_params.get("reason") or "Target action is currently unavailable on this page"
                        finding = {
                            "metric": f"Action Status ({active_subtask[:30]})",
                            "status": "unfulfillable",
                            "reason": block_reason,
                            "subtask": active_subtask,
                            "url": current_url,
                        }
                        self.extracted_data.append(finding)
                        saved_path = None
                        if self.screenshot_mode in ("major_steps", "every_step"):
                            report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                            os.makedirs(report_out_dir, exist_ok=True)
                            sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                            try:
                                if self.driver and self.driver._page:
                                    saved_path = self.driver.take_screenshot(sc_path)
                            except Exception:
                                pass
                        duration = (time.time() - step_start) * 1000
                        event = AgentStepEvent(
                            step=step_num,
                            subtask=active_subtask,
                            url=current_url,
                            title=page_title,
                            available_tools=tools,
                            chosen_action="action_blocked",
                            confidence=confidence,
                            probabilities={},
                            tool_params={"reason": block_reason},
                            tool_result=f"Action Blocked: {block_reason}",
                            duration_ms=duration,
                            engine_info=engine_info,
                            screenshot_path=saved_path,
                        )
                        self._record_step(event)
                        self.history.append(f"action_blocked({json.dumps({'subtask': active_subtask, 'reason': block_reason})})")
                        self.last_result = {"status": "unfulfillable", "reason": block_reason}
                        self.current_subtask_idx += 1
                        continue

                    tool_name = chosen.replace("tool_", "")
                    is_tool = chosen.startswith("tool_") or any(t.get("name") == tool_name for t in tools)

                    if is_tool:
                        chosen = f"tool_{tool_name}"
                        tool_def = next((t for t in tools if t.get("name") == tool_name), None)
                        params = self._resolve_tool_params(
                            tool_def, screen_elements=screen_elements, fallback_params=fallback_params
                        )

                        schema = tool_def.get("inputSchema") or {} if tool_def else {}
                        required = schema.get("required") or []
                        missing_required = [r for r in required if r not in params or params[r] in (None, "")]
                        if missing_required:
                            # Cannot invoke tool without required params. Mark as unusable for this state.
                            failed_actions.add(chosen)
                            failed_actions.add(tool_name)
                            if self.engine:
                                self.engine.invalidate_cache(current_url)
                            continue

                        try:
                            raw_result = self.driver.call_tool(tool_name, params)
                            self.driver.wait_for_settled(1000)
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
                                self.driver.click(target=match_el.get("text", tool_name), x=match_el.get("x"), y=match_el.get("y"))
                                result["fallback"] = f"Clicked '{match_el['text']}'"

                    elif chosen.startswith("browser_"):
                        # Standard Playwright Browser Primitives
                        if chosen == "browser_scroll_down":
                            self.driver.mousewheel(0, 600)
                            result = {"status": "scrolled_down", "amount": 600}
                            params = {"direction": "down", "amount": 600}
                        elif chosen == "browser_scroll_up":
                            self.driver.mousewheel(0, -600)
                            result = {"status": "scrolled_up", "amount": 600}
                            params = {"direction": "up", "amount": 600}
                        elif chosen == "browser_take_screenshot":
                            report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                            os.makedirs(report_out_dir, exist_ok=True)
                            sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                            saved_path = self.driver.take_screenshot(sc_path)
                            result = {"screenshot": saved_path}
                            params = {"path": saved_path}
                        elif chosen == "browser_reload":
                            self.driver.reload()
                            result = {"status": "reloaded"}
                        elif chosen == "browser_go_back":
                            self.driver.go_back()
                            result = {"status": "navigated_back"}

                    elif chosen.startswith("input_"):
                        # Support for web applications without WebMCP (form inputs via Playwright fill)
                        field = fallback_params.get("_field") or chosen.replace("input_", "")
                        coords = fallback_params.get("_coords")
                        target_x, target_y = None, None
                        if coords and len(coords) >= 2 and coords[0] is not None and coords[1] is not None:
                            try:
                                target_x, target_y = float(coords[0]), float(coords[1])
                            except (ValueError, TypeError):
                                pass

                        # Resolve input value from fallback_params, slots, or active subtask
                        val = fallback_params.get("value") or fallback_params.get("val")
                        clean_f = field.lower().replace("_", "").replace("-", "")
                        is_search_field = (
                            clean_f in ("q", "k", "query", "search", "keyword", "keywords", "fieldkeywords", "twotabsearchtextbox")
                            or any(s in clean_f for s in ["search", "query", "find", "keyword", "twotab"])
                            or "search" in active_subtask.lower()
                        )

                        if val is None:
                            if any(u in clean_f for u in ["user", "login", "email", "account"]) and "pass" not in clean_f:
                                val = self.extracted_slots.get("username") or self.extracted_slots.get("email")
                            elif any(p in clean_f for p in ["pass", "pwd", "secret"]):
                                val = self.extracted_slots.get("password")
                            elif any(n in clean_f for n in ["name", "firstname", "lastname", "fullname"]):
                                val = self.extracted_slots.get("name") or self.extracted_slots.get("full_name") or self.extracted_slots.get("first_name")
                            elif any(ph in clean_f for ph in ["phone", "mobile", "tel", "cell"]):
                                val = self.extracted_slots.get("phone") or self.extracted_slots.get("mobile")
                            elif any(a in clean_f for a in ["address", "street", "city", "state", "zip", "postal", "pincode"]):
                                val = self.extracted_slots.get(clean_f) or self.extracted_slots.get("address") or self.extracted_slots.get("city") or self.extracted_slots.get("zip")
                            elif any(m in clean_f for m in ["message", "comment", "feedback", "notes", "body", "subject", "title"]):
                                val = self.extracted_slots.get(clean_f) or self.extracted_slots.get("message") or self.extracted_slots.get("comment") or self.extracted_slots.get("subject")
                            elif is_search_field:
                                val = (
                                    self.extracted_slots.get("search")
                                    or self.extracted_slots.get("query")
                                    or self.extracted_slots.get("product")
                                    or self.extracted_slots.get("item")
                                )
                            elif any(q in clean_f for q in ["quantity", "qty", "count"]) and clean_f != "q":
                                val = self.extracted_slots.get("quantity", 1)

                        if val is None:
                            for sk, sv in self.extracted_slots.items():
                                clean_sk = sk.lower().replace("_", "").replace("-", "")
                                if (len(clean_f) >= 3 and (clean_sk in clean_f or clean_f in clean_sk)) or clean_sk == clean_f:
                                    val = sv
                                    break

                        if val is None:
                            match_q = re.search(r"['\"](.*?)['\"]", active_subtask)
                            if match_q:
                                cand = match_q.group(1).strip()
                                val = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", cand, flags=re.IGNORECASE).strip()

                        if val is None and is_search_field:
                            m_for = re.search(r"\b(?:for|find|search)\s+([a-zA-Z0-9\s\-_]+?)(?:\s+(?:on|in|at|page|from|using)\b|$)", active_subtask, flags=re.IGNORECASE)
                            if m_for:
                                cand_val = m_for.group(1).strip()
                                cand_val = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", cand_val, flags=re.IGNORECASE).strip()
                                if len(cand_val) > 2 and cand_val.lower() not in ("the", "this", "that", "page"):
                                    val = cand_val

                        if val is not None:
                            is_search = "search" in clean_f or "search" in active_subtask.lower() or fallback_params.get("_press_enter", False)
                            success = self.driver.fill(target=field, value=str(val), x=target_x, y=target_y, press_enter=is_search)
                            result = {"filled_input": field, "value": "••••••••" if "pass" in clean_f else str(val), "success": success}
                            params = {"field": field, "value": "••••••••" if "pass" in clean_f else str(val)}
                            if is_search and success:
                                time.sleep(0.3)
                                self.driver.wait_for_settled(timeout_ms=1200)
                                if any(w in active_subtask.lower() for w in ("price", "cost", "rate", "note", "check")) or any(w in self.goal.lower() for w in ("price", "cheaper")):
                                    found_p = self._extract_universal_dom_price()
                                    if found_p:
                                        curr_url = self._get_active_url()
                                        curr_host = urlparse(curr_url).netloc
                                        self.extracted_slots[f"price_{curr_host}"] = found_p
                                        var_label = f" (Variant: {self.extracted_slots['selected_variant']})" if self.extracted_slots.get("selected_variant") else ""
                                        self.extracted_data.append({
                                            "subtask": active_subtask,
                                            "metric": f"Price on {curr_host}",
                                            "count": 1,
                                            "status": "RECORDED",
                                            "source": "Universal Currency DOM",
                                            "summary": f"Found price {found_p} on {curr_host}{var_label}",
                                        })
                        else:
                            result = {"input_error": f"Could not resolve value for input '{field}'"}

                    elif chosen.startswith("click_"):
                        # Playwright native locator click: auto-scrolls into view and clicks
                        label = chosen.replace("click_", "")
                        clean_label = label.replace("_", " ").lower().strip()
                        tok_match = re.search(r"\b([a-f0-9]{6,36})\b", clean_label)
                        tok = tok_match.group(1) if tok_match else ""
                        if not tok and any(w in clean_label for w in ("cancel", "approve", "reject", "delete")) and self.extracted_slots:
                            tok = str(self.extracted_slots.get("orderId") or self.extracted_slots.get("order_id") or self.extracted_slots.get("id") or "")

                        coords = fallback_params.get("_coords") if fallback_params else None
                        target_x, target_y = None, None
                        if coords and isinstance(coords, (tuple, list)) and len(coords) >= 2 and coords[0] is not None and coords[1] is not None:
                            try:
                                target_x, target_y = float(coords[0]), float(coords[1])
                            except (ValueError, TypeError):
                                pass

                        # Also lookup coordinate from screen_elements if not present in fallback_params
                        if target_x is None or target_y is None:
                            match_el = next((e for e in screen_elements if clean_label in e.get("text", "").lower() or (tok and tok in e.get("text", "").lower())), None)
                            if match_el and match_el.get("x") is not None and match_el.get("y") is not None:
                                target_x, target_y = float(match_el["x"]), float(match_el["y"])

                        target_name = clean_label
                        if tok and tok not in target_name:
                            target_name = f"{clean_label} {tok}"

                        # Check if this click targets a dropdown/combobox with a desired option
                        cand_meta = {}
                        if self.engine and current_url:
                            cand_meta = self.engine.site_cache.get(normalize_route_key(current_url), {}).get(chosen, {})

                        option_val = self.extracted_slots.get("status") or self.extracted_slots.get("option") or self.extracted_slots.get("filter")
                        is_dropdown = (
                            cand_meta.get("type") == "dropdown"
                            or any(w in clean_label for w in ("status", "category", "combobox", "dropdown", "select"))
                            or any(w in active_subtask.lower() for w in ("dropdown", "combobox", "select status", "select option"))
                            or any(e.get("role") == "combobox" for e in screen_elements if clean_label in e.get("text", "").lower())
                        )

                        if is_dropdown and option_val:
                            already_has_value = any(
                                str(option_val).lower() in e.get("text", "").lower()
                                for e in screen_elements
                                if e.get("role") == "combobox" or (clean_label in e.get("text", "").lower() and str(option_val).lower() in e.get("text", "").lower())
                            )
                            if already_has_value:
                                result = {"selected_dropdown": clean_label, "option": str(option_val), "success": True, "already_set": True}
                                params = {"dropdown": clean_label, "value": str(option_val)}
                            else:
                                success = self.driver.select_dropdown(target=clean_label, value=str(option_val))
                                if success:
                                    result = {"selected_dropdown": clean_label, "option": str(option_val), "success": True}
                                    params = {"dropdown": clean_label, "value": str(option_val)}
                                else:
                                    success = self.driver.click(target=target_name, x=target_x, y=target_y)
                                    result = {"clicked": target_name, "success": success}
                                    params = {"target": target_name, "x": target_x, "y": target_y}
                        else:
                            is_login_submit = any(w in clean_label for w in ("sign in", "login", "log in")) and ("login" in current_url.lower())
                            if is_login_submit and (not self.extracted_slots.get("username") or not self.extracted_slots.get("password")):
                                failed_actions.add(chosen)
                                continue

                            is_batch_task = any(w in active_subtask.lower() for w in ("cancel all", "delete all", "remove all", "approve all", "reject all", "all open", "all pending", "all orders in pending"))
                            action_verb = next((v for v in ("cancel", "delete", "remove", "approve", "reject") if v in clean_label or v in active_subtask.lower()), "")

                            if is_batch_task and action_verb and self.driver and self.driver._page:
                                # Batch execution: iterate and process all matching items on current page
                                batch_count = 0
                                while batch_count < 100:
                                    btn = self.driver._page.locator(
                                        f"button:has-text('{action_verb.capitalize()}'), button[aria-label*='{action_verb}' i], button[title*='{action_verb}' i], a:has-text('{action_verb.capitalize()}')"
                                    ).first

                                    if btn.count() == 0:
                                        break
                                    try:
                                        btn.scroll_into_view_if_needed(timeout=1000)
                                        btn.click(timeout=1000)
                                        time.sleep(0.12)
                                        batch_count += 1
                                    except Exception:
                                        break
                                success = True
                                result = {"batch_action": f"{action_verb} all", "processed_count": batch_count, "success": True}
                                params = {"target": f"{action_verb} all", "processed_count": batch_count}
                            else:
                                if any(w in clean_label for w in ("cart", "buy", "order", "purchase", "checkout", "continue", "submit")) and self.driver and self.driver._page:
                                    # Pre-actuation check: if page has unselected variant swatches/options
                                    variant_btns = find_page_variant_buttons(self.driver._page)
                                    if variant_btns and not self.extracted_slots.get("selected_variant"):
                                        if self.disambiguation_callback:
                                            opts = [{"index": i + 1, "title": b["text"], "spec": b["text"], "price": ""} for i, b in enumerate(variant_btns[:6])]
                                            ch = self.disambiguation_callback("Please select an option or variant before proceeding:", opts)
                                            if ch:
                                                ch_str = str(ch).strip()
                                                chosen_btn = None
                                                if ch_str.isdigit() and 0 <= int(ch_str) - 1 < len(variant_btns):
                                                    chosen_btn = variant_btns[int(ch_str) - 1]
                                                else:
                                                    chosen_btn = next((b for b in variant_btns if ch_str.lower() in b["text"].lower()), None)
                                                if chosen_btn:
                                                    try:
                                                        chosen_btn["element"].click()
                                                        self.extracted_slots["selected_variant"] = chosen_btn["text"]
                                                        time.sleep(0.3)
                                                    except Exception:
                                                        pass
                                        else:
                                            try:
                                                variant_btns[0]["element"].click()
                                                self.extracted_slots["selected_variant"] = variant_btns[0]["text"]
                                                time.sleep(0.3)
                                            except Exception:
                                                pass

                                success = self.driver.click(target=target_name, x=target_x, y=target_y)
                                result = {"clicked": target_name, "success": success}
                                params = {"target": target_name, "x": target_x, "y": target_y}

                    elif chosen == "other":
                        # Target element was not in view. If page allows scrolling, perform exploratory mousewheel
                        if scroll_state.get("can_scroll_down") and consecutive_scrolls < 3:
                            self.driver.mousewheel(0, 600)
                            consecutive_scrolls += 1
                            chosen = "browser_scroll_down"
                            result = {"status": "exploratory_scroll_down", "attempt": consecutive_scrolls}
                            params = {"direction": "down", "amount": 600}
                            if self.engine:
                                self.engine.invalidate_cache(current_url)
                        else:
                            result = {"status": "no_matching_affordance", "action": "other"}
                            params = {}


                    self.last_result = result
                    self.history.append(f"{chosen}({json.dumps(params)})")

                    # Check for errors and invalidate cache if needed
                    if isinstance(result, dict) and any(k in result for k in ("tool_error", "input_error")):
                        failed_actions.add(chosen)
                        if self.engine:
                            self.engine.invalidate_cache(current_url)

                    # Fast dynamic settling after actuation
                    time.sleep(0.1)
                    self.driver.wait_for_settled(timeout_ms=1000)
                    new_url = self.driver.get_active_url()
                    fresh_elements = self.driver.get_screen_elements()

                    # If login subtask, wait up to 3500ms for async redirect
                    is_login_subtask = any(w in active_subtask.lower() for w in ("login", "log in", "signin", "sign in", "authenticate", "credential", "auth"))
                    if is_login_subtask and (new_url.rstrip("/").endswith("/login") or "login" in new_url.lower()):
                        try:
                            if self.driver and self.driver._page:
                                self.driver._page.wait_for_url(lambda u: not u.rstrip("/").endswith("/login") and "login" not in u.lower(), timeout=3500)
                                new_url = self.driver.get_active_url()
                                fresh_elements = self.driver.get_screen_elements()
                        except Exception:
                            pass

                    # Closed-Loop State Verification Gate (Assert Engine)
                    assert_passed, assert_reason = self._assert_step_transition(
                        subtask=active_subtask,
                        action=chosen,
                        params=params,
                        result=result,
                        before_url=current_url,
                        after_url=new_url,
                        before_elements=screen_elements,
                        after_elements=fresh_elements,
                    )

                    # Anti-Doom Loop: track attempts of the same action on the same page
                    state_key = f"{normalize_route_key(current_url)}:{chosen}"
                    state_action_history[state_key] = state_action_history.get(state_key, 0) + 1
                    if (state_action_history[state_key] >= 2 and new_url == current_url) or not assert_passed:
                        failed_actions.add(chosen)
                        if self.engine:
                            self.engine.invalidate_cache(current_url)

                    if not assert_passed:
                        saved_path = None
                        if self.screenshot_mode in ("major_steps", "every_step"):
                            report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                            os.makedirs(report_out_dir, exist_ok=True)
                            sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                            try:
                                if self.driver and self.driver._page:
                                    saved_path = self.driver.take_screenshot(sc_path)
                            except Exception:
                                pass

                        # Loop Engineering: Track subtask stagnation and attempt count
                        subtask_fail_count = self._subtask_failures.get(self.current_subtask_idx, 0) + 1
                        self._subtask_failures[self.current_subtask_idx] = subtask_fail_count

                        engine_note = f"Verification Gate Failed ({assert_reason})"

                        # 1. Level 1 Recovery: Check if failure was caused by being on the wrong website/domain
                        remediation_taken = False
                        target_url = self._extract_navigation_target(active_subtask, new_url)
                        if target_url:
                            tgt_netloc = urlparse(target_url).netloc.lower().lstrip("www.")
                            cur_netloc = urlparse(new_url).netloc.lower().lstrip("www.")
                            if tgt_netloc and cur_netloc != tgt_netloc:
                                # Direct recovery: navigate to the target domain intended by the subtask
                                self.driver.navigate_to(target_url)
                                self.driver.wait_for_settled(1200)
                                failed_actions.clear()
                                consecutive_scrolls = 0
                                remediation_taken = True
                                engine_note = f"Loop Engineering Recovery: Navigated to correct domain ({target_url})"

                        # 2. Level 2 Recovery: If subtask failed >= 2 times, introspect current browser state and alter plan
                        if not remediation_taken and subtask_fail_count >= 2:
                            live_url = self.driver.get_active_url()
                            live_title = self.driver.get_page_title()
                            live_elements = self.driver.get_screen_elements()
                            remaining = self.subtasks[self.current_subtask_idx + 1:] if (self.current_subtask_idx + 1 < len(self.subtasks)) else []

                            altered_tasks = self.extractor.remediate_plan_from_screen(
                                goal=self.goal,
                                failed_subtask=active_subtask,
                                failure_reason=assert_reason,
                                current_url=live_url,
                                page_title=live_title,
                                screen_elements=live_elements,
                                remaining_subtasks=remaining,
                            )

                            if altered_tasks and altered_tasks != [active_subtask]:
                                # Replace current subtask with altered tasks while preserving downstream subtasks
                                preserved_remaining = [
                                    st for st in remaining
                                    if not any(st.lower() in alt.lower() or alt.lower() in st.lower() for alt in altered_tasks)
                                ]
                                self.subtasks = self.subtasks[:self.current_subtask_idx] + altered_tasks + preserved_remaining
                                failed_actions.clear()
                                consecutive_scrolls = 0
                                engine_note = f"Loop Engineering: Plan altered from browser state ({len(altered_tasks)} revised steps, {len(self.subtasks)} total)"
                            else:
                                # Record audit finding and advance to next subtask to avoid doom loop
                                finding_entry = {
                                    "subtask": active_subtask,
                                    "status": "unfulfillable",
                                    "reason": assert_reason,
                                    "url": live_url,
                                }
                                self.extracted_data.append(finding_entry)
                                self.extracted_slots[f"finding_{self.current_subtask_idx}"] = finding_entry
                                self.current_subtask_idx += 1
                                failed_actions.clear()
                                consecutive_scrolls = 0
                                engine_note = f"Loop Engineering: Subtask unfulfillable ({assert_reason}). Advancing plan."

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
                            tool_result={"assertion_failed": assert_reason, "result": result},
                            duration_ms=(time.time() - step_start) * 1000,
                            engine_info=engine_note,
                            screenshot_path=saved_path,
                        )
                        self._record_step(event)
                        continue


                    prev_subtask_idx = self.current_subtask_idx

                    is_failed_action = (
                        result is False
                        or (isinstance(result, dict) and (result.get("success") is False or "tool_error" in result or result.get("status") in ("failed", "error", "no_matching_affordance")))
                    )
                    # Advance subtask only if action succeeded or produced a valid fallback
                    if not is_failed_action:
                        # Intermediate navigation check: if we clicked a navigation link/tool to reach the section needed
                        coords = params.get("_coords") or (params.get("x"), params.get("y"))
                        cx = coords[0] if (coords and len(coords) >= 2) else None
                        cy = coords[1] if (coords and len(coords) >= 2) else None

                        cand_meta = {}
                        if self.engine and current_url:
                            cand_meta = self.engine.site_cache.get(normalize_route_key(current_url), {}).get(chosen, {})

                        is_nav_step = not chosen.startswith("input_") and (
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
                        has_search_slot = bool(self.extracted_slots.get("search") or self.extracted_slots.get("query") or self.extracted_slots.get("product"))
                        subtask_has_target_item = has_search_slot and any(
                            len(w) > 3 and w in active_subtask.lower()
                            for w in re.findall(r"\w+", str(self.extracted_slots.get("search") or self.extracted_slots.get("product") or "").lower())
                        )
                        is_operational_subtask = any(w in active_subtask.lower() for w in operational_keywords) or subtask_has_target_item
                        is_login_action = "login" in chosen.lower()
                        subtask_is_login = any(w in active_subtask.lower() for w in ("login", "signin", "authenticate", "credential"))
                        is_batch_subtask = any(w in active_subtask.lower().split() for w in ("all", "every", "each"))

                        is_primitive_action = chosen.startswith("browser_")
                        subtask_mentions_primitive = any(
                            w in active_subtask.lower()
                            for w in ("screenshot", "snapshot", "scroll", "reload", "refresh", "go back", "back")
                        )

                        # Subtasks that specifically mandate input/typing or searching MUST NOT advance on a simple click
                        subtask_demands_input = (
                            any(w in active_subtask.lower() for w in ("enter", "fill", "type", "input", "submit search", "type into"))
                            and not any(w in active_subtask.lower() for w in ("click to add", "add to cart", "add it to cart", "buy", "purchase", "select", "checkout", "place order", "click"))
                        )
                        action_did_input = chosen.startswith("input_") or bool(params.get("_input")) or bool(params.get("field"))

                        # Detect affirmative action fulfillment: does chosen action complete the subtask intent?
                        act_clean = chosen.lower().replace("-", "_")
                        sub_clean = active_subtask.lower()
                        action_fulfills_subtask = False

                        action_intent_pairs = [
                            ("add_to_cart", ("add to cart", "add it to cart", "add item", "to cart")),
                            ("buy", ("buy", "purchase", "place order")),
                            ("order", ("order", "place order")),
                            ("cancel", ("cancel",)),
                            ("approve", ("approve",)),
                            ("reject", ("reject",)),
                            ("delete", ("delete", "remove")),
                            ("checkout", ("checkout", "check out")),
                            ("logout", ("logout", "sign out", "signout")),
                            ("login", ("login", "sign in", "signin")),
                            ("browser_switch_tab", ("switch to", "switch tab")),
                            ("browser_new_tab", ("open a new tab", "new tab")),
                        ]
                        for act_token, sub_tokens in action_intent_pairs:
                            if act_token in act_clean and any(stk in sub_clean for stk in sub_tokens):
                                action_fulfills_subtask = True
                                break

                        if not action_fulfills_subtask and act_clean.startswith("click_") and not subtask_demands_input:
                            target_label = act_clean.replace("click_", "")
                            if len(target_label) >= 4 and target_label in sub_clean.replace(" ", "_"):
                                action_fulfills_subtask = True

                        if subtask_demands_input and not action_did_input:
                            # Clicking an icon, link, or button must not advance a subtask that demands entering text!
                            pass
                        elif action_fulfills_subtask:
                            # Action directly fulfilled the active subtask's primary objective!
                            self.current_subtask_idx += 1
                            consecutive_scrolls = 0
                        elif is_primitive_action and is_operational_subtask and not subtask_mentions_primitive:
                            # Exploratory viewport scroll/refresh to locate an off-screen element
                            # must NOT advance the operational subtask!
                            pass
                        elif is_login_action and not subtask_is_login:
                            # Retain active operational subtask for post-login execution
                            pass
                        elif subtask_is_login and not (new_url.rstrip("/").endswith("/login") or "login" in new_url.lower()):
                            # Session is authenticated and navigated away from login page; advance subtask
                            self.current_subtask_idx += 1
                            consecutive_scrolls = 0
                        elif is_nav_step and is_operational_subtask:
                            # Navigation is only an intermediate move to reach the page where the operation lives;
                            # never advance operational subtasks on mere navigation clicks!
                            pass
                        elif is_batch_subtask:
                            # For batch subtasks (e.g. "cancel all open orders" or multi-page pagination)
                            if self.engine:
                                self.engine.invalidate_cache(new_url)
                            fresh_elements = self.driver.get_screen_elements()
                            is_pagination_subtask = any(w in active_subtask.lower() for w in ("page", "pagination", "next page"))
                            if is_pagination_subtask:
                                has_next = any(
                                    "next" in el.get("text", "").lower() and not el.get("disabled")
                                    for el in fresh_elements
                                    if el.get("tag") in ("button", "a")
                                )
                                if has_next and chosen not in ("finish", "tool_logout_user"):
                                    pass
                                else:
                                    self.current_subtask_idx += 1
                                    consecutive_scrolls = 0
                            else:
                                action_verb = next((v for v in ("cancel", "delete", "remove", "approve", "reject", "buy", "order") if v in active_subtask.lower()), "")
                                more_exist = False
                                if action_verb:
                                    more_exist = any(
                                        action_verb in el.get("text", "").lower()
                                        for el in fresh_elements
                                        if el.get("tag") in ("button", "a") and not el.get("disabled")
                                    )
                                if more_exist:
                                    # Keep subtask active to process next item in batch
                                    pass
                                else:
                                    self.current_subtask_idx += 1
                                    consecutive_scrolls = 0
                        else:
                            self.current_subtask_idx += 1
                            consecutive_scrolls = 0

                    duration = (time.time() - step_start) * 1000
                    is_subtask_advanced = (self.current_subtask_idx != prev_subtask_idx)

                    if is_subtask_advanced and not is_failed_action:
                        # Cache verified subtask macro ONLY when subtask actually advanced!
                        if self.engine and current_url:
                            parsed_u = urlparse(current_url)
                            origin = f"{parsed_u.scheme}://{parsed_u.netloc}"
                            route = normalize_route_key(current_url)
                            self.engine.cache_subtask_macro(
                                origin=origin,
                                route=route,
                                subtask=active_subtask,
                                action=chosen,
                                params=params,
                                confidence=confidence,
                            )
                    else:
                        # Action did not advance subtask: isolate fault, prevent repeat, and invalidate stale macros
                        failed_actions.add(chosen)
                        if current_url:
                            from .cache import global_prompt_cache
                            parsed_u = urlparse(current_url)
                            origin = f"{parsed_u.scheme}://{parsed_u.netloc}"
                            route = normalize_route_key(current_url)
                            global_prompt_cache.invalidate_subtask_macro(origin, route, active_subtask)
                            global_prompt_cache.invalidate_recent_prompts(["screen_locator"], limit=3)

                        # Anti-stagnation guard: If the same subtask has taken 4+ steps on this page without advancing,
                        # advance subtask to prevent doom loop
                        if self._subtask_step_counts.get(self.current_subtask_idx, 0) >= 4:
                            self.current_subtask_idx += 1
                            consecutive_scrolls = 0

                    is_major_action = (
                        chosen in ("tool_logout_user", "tool_user_login", "tool_place_order")
                        or chosen.startswith("click_")
                        or "status" in chosen.lower()
                        or (isinstance(result, dict) and any(k in result for k in ("tool_error", "input_error")))
                    )
                    is_major_step = is_subtask_advanced or is_major_action or chosen.startswith("browser_take_screenshot")

                    take_sc = (
                        self.screenshot_mode == "every_step"
                        or (self.screenshot_mode == "major_steps" and is_major_step)
                    )

                    saved_path = None
                    if take_sc:
                        report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                        os.makedirs(report_out_dir, exist_ok=True)
                        sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                        try:
                            if self.driver and self.driver._page:
                                saved_path = self.driver.take_screenshot(sc_path)
                        except Exception:
                            pass

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
                        screenshot_path=saved_path,
                    )
                    self._record_step(event)

                    consecutive_step_errors = 0
                except Exception as step_exc:
                    consecutive_step_errors += 1
                    err_msg = str(step_exc)
                    logger.error(f"Step {step_num} error intercepted by Loop Engineering: {err_msg}", exc_info=True)

                    # 1. Dismiss any transient blocking popups or dialogs that might have triggered failure
                    try:
                        if self.driver:
                            self.driver.dismiss_transient_overlays()
                    except Exception:
                        pass

                    # 2. Invalidate cache and record failed action
                    if 'chosen' in locals() and chosen:
                        failed_actions.add(chosen)
                    if self.engine and 'current_url' in locals() and current_url:
                        self.engine.invalidate_cache(current_url)

                    # 3. Track subtask failures
                    subtask_fail_count = self._subtask_failures.get(self.current_subtask_idx, 0) + 1
                    self._subtask_failures[self.current_subtask_idx] = subtask_fail_count

                    # 4. Self-Healing Loop Engineering via Local Models & Screen Perception
                    curr_st = active_subtask if 'active_subtask' in locals() else (
                        self.subtasks[self.current_subtask_idx] if self.current_subtask_idx < len(self.subtasks) else self.goal
                    )
                    engine_note = f"Loop Engineering Error Intercept ({err_msg[:60]})"
                    try:
                        live_url = self.driver.get_active_url() if (self.driver and self.driver._page and not self.driver._page.is_closed()) else (current_url if 'current_url' in locals() else "")
                        live_title = self.driver.get_page_title() if (self.driver and self.driver._page and not self.driver._page.is_closed()) else (page_title if 'page_title' in locals() else "")
                        live_elements = self.driver.get_screen_elements() if (self.driver and self.driver._page and not self.driver._page.is_closed()) else []
                        remaining = self.subtasks[self.current_subtask_idx + 1:] if (self.current_subtask_idx + 1 < len(self.subtasks)) else []

                        altered_tasks = self.extractor.remediate_plan_from_screen(
                            goal=self.goal,
                            failed_subtask=curr_st,
                            failure_reason=err_msg,
                            current_url=live_url,
                            page_title=live_title,
                            screen_elements=live_elements,
                            remaining_subtasks=remaining,
                        )
                        if altered_tasks and altered_tasks != [curr_st]:
                            preserved_remaining = [
                                st for st in remaining
                                if not any(st.lower() in alt.lower() or alt.lower() in st.lower() for alt in altered_tasks)
                            ]
                            self.subtasks = self.subtasks[:self.current_subtask_idx] + altered_tasks + preserved_remaining
                            failed_actions.clear()
                            consecutive_scrolls = 0
                            engine_note = f"Loop Engineering: Plan altered from browser state ({len(altered_tasks)} revised steps, {len(self.subtasks)} total)"
                        else:
                            # Record audit finding and advance to next subtask to avoid trapping the agent
                            finding_entry = {
                                "subtask": curr_st,
                                "status": "unfulfillable",
                                "reason": f"Step error: {err_msg}",
                                "url": live_url,
                            }
                            self.extracted_data.append(finding_entry)
                            self.extracted_slots[f"finding_{self.current_subtask_idx}"] = finding_entry
                            self.current_subtask_idx += 1
                            failed_actions.clear()
                            consecutive_scrolls = 0
                            engine_note = f"Loop Engineering: Subtask advanced after error ({err_msg[:60]})"
                    except Exception as rem_exc:
                        logger.warning(f"Loop Engineering remediation error: {rem_exc}")
                        self.current_subtask_idx += 1

                    # 5. Capture event and screenshot for transparent reporting
                    saved_path = None
                    try:
                        if self.screenshot_mode in ("every_step", "major_steps") and self.driver and self.driver._page and not self.driver._page.is_closed():
                            report_out_dir = os.path.dirname(self.report_file) or self.report_dir
                            os.makedirs(report_out_dir, exist_ok=True)
                            sc_path = os.path.join(report_out_dir, f"screenshot_step_{step_num}.png")
                            saved_path = self.driver.take_screenshot(sc_path)
                    except Exception:
                        pass

                    event = AgentStepEvent(
                        step=step_num,
                        subtask=curr_st,
                        url=live_url if 'live_url' in locals() else "",
                        title=live_title if 'live_title' in locals() else "",
                        available_tools=tools if 'tools' in locals() else [],
                        chosen_action=chosen if 'chosen' in locals() else "step_error_recovery",
                        confidence=0.0,
                        probabilities={},
                        tool_params={},
                        tool_result={"error": err_msg, "step_recovered": True},
                        duration_ms=(time.time() - step_start) * 1000,
                        engine_info=engine_note,
                        screenshot_path=saved_path,
                    )
                    self._record_step(event)

                    # Only abort if browser is dead or consecutive unrecoverable crashes exceed threshold
                    if consecutive_step_errors >= 5 or (self.driver and self.driver._page and self.driver._page.is_closed()):
                        execution_error = f"Exceeded consecutive error threshold ({consecutive_step_errors}): {err_msg}"
                        break
                    continue
        except Exception as fatal_exc:
            execution_error = str(fatal_exc)
            logger.error(f"Fatal error during agent execution: {fatal_exc}", exc_info=True)
        finally:
            final_url = self.driver.get_active_url() if (self.driver and self.driver._page and not self.driver._page.is_closed()) else self.target_url
            report_out_path = self.report_file
            report_out_dir = os.path.dirname(report_out_path) or self.report_dir
            os.makedirs(report_out_dir, exist_ok=True)
            screenshot_path = os.path.join(report_out_dir, "final_execution_state.png")
            screenshot_taken = False
            if self.screenshot_mode in ("major_steps", "every_step"):
                try:
                    if self.driver and self.driver._page and not self.driver._page.is_closed():
                        final_sc = self.driver.take_screenshot(screenshot_path, full_page=True)
                        if final_sc:
                            screenshot_path = final_sc
                            screenshot_taken = True
                except Exception:
                    screenshot_taken = False

            if not self.headed and self._owns_driver and self.driver:
                self.driver.close()

        total_elapsed = time.time() - start_time
        status_code, status_badge, _, _ = evaluate_run_status(self, execution_error)
        is_success = (status_code == "success")

        report_file_written = None
        html_report_written = None
        if self.generate_report_enabled:
            self.generate_report(
                report_out_path,
                final_screenshot=screenshot_path if screenshot_taken else None,
                final_url=final_url,
                execution_error=execution_error,
            )
            report_file_written = report_out_path

            html_out_path = report_out_path.rsplit(".", 1)[0] + ".html"
            self.generate_html_report(
                html_out_path,
                final_screenshot=screenshot_path if screenshot_taken else None,
                final_url=final_url,
                execution_error=execution_error,
            )
            html_report_written = html_out_path

        status_str = status_code
        return {
            "status": status_str,
            "success": is_success,
            "error": execution_error,
            "steps": len(self.steps_taken),
            "total_time_seconds": total_elapsed,
            "history": self.history,
            "last_result": self.last_result,
            "extracted_data": self.extracted_data,
            "telemetry": self.get_session_telemetry(),
            "report_file": report_file_written,
            "html_report_file": html_report_written,
            "screenshot": screenshot_path if screenshot_taken else None,
        }

    def close(self):
        """Closes browser driver session."""
        if self._owns_driver and self.driver:
            self.driver.close()

    def generate_report(
        self,
        output_path: Optional[str] = None,
        final_screenshot: Optional[str] = None,
        final_url: Optional[str] = None,
        execution_error: Optional[str] = None,
    ) -> str:
        """Generates a detailed execution report in Markdown format."""
        return generate_markdown_report(
            agent=self,
            output_path=output_path,
            final_screenshot=final_screenshot,
            final_url=final_url,
            execution_error=execution_error,
        )

    def generate_html_report(
        self,
        output_path: Optional[str] = None,
        final_screenshot: Optional[str] = None,
        final_url: Optional[str] = None,
        execution_error: Optional[str] = None,
    ) -> str:
        """Generates a modern, responsive HTML execution report dashboard."""
        return generate_html_dashboard(
            agent=self,
            output_path=output_path,
            final_screenshot=final_screenshot,
            final_url=final_url,
            execution_error=execution_error,
        )

    def _assert_step_transition(
        self,
        subtask: str = "",
        action: str = "",
        params: Optional[Dict[str, Any]] = None,
        result: Any = None,
        before_url: str = "",
        after_url: str = "",
        before_elements: Optional[List[Dict[str, Any]]] = None,
        after_elements: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, str]:
        """Validates physical action execution integrity."""
        return assert_step_transition(
            subtask=subtask,
            action=action,
            params=params or {},
            result=result,
            before_url=before_url,
            after_url=after_url,
            before_elements=before_elements or [],
            after_elements=after_elements or [],
            extracted_slots=self.extracted_slots,
        )


    def _resolve_tool_params(
        self,
        tool_def: Optional[Dict[str, Any]],
        screen_elements: Optional[List[Dict[str, Any]]] = None,
        fallback_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Universally maps slots, schema properties, and prior step results to tool parameters."""
        active_subtask = (
            self.subtasks[self.current_subtask_idx]
            if (self.subtasks and self.current_subtask_idx < len(self.subtasks))
            else self.goal
        )
        return resolve_tool_params(
            tool_def=tool_def,
            extracted_slots=self.extracted_slots,
            last_result=self.last_result,
            subtask=active_subtask,
            goal=self.goal,
            screen_elements=screen_elements,
            fallback_params=fallback_params,
            driver=self.driver,
        )

    def _find_matching_screen_element(self, tool_name: str, elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return find_matching_screen_element(tool_name, elements)

    def _find_contextual_entity(self, prop_name: str, prop_desc: str, context: Any) -> Any:
        return find_contextual_entity(prop_name, prop_desc, context, self.extracted_slots)

    def _infer_path_from_subtask(self, screen_elements: Optional[List[Dict[str, Any]]] = None) -> str:
        active_subtask = (
            self.subtasks[self.current_subtask_idx]
            if (self.subtasks and self.current_subtask_idx < len(self.subtasks))
            else self.goal
        )
        return infer_path_from_subtask(active_subtask, self.goal, screen_elements, self.extracted_slots)

    def _expand_id_prefix(self, prefix: str, prop_name: str) -> Optional[str]:
        return expand_id_prefix(prefix, prop_name, self.last_result, self.driver)

    def _find_matching_id_in_data(self, data: Any, prefix: str) -> Optional[str]:
        return find_matching_id_in_data(data, prefix)

    def _parse_tool_result(self, raw_result: Any) -> Any:
        return parse_tool_result(raw_result)