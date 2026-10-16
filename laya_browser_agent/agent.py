"""Universal Autonomous Browser Agent powered by Laya MLX decision models and WebMCP."""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .browser import PlaywrightWebMcpDriver
from .extractor import SlotExtractor
from .laya_engine import LayaEngine


@dataclass
class AgentStepEvent:
    """Telemetry event emitted on each step of the agent execution."""

    step: int
    url: str
    title: str
    available_tools: List[Dict[str, Any]]
    chosen_action: str
    confidence: float
    probabilities: Dict[str, float]
    tool_params: Dict[str, Any]
    tool_result: Any
    duration_ms: float


class LayaAgent:
    """
    Universal autonomous browser agent.
    
    Works with ANY web application exposing WebMCP tools or declarative forms.
    Supports both headed (visible) and headless browser modes.
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
    ):
        self.target_url = target_url
        self.goal = goal
        self.headed = bool(headed)
        self.max_steps = max_steps
        self.model_id = model_id
        self.ollama_model = ollama_model
        self.event_callback = event_callback

        self.driver: Optional[PlaywrightWebMcpDriver] = None
        self.engine: Optional[LayaEngine] = None
        self.extractor: SlotExtractor = SlotExtractor(ollama_model=self.ollama_model)

        self.extracted_slots: Dict[str, Any] = {}
        self.history: List[str] = []
        self.last_result: Any = None
        self.steps_taken: List[AgentStepEvent] = []

    def run(self) -> Dict[str, Any]:
        """Runs the autonomous decision and action loop."""
        start_time = time.time()

        if not self.engine:
            self.engine = LayaEngine(model_id=self.model_id)
        if not self.driver:
            self.driver = PlaywrightWebMcpDriver(headed=self.headed)

        # 1. Generic parameter extraction from user goal
        self.extracted_slots = self.extractor.extract(self.goal)

        # 2. Launch browser at target URL (headed or headless)
        self.driver.start(self.target_url)

        try:
            for step_num in range(1, self.max_steps + 1):
                step_start = time.time()

                # Settle page and discover registered tools
                self.driver.wait_for_settled(timeout_ms=2500)
                current_url = self.driver.get_active_url()
                page_title = self.driver.get_page_title()
                tools = self.driver.discover_tools()

                if not tools:
                    time.sleep(0.5)
                    tools = self.driver.discover_tools()

                # 3. Laya MLX Decision Policy
                chosen, confidence, probs = self.engine.decide_next_action(
                    goal=self.goal,
                    current_url=current_url,
                    available_tools=tools,
                    recent_history=self.history,
                )

                # Goal finished
                if chosen == "finish":
                    duration = (time.time() - step_start) * 1000
                    event = AgentStepEvent(
                        step=step_num,
                        url=current_url,
                        title=page_title,
                        available_tools=tools,
                        chosen_action="finish",
                        confidence=confidence,
                        probabilities=probs,
                        tool_params={},
                        tool_result="Goal completed successfully",
                        duration_ms=duration,
                    )
                    self.steps_taken.append(event)
                    if self.event_callback:
                        self.event_callback(event)
                    break

                # 4. Universal parameter resolution from schema and context
                tool_def = next((t for t in tools if t.get("name") == chosen), None)
                params = self._resolve_tool_params(tool_def)

                # 5. Programmatic WebMCP tool execution
                try:
                    raw_result = self.driver.call_tool(chosen, params)
                    result = self._parse_tool_result(raw_result)
                except Exception as e:
                    result = {"error": str(e)}

                self.last_result = result
                self.history.append(f"{chosen}({json.dumps(params)})")

                duration = (time.time() - step_start) * 1000
                event = AgentStepEvent(
                    step=step_num,
                    url=current_url,
                    title=page_title,
                    available_tools=tools,
                    chosen_action=chosen,
                    confidence=confidence,
                    probabilities=probs,
                    tool_params=params,
                    tool_result=result,
                    duration_ms=duration,
                )
                self.steps_taken.append(event)
                if self.event_callback:
                    self.event_callback(event)

                # Route settling
                self.driver.wait_for_settled(timeout_ms=2000)

            total_elapsed = time.time() - start_time
            is_success = bool(self.steps_taken and self.steps_taken[-1].chosen_action == "finish")

            return {
                "status": "success" if is_success else "max_steps_reached",
                "steps": len(self.steps_taken),
                "total_time_seconds": total_elapsed,
                "history": self.history,
                "last_result": self.last_result,
            }

        finally:
            if not self.headed:
                # Always close immediately in headless mode
                self.driver.close()

    def _resolve_tool_params(self, tool_def: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Universally maps slots, schema properties, and prior step results to tool parameters."""
        if not tool_def:
            return {}

        schema = tool_def.get("inputSchema") or {}
        properties: Dict[str, Any] = schema.get("properties") or {}
        resolved: Dict[str, Any] = {}

        if not properties:
            # If tool requires no properties or none are declared, return any matching extracted slots
            return {}

        for prop_name, prop_meta in properties.items():
            prop_type = prop_meta.get("type", "string") if isinstance(prop_meta, dict) else "string"
            prop_desc = (prop_meta.get("description", "") if isinstance(prop_meta, dict) else "").lower()

            value = None

            # A. Exact or normalized slot match
            norm_prop = prop_name.lower().replace("_", "").replace("-", "")
            for slot_k, slot_v in self.extracted_slots.items():
                norm_slot = slot_k.lower().replace("_", "").replace("-", "")
                if norm_slot == norm_prop:
                    value = slot_v
                    break

            # B. Common aliases (username/user, password/pass, query/search/filter, count/quantity)
            if value is None:
                aliases = {
                    "username": ["user", "login", "email", "account"],
                    "password": ["pass", "pwd", "secret"],
                    "search": ["query", "q", "filter", "keyword"],
                    "query": ["search", "q", "filter", "keyword"],
                    "quantity": ["qty", "count", "amount", "number"],
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

            # D. Navigation paths inferred from user prompt if tool has a 'path' or 'url' param
            if value is None and prop_name in ("path", "url", "route"):
                value = self._infer_path_from_goal(self.goal)

            # E. Type casting
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
        is_id_field = "id" in prop_name.lower() or "id" in prop_desc

        if isinstance(context, list) and context:
            # List of items: pick first item having an ID or matching field
            for item in context:
                if isinstance(item, dict):
                    if is_id_field and "id" in item:
                        # Prefer in-stock or active item if available
                        if item.get("quantity", 1) > 0:
                            return item["id"]
                    if prop_name in item:
                        return item[prop_name]
            # Fallback to first item's ID
            if is_id_field and isinstance(context[0], dict) and "id" in context[0]:
                return context[0]["id"]

        elif isinstance(context, dict):
            # Check nested object or direct key
            if prop_name in context:
                return context[prop_name]
            for val in context.values():
                if isinstance(val, dict) and prop_name in val:
                    return val[prop_name]
            if is_id_field and "id" in context:
                return context["id"]

        return None

    def _infer_path_from_goal(self, goal: str) -> str:
        """Infers route or path mentioned or implied in the user goal."""
        import re
        # Look for explicit paths in goal (e.g. /products, /settings, /orders)
        match = re.search(r"(/[a-zA-Z0-9_\-]+)", goal)
        if match:
            return match.group(1)
        return "/"

    def _parse_tool_result(self, raw_result: Any) -> Any:
        """Parses tool return value into structured JSON if returned as a string."""
        if isinstance(raw_result, str):
            try:
                return json.loads(raw_result)
            except Exception:
                return raw_result
        return raw_result

    def close(self):
        """Closes browser session."""
        if self.driver:
            self.driver.close()
