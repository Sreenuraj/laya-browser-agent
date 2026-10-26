"""Laya MLX Decision Engine with Screen Perception, Dynamic Candidate Extraction, and Zero-Shot Policy."""

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from laya_mlx import Agent
    HAS_LAYA_MLX = True
except (ImportError, Exception):
    Agent = None
    HAS_LAYA_MLX = False

from .affordance import extract_page_candidates, normalize_route_key, tool_matches_subtask
from .cache import global_prompt_cache
from .llm import get_llm_client, resolve_local_model


_CACHED_MLX_AGENTS: Dict[Tuple[str, str], Any] = {}


class LayaEngine:
    """
    Sub-15ms System 1 decision engine running on Apple Silicon GPU via Laya MLX.
    Uses calibrated zero-shot choice evaluation over live page affordances, with
    local LLM fallback when confidence is low or required action is off-screen.
    """

    def __init__(
        self,
        model_id: str = "aac6fef/laya-mlx",
        device: str = "gpu",
        confidence_threshold: float = 0.75,
        ollama_url: str = "http://localhost:11434",
        ollama_model: Optional[str] = None,
    ):
        self.model_id = model_id
        self.confidence_threshold = confidence_threshold
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model or resolve_local_model(preferred=None, base_url=self.ollama_url)
        self.site_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.telemetry: Dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_reads": 0,
            "cache_writes": 0,
            "latency_saved_ms": 0.0,
        }

        self.agent = None
        if HAS_LAYA_MLX and Agent is not None:
            cache_key = (model_id, device)
            if cache_key in _CACHED_MLX_AGENTS:
                self.agent = _CACHED_MLX_AGENTS[cache_key]
            else:
                try:
                    self.agent = Agent(model_id, device=device)
                    _CACHED_MLX_AGENTS[cache_key] = self.agent
                except Exception:
                    try:
                        self.agent = Agent(model_id, device="cpu")
                        # Store under the original key so we don't retry the slow
                        # Metal load on every subsequent LayaEngine instantiation.
                        _CACHED_MLX_AGENTS[cache_key] = self.agent
                    except Exception:
                        self.agent = None

    def invalidate_cache(self, url: str):
        """Evicts cached candidates for the specified URL route."""
        route_key = normalize_route_key(url)
        self.site_cache.pop(route_key, None)

    def verify_macro_affordance(self, macro: Dict[str, Any], candidates: Dict[str, Any]) -> bool:
        """Verifies if the cached macro action is physically present in the live screen candidates."""
        if not isinstance(macro, dict):
            return False
        action = str(macro.get("action", ""))
        if not action:
            return False
        if action in candidates:
            return True
        tool_name = action.replace("tool_", "")
        return any(k.replace("tool_", "") == tool_name for k in candidates.keys())

    def decide_next_action(
        self,
        subtask: str,
        goal: str,
        current_url: str,
        available_tools: List[Dict[str, Any]],
        screen_elements: List[Dict[str, Any]],
        recent_history: List[str],
        last_result: Any = None,
        extracted_slots: Optional[Dict[str, Any]] = None,
        excluded_actions: Optional[List[str]] = None,
        force_refresh: bool = False,
        scroll_state: Optional[Dict[str, Any]] = None,
        can_go_back: bool = False,
    ) -> Tuple[str, float, Dict[str, Any], str]:
        """
        Evaluates current screen affordances to decide the next action in a single forward pass.
        Returns: (chosen_action, confidence, params, engine_info)
        """
        route_key = normalize_route_key(current_url)
        excluded_set: Set[str] = set(excluded_actions or [])

        # Live extraction of page capabilities (CDP query in Playwright takes ~2ms)
        candidates = extract_page_candidates(
            available_tools=available_tools,
            screen_elements=screen_elements,
            current_url=current_url,
            excluded_actions=excluded_set,
            scroll_state=scroll_state,
            can_go_back=can_go_back,
        )
        self.site_cache[route_key] = candidates

        if not candidates:
            return "finish", 1.0, {}, "No actionable elements on screen"

        # Check Parent-Scoped Subtask Cache with Live Affordance Verification
        from laya_browser_agent.cache import global_prompt_cache, extract_parent_scope
        origin, route = extract_parent_scope(current_url)
        cached_macro = global_prompt_cache.get_subtask_macro(origin, route, subtask)
        if cached_macro and isinstance(cached_macro, dict):
            cached_act = cached_macro.get("action")
            cached_params = cached_macro.get("params", {})
            if cached_act and cached_act not in excluded_set and self.verify_macro_affordance(cached_macro, candidates):
                self.telemetry["cache_reads"] += 1
                self.telemetry["latency_saved_ms"] += 250.0
                return cached_act, 0.99, cached_params, f"Subtask Macro Hit ({origin}{route}) [<0.2ms]"
            else:
                global_prompt_cache.invalidate_subtask_macro(origin, route, subtask)

        # Shortlist candidates to stay well within Laya's 192-token head budget:
        # Prioritize tools, navigation links, and relevant buttons matching subtask tokens/verbs
        target_token_match = re.search(r"\b([a-fA-F0-9]{6,36})\b", subtask)
        target_token = target_token_match.group(1).lower() if target_token_match else ""
        if not target_token and extracted_slots:
            target_token = str(extracted_slots.get("orderId") or extracted_slots.get("order_id") or extracted_slots.get("id") or "").lower()

        sub_words = set(re.findall(r"\w+", subtask.lower()))

        # Separate candidates into semantic buckets
        tool_cands: Dict[str, str] = {}
        overlay_cands: Dict[str, str] = {}
        dropdown_cands: Dict[str, str] = {}
        pagination_cands: Dict[str, str] = {}
        matched_nav_cands: Dict[str, str] = {}
        other_nav_cands: Dict[str, str] = {}
        matched_action_cands: Dict[str, str] = {}
        other_action_cands: Dict[str, str] = {}
        prim_cands: Dict[str, str] = {}

        for k, cand in candidates.items():
            if k in excluded_set:
                continue
            ctype = cand.get("type")
            region = cand.get("region", "")
            lbl = cand.get("label", k)
            full_txt = f"{k} {lbl}".lower()

            if ctype == "overlay" or region == "overlay":
                overlay_cands[k] = lbl
            elif ctype == "dropdown":
                dropdown_cands[k] = lbl
            elif ctype == "pagination":
                pagination_cands[k] = lbl
            elif ctype == "tool":
                tool_cands[k] = lbl
            elif ctype == "primitive":
                prim_cands[k] = lbl
            elif ctype == "navigate":
                if any(w in full_txt for w in sub_words if len(w) > 3):
                    matched_nav_cands[k] = lbl
                else:
                    other_nav_cands[k] = lbl
            else:
                if target_token and target_token in full_txt:
                    matched_action_cands[k] = lbl
                elif any(w in full_txt for w in sub_words if len(w) > 3):
                    matched_action_cands[k] = lbl
                else:
                    other_action_cands[k] = lbl

        # Assemble up to 10-12 focused choices for Laya
        criteria: Dict[str, str] = {}

        # 1. Active modal / dialog / dropdown overlay (Highest priority: user must interact with active overlay first)
        for k, v in list(overlay_cands.items())[:5]:
            if len(criteria) < 8:
                criteria[k] = v

        # 1.5 Prioritize live affordances that match previously learned route macros
        cached_route_macros = global_prompt_cache.get_subtask_macros_for_route(origin, route)
        for m in cached_route_macros:
            m_act = m.get("action")
            if m_act and m_act in candidates and m_act not in excluded_set and m_act not in criteria:
                if len(criteria) < 8:
                    cand_info = candidates.get(m_act, {})
                    criteria[m_act] = cand_info.get("label") or m_act

        # 2. Operational tools matching subtask verbs
        matched_tools = {k: v for k, v in tool_cands.items() if tool_matches_subtask(k, subtask)}
        other_tools = {k: v for k, v in tool_cands.items() if k not in matched_tools}

        sorted_matched_tools = sorted(
            matched_tools.items(),
            key=lambda item: sum(1 for w in sub_words if len(w) > 3 and (w in item[0].lower() or w in item[1].lower())),
            reverse=True,
        )
        for k, v in sorted_matched_tools:
            if k != "tool_navigate_to_page" and len(criteria) < 8:
                criteria[k] = v

        # 3. Dropdowns & comboboxes (if subtask mentions dropdown/status/filter/combobox/select)
        is_dropdown_subtask = any(w in subtask.lower() for w in ("dropdown", "combobox", "status", "filter", "select", "option"))
        if is_dropdown_subtask:
            for k, v in list(dropdown_cands.items())[:3]:
                if len(criteria) < 8:
                    criteria[k] = v

        # 4. Pagination controls (if subtask mentions page/pagination/next page)
        is_pagination_subtask = any(w in subtask.lower() for w in ("page", "pagination", "next page", "each page", "all pages"))
        if is_pagination_subtask:
            for k, v in list(pagination_cands.items())[:3]:
                if len(criteria) < 8:
                    criteria[k] = v

        # 5. Matched navigation links (e.g. Products tab when searching for products)
        for k, v in list(matched_nav_cands.items())[:3]:
            if len(criteria) < 8:
                criteria[k] = v

        # 6. Matched action buttons / inputs (up to 4, inputs prioritized for search/entry subtasks)
        is_search_intent = any(w in subtask.lower() for w in ("search", "find", "query", "lookup", "enter", "type", "fill"))
        sorted_action_cands = sorted(
            matched_action_cands.items(),
            key=lambda item: (1 if item[0].startswith("input_field_") and is_search_intent else 0),
            reverse=True,
        )
        for k, v in sorted_action_cands[:4]:
            if len(criteria) < 8:
                criteria[k] = v

        # 7. Explicit primitive match (screenshot, scroll, reload)
        for pk, pv in prim_cands.items():
            if any(w in subtask.lower() for w in ("screenshot", "snapshot", "capture")) and "screenshot" in pk:
                criteria[pk] = pv
            elif any(w in subtask.lower() for w in ("scroll down", "bottom", "below")) and "scroll_down" in pk:
                criteria[pk] = pv
            elif any(w in subtask.lower() for w in ("scroll up", "top", "above")) and "scroll_up" in pk:
                criteria[pk] = pv
            elif any(w in subtask.lower() for w in ("reload", "refresh")) and "reload" in pk:
                criteria[pk] = pv
            elif any(w in subtask.lower() for w in ("go back", "back")) and "go_back" in pk:
                criteria[pk] = pv

        # 8. If looking for a specific target token/verb and no matched button exists, expose scroll down
        if not matched_action_cands and not matched_nav_cands and "browser_scroll_down" in prim_cands:
            criteria["browser_scroll_down"] = prim_cands["browser_scroll_down"]

        # 9. Other operational tools if space permits
        for k, v in list(other_tools.items()):
            if k != "tool_navigate_to_page" and len(criteria) < 8:
                criteria[k] = v
        if "tool_navigate_to_page" in tool_cands and len(criteria) < 8:
            criteria["tool_navigate_to_page"] = tool_cands["tool_navigate_to_page"]

        # 10. Other dropdowns if space permits
        for k, v in list(dropdown_cands.items()):
            if len(criteria) < 8 and k not in criteria:
                criteria[k] = v

        # 11. Other navigation links
        for k, v in list(other_nav_cands.items())[:max(0, 9 - len(criteria))]:
            criteria[k] = v

        # 12. Other buttons if space permits
        remaining_slots = max(0, 10 - len(criteria))
        for k, v in list(other_action_cands.items())[:remaining_slots]:
            criteria[k] = v

        # 8. Mandatory negative option (Prevents forced softmax probability spills onto irrelevant buttons)
        criteria["other"] = "None of the other options fits the active step or the required element is not on this screen"

        # Format clean, unpolluted state: focuses on active subtask and screen context
        visible_summary = ", ".join([f"'{e['text']}'" for e in screen_elements[:8] if e.get("text")]) or "None"
        state = (
            f"Active Objective: {subtask}\n"
            f"Current URL: {current_url}\n"
            f"Visible Screen Elements: {visible_summary}"
        )

        chosen = "other"
        confidence = 0.0

        if self.agent is not None:
            try:
                questions = {
                    "action": {
                        "type": "choice",
                        "instructions": f"What action should be taken to fulfill the active objective: '{subtask}'?",
                        "criteria": criteria,
                    }
                }
                res = self.agent.predict(state, questions)
                answer = res.get("answers", {}).get("action", {})
                chosen = answer.get("choice", chosen)
                confidence = float(answer.get("answer_confidence") or answer.get("confidence", 0.0))
            except Exception:
                confidence = 0.0

        # Safety Guardrails: NEVER allow premature session termination
        is_logout_action = chosen == "tool_logout_user" or "logout" in chosen.lower() or "signout" in chosen.lower()
        subtask_has_logout = any(w in subtask.lower() for w in ("logout", "signout", "log out", "sign out", "exit session"))
        violates_guardrail = (is_logout_action and not subtask_has_logout)

        # Build actuation parameters from chosen candidate
        cand_meta = candidates.get(chosen, {})
        params: Dict[str, Any] = {}
        if cand_meta.get("coords"):
            params["_coords"] = cand_meta["coords"]
        if cand_meta.get("path"):
            params["path"] = cand_meta["path"]
        if cand_meta.get("type") == "input":
            params["_input"] = True
            params["_field"] = cand_meta.get("field", "")
            params["_input_type"] = cand_meta.get("input_type", "text")

        # Fallback handling: if confidence is low, negative option chosen, or guardrail violated:
        if chosen == "other" or confidence < self.confidence_threshold or violates_guardrail or chosen not in candidates:
            filtered_tools = [t for t in available_tools if t.get("name") not in excluded_set and f"tool_{t.get('name')}" not in excluded_set]
            fallback = self._query_screen_locator(
                subtask=subtask,
                current_url=current_url,
                available_tools=filtered_tools,
                screen_elements=screen_elements,
                last_result=last_result,
                extracted_slots=extracted_slots,
                scroll_state=scroll_state,
                can_go_back=can_go_back,
            )
            if fallback:
                fb_action, fb_conf, fb_params, fb_info = fallback
                if fb_action == "action_blocked":
                    return fb_action, fb_conf, fb_params, fb_info
                if fb_action not in excluded_set:
                    fb_is_logout = fb_action == "tool_logout_user" or "logout" in fb_action.lower()
                    if not (fb_is_logout and not subtask_has_logout):
                        # Ground candidate: if model hallucinated an action not in live candidates, map to highest overlap candidate
                        if fb_action not in candidates and not fb_action.startswith("browser_"):
                            fb_tokens = set(re.split(r"[_\W]+", fb_action.lower())) - {"click", "tool", "btn", "link"}
                            best_cand = None
                            best_overlap = 0
                            for ck in candidates.keys():
                                c_tokens = set(re.split(r"[_\W]+", ck.lower())) - {"click", "tool", "btn", "link"}
                                match_count = len(fb_tokens & c_tokens)
                                if match_count > best_overlap:
                                    best_overlap = match_count
                                    best_cand = ck
                            if best_cand:
                                fb_action = best_cand

                        cand_meta = candidates.get(fb_action, {})
                        if cand_meta:
                            if "_coords" not in fb_params and cand_meta.get("coords"):
                                fb_params["_coords"] = cand_meta["coords"]
                            if "path" not in fb_params and cand_meta.get("path"):
                                fb_params["path"] = cand_meta["path"]
                        return fb_action, fb_conf, fb_params, fb_info

            # If fallback cannot resolve an action, return safe uncommitted action
            return "other", 0.0, {}, "Uncertain action; awaiting navigation or visual update"

        engine_info = f"Laya MLX Fast Policy ({confidence * 100:.1f}% conf)"
        return chosen, confidence, params, engine_info

    def cache_subtask_macro(
        self,
        origin: str,
        route: str,
        subtask: str,
        action: str,
        params: Dict[str, Any],
        confidence: float = 0.99,
    ):
        """Caches a verified subtask macro ONLY when the subtask has been verified to advance."""
        if action not in ("other", "finish", "browser_new_tab", "browser_switch_tab"):
            global_prompt_cache.set_subtask_macro(
                origin, route, subtask,
                {"action": action, "params": params, "confidence": confidence}
            )

    def _query_screen_locator(
        self,
        subtask: str,
        current_url: str,
        available_tools: List[Dict[str, Any]],
        screen_elements: List[Dict[str, Any]],
        last_result: Any = None,
        extracted_slots: Optional[Dict[str, Any]] = None,
        scroll_state: Optional[Dict[str, Any]] = None,
        can_go_back: bool = False,
    ) -> Optional[Tuple[str, float, Dict[str, Any], str]]:
        """Uses local LLM to inspect visual elements on screen and resolve the exact tool or coordinate to click."""
        sub_low = subtask.lower().strip()

        # Universal Form Input Resolution (Universal text entry into input/textarea)
        is_selection_subtask = any(w in sub_low for w in ("select", "choose", "pick", "click", "open", "view"))
        is_input_subtask = (
            any(w in sub_low for w in ("enter", "input", "type", "fill", "write"))
            or "into search" in sub_low
            or (re.search(r"\bsearch\s+(?:for\s+)?['\"]", sub_low) is not None)
            or (sub_low.startswith("search ") and "from search" not in sub_low and "in search" not in sub_low)
        ) and not (is_selection_subtask and any(ph in sub_low for ph in ("from search", "in search", "from dropdown", "from list", "from results")))
        if is_input_subtask:
            target_field_name = ""
            m_into = re.search(r"\b(?:into|in|for)\s+([a-zA-Z0-9_\-]+)\s*(?:field|input|box|bar|textarea)?\b", sub_low)
            if m_into:
                target_field_name = m_into.group(1).lower().strip()

            matching_input = None
            for e in screen_elements:
                if e.get("tag") not in ("input", "textarea"):
                    continue
                e_name = (e.get("name") or "").lower()
                e_id = (e.get("id") or "").lower()
                e_ph = (e.get("placeholder") or "").lower()
                e_lbl = (e.get("aria-label") or e.get("text") or "").lower()
                combined = f"{e_name} {e_id} {e_ph} {e_lbl}"
                if target_field_name and (target_field_name in combined or combined in target_field_name):
                    matching_input = e
                    break
                if extracted_slots:
                    for sk in extracted_slots:
                        if len(sk) >= 3 and sk.lower() in combined and sk.lower() in sub_low:
                            matching_input = e
                            target_field_name = sk
                            break
                if matching_input:
                    break

            if not matching_input:
                is_search = any(w in sub_low for w in ("search", "query", "find", "lookup"))
                matching_input = next(
                    (
                        e for e in screen_elements
                        if e.get("tag") in ("input", "textarea")
                        and e.get("type") not in ("submit", "button", "checkbox", "radio", "hidden")
                        and (not is_search or any(sw in ((e.get("name") or "") + " " + (e.get("placeholder") or "")).lower() for sw in ("search", "query", "q")))
                    ),
                    None
                ) or next(
                    (
                        e for e in screen_elements
                        if e.get("tag") in ("input", "textarea")
                        and e.get("type") not in ("submit", "button", "checkbox", "radio", "hidden")
                    ),
                    None
                )

            if matching_input:
                f_name = matching_input.get("name") or matching_input.get("id") or target_field_name or "input"
                clean_f = re.sub(r"[^a-zA-Z0-9_]", "_", f_name).strip("_").lower()
                action_name = f"input_{clean_f[:25]}"
                input_val = ""
                if extracted_slots and target_field_name in extracted_slots:
                    input_val = str(extracted_slots[target_field_name])
                elif extracted_slots:
                    input_val = str(extracted_slots.get("search") or extracted_slots.get("query") or extracted_slots.get("username") or extracted_slots.get("password") or "")
                if not input_val:
                    m_val = re.search(r"['\"](.*?)['\"]", subtask)
                    if m_val:
                        input_val = m_val.group(1).strip()
                press_enter = bool(
                    any(sw in sub_low for sw in ("submit", "press enter", "search"))
                    or matching_input.get("type") == "search"
                    or any(sw in (f_name + " " + (matching_input.get("placeholder") or "")).lower() for sw in ("search", "query", "q", "keyword"))
                )
                return action_name, 0.99, {"field": f_name, "value": input_val, "_field": f_name, "_input": True, "_press_enter": press_enter, "_coords": (matching_input.get("x"), matching_input.get("y"))}, f"Universal Affordance Input '{f_name}'"

        # Universal affordance match based on subtask token overlap (zero hardcoded domain verbs)
        sub_tokens = [w for w in re.findall(r"\w+", sub_low) if len(w) > 2 and w not in ("click", "press", "button", "link", "page", "the", "from", "with")]
        is_nav_or_control = any(w in sub_low for w in ("switch", "tab", "reload", "back", "scroll", "close", "finish"))
        if not is_nav_or_control and extracted_slots:
            for sval in extracted_slots.values():
                if isinstance(sval, (str, int)):
                    sub_tokens.extend([w for w in re.findall(r"\w+", str(sval).lower()) if len(w) > 2])
        sub_token_set = set(sub_tokens)

        if sub_token_set:
            best_el = None
            best_score = 0
            for e in screen_elements:
                if e.get("tag") not in ("button", "a") and e.get("role") not in ("button", "link", "menuitem"):
                    continue
                e_txt = (e.get("text") or "").lower()
                e_tokens = set(re.findall(r"\w+", e_txt))
                overlap = len(sub_token_set & e_tokens)
                if overlap > best_score:
                    best_score = overlap
                    best_el = e

            if best_el and best_score >= max(1, min(2, len(sub_token_set))):
                txt = best_el.get("text", "")
                clean_txt = re.sub(r"[^a-zA-Z0-9_]", "_", txt).strip("_").lower()
                action_name = f"click_{clean_txt[:30]}"
                return action_name, 0.99, {"x": best_el.get("x"), "y": best_el.get("y"), "target": txt, "path": best_el.get("href", "")}, f"Universal Affordance Match: {txt[:30]}"

        screen_summary = [
            {"text": (e.get("text") or "")[:40], "tag": e.get("tag"), "x": e.get("x"), "y": e.get("y"), "href": e.get("href", "")}
            for e in screen_elements[:35]
            if (e.get("text") or e.get("tag") in ("button", "input", "a"))
        ]
        tool_info = [
            {"name": t.get("name"), "description": t.get("description", "")[:100]}
            for t in available_tools
        ]
        tool_names = {t.get("name") for t in available_tools}

        can_scroll_down = bool(scroll_state.get("can_scroll_down", True)) if scroll_state is not None else True
        can_scroll_up = bool(scroll_state.get("can_scroll_up", False)) if scroll_state is not None else False

        prompt = (
            f"You are an autonomous browser vision and routing engine.\n"
            f"Objective: {subtask}\n"
            f"Current URL: {current_url}\n"
            f"WebMCP Tools available on page: {json.dumps(tool_info)}\n"
            f"Visible Elements on screen with coordinates (x,y): {json.dumps(screen_summary)}\n"
            f"Extracted User Inputs: {json.dumps(extracted_slots or {})}\n"
            f"Browser Affordances: can_scroll_down={can_scroll_down}, can_scroll_up={can_scroll_up}, can_go_back={can_go_back}\n\n"
            "Identify the best action to achieve or progress toward this objective:\n"
            "- ALWAYS prefer an available WebMCP tool (action='tool_<tool_name>') over clicking screen buttons if a tool matches the goal.\n"
            "- If the objective asks to select an option from a dropdown, combobox, or filter (e.g. 'All Status', 'Category', 'Sort'), return action='click_<dropdown_name>' or action='click_<option_name>' to open or select from the dropdown. Do NOT call text search tools for dropdown filtering.\n"
            "- If the objective asks to paginate, check each page, or go to all pages, return action='click_next' if a Next button is available and enabled.\n"
            "- If a WebMCP tool achieves it directly on this page, return action='tool_<tool_name>' and params={...}.\n"
            "- If the current page does not have the target feature, identify which navigation link/tab leads toward that section and return action='click_<text>' with coords={'x': x, 'y': y} or action='tool_<nav_tool>'.\n"
            "- If clicking a visible screen button or input achieves it, return action='click_<text>' with coords={'x': x, 'y': y}.\n"
            "- If the objective asks to open a new tab or switch tabs, return action='browser_new_tab' or action='browser_switch_tab'.\n"
            "- If the target element, row, button, or content is NOT visible in the screen elements, and the page can scroll down (can_scroll_down=True), return action='browser_scroll_down' so the browser can reveal elements below the fold.\n"
            "- If the target element might be above the current scroll position (can_scroll_up=True), return action='browser_scroll_up'.\n"
            "- If the objective asks to take a screenshot, capture screen, or snapshot, return action='browser_take_screenshot'.\n"
            "- NEVER return logout_user or click logout unless the objective explicitly asks to logout.\n"
            "- NEVER return a mutating or form submission action (e.g. submit, post, delete, create) when the objective is purely navigation or inspection. Use navigation links or read tools instead.\n"
            "- If the visible screen elements indicate that the requested item or action cannot be completed (e.g. the item is unreleased, out of stock, shows 'Notify Me', 'Currently Unavailable', 'Sold Out', 'Coming Soon', or requires login/manual verification), return action='action_blocked' and explain why in 'reasoning'.\n"
            "- ONLY return action='other' if neither the current page, scrolling, nor visible navigation leads toward the objective.\n\n"
            "Respond in JSON format only:\n"
            "{\n"
            '  "action": "tool_<name>", "click_<name>", "browser_scroll_down", "browser_scroll_up", "browser_take_screenshot", "browser_reload", "browser_go_back", "browser_new_tab", "browser_switch_tab", "action_blocked", or "other",\n'
            '  "params": { ... },\n'
            '  "coords": {"x": 0, "y": 0},\n'
            '  "reasoning": "<short rationale>"\n'
            "}"
        )

        cache_payload = {
            "subtask": subtask,
            "url": current_url,
            "tools": tool_info,
            "elements": screen_summary,
            "slots": extracted_slots or {},
        }
        from laya_browser_agent.cache import global_prompt_cache
        cached_parsed = global_prompt_cache.get("screen_locator", self.ollama_model, cache_payload)
        is_cache_hit = False
        parsed = None

        if cached_parsed and isinstance(cached_parsed, dict):
            parsed = cached_parsed
            is_cache_hit = True
            self.telemetry["cache_reads"] += 1
            self.telemetry["latency_saved_ms"] += 1500.0
        else:
            client = get_llm_client()
            messages = [{"role": "user", "content": prompt}]
            parsed, in_tok, out_tok, elapsed_ms = client.chat_json(messages, timeout=25.0)
            if isinstance(parsed, dict):
                self.telemetry["input_tokens"] += in_tok
                self.telemetry["output_tokens"] += out_tok
                self.telemetry["cache_writes"] += 1
                global_prompt_cache.set("screen_locator", self.ollama_model, cache_payload, parsed, latency_saved_ms=elapsed_ms)

        if parsed and isinstance(parsed, dict):
            raw_action = parsed.get("action", "").strip()
            if raw_action == "action_blocked" or parsed.get("status") == "blocked":
                reasoning = parsed.get("reasoning") or parsed.get("reason") or "Target action cannot be completed on this page"
                return "action_blocked", 0.95, {"reason": reasoning}, f"Screen Arbitrator Blocked ({self.ollama_model}): {reasoning}"
            if not raw_action or raw_action.lower() in ("other", "none", "unknown"):
                return None

            act_low = raw_action.lower().replace("-", "_")
            if "scroll" in act_low and "down" in act_low:
                action = "browser_scroll_down"
            elif "scroll" in act_low and "up" in act_low:
                action = "browser_scroll_up"
            elif any(w in act_low for w in ("screenshot", "snapshot", "capture")):
                action = "browser_take_screenshot"
            elif any(w in act_low for w in ("reload", "refresh")):
                action = "browser_reload"
            elif any(w in act_low for w in ("go_back", "goback", "back")):
                action = "browser_go_back"
            else:
                raw_name = raw_action.replace("tool_", "").strip()
                clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_action.replace("click_", "").replace("tool_", "").strip()).strip("_").lower()
                if raw_name in tool_names:
                    action = f"tool_{raw_name}"
                elif raw_action.startswith("click_") or clean_name:
                    action = f"click_{clean_name}"
                else:
                    action = raw_action

            params = parsed.get("params") or {}
            coords = parsed.get("coords") or {}
            if coords and isinstance(coords, dict):
                cx = coords.get("x")
                cy = coords.get("y")
                if cx is not None and cy is not None:
                    try:
                        params["_coords"] = (float(cx), float(cy))
                    except (ValueError, TypeError):
                        pass
            reasoning = parsed.get("reasoning", "")
            if action:
                engine_msg = f"Local Prompt Cache Hit ({self.ollama_model}, <0.2ms)" if is_cache_hit else f"LLM Router Fallback ({self.ollama_model}): {reasoning}"
                return action, 0.90, params, engine_msg
