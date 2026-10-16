"""Laya MLX Decision Engine with Screen Perception, Dynamic Candidate Extraction, and Zero-Shot Policy."""

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx
from laya_mlx import Agent


def normalize_route_key(url: str) -> str:
    """Normalizes URL to origin + path (e.g. 'localhost:3000/orders')."""
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    if "://" in clean:
        clean = clean.split("://", 1)[-1]
    return clean or "root"


def extract_page_candidates(
    available_tools: List[Dict[str, Any]],
    screen_elements: List[Dict[str, Any]],
    current_url: str,
    excluded_actions: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Extracts all interactive affordances on the current page into a candidate map.
    Handles web applications WITH WebMCP (tools) and WITHOUT WebMCP (standard DOM inputs/buttons/links).
    Each candidate holds its semantic description (for Laya MLX) AND actuation metadata (coords, field, path).
    """
    candidates: Dict[str, Dict[str, Any]] = {}
    excluded = excluded_actions or set()

    has_auth_tool = any("login" in t.get("name", "").lower() or "auth" in t.get("name", "").lower() for t in available_tools)

    # 1. Available WebMCP Tools
    for tool in available_tools:
        name = tool.get("name")
        if not name:
            continue
        key = f"tool_{name}"
        if key in excluded or name in excluded:
            continue
        desc = (tool.get("description") or f"Call {name}").strip()
        if len(desc) > 90:
            desc = desc[:87] + "..."
        candidates[key] = {
            "type": "tool",
            "name": name,
            "label": desc,
            "tool_def": tool,
        }

    # 2. Form Inputs & Textareas (Only if no WebMCP tools available on this page, or non-WebMCP app)
    if not available_tools:
        for el in screen_elements:
            tag = el.get("tag", "")
            el_type = el.get("type", "")
            if tag in ("input", "textarea") and el_type not in ("submit", "button", "checkbox", "radio", "hidden"):
                field_name = el.get("name") or el.get("id") or el.get("placeholder") or el.get("text") or "input"
                clean_field = re.sub(r"[^a-zA-Z0-9_]", "_", field_name).strip("_").lower()
                key = f"input_{clean_field[:25]}"
                if key in candidates or key in excluded:
                    continue
                candidates[key] = {
                    "type": "input",
                    "field": field_name,
                    "input_type": el_type or "text",
                    "label": f"Enter {field_name} (type={el_type or 'text'})",
                    "coords": (el["x"], el["y"]) if el.get("x") is not None else None,
                }

    # 3. Action Buttons & Navigation Links
    for el in screen_elements:
        tag = el.get("tag", "")
        el_type = el.get("type", "")
        if tag in ("input", "textarea") and el_type not in ("submit", "button"):
            continue

        text = el.get("text", "").strip()
        if not text or len(text) < 2:
            continue

        # If page already has a WebMCP login tool, skip generic raw submit button on login form
        if has_auth_tool and any(w in text.lower() for w in ("sign in", "login", "log in", "submit")):
            continue

        clean_text = re.sub(r"[^a-zA-Z0-9_]", "_", text).strip("_").lower()
        key = f"click_{clean_text[:30]}"
        if key in candidates or key in excluded:
            continue

        href = el.get("href", "")
        # Filter out self-links to prevent navigation loops
        if href and (current_url.rstrip("/").endswith(href.rstrip("/")) or href.rstrip("/") in current_url):
            continue

        is_nav = bool(href or tag == "a" or el.get("role") in ("link", "tab"))

        # Filter out logo/home links when on deeper page to avoid resetting session/looping
        if is_nav and href in ("/", "/dashboard", "#"):
            current_path = current_url.split("://")[-1].split("/", 1)[-1]
            if current_path and current_path not in ("", "dashboard"):
                continue

        if is_nav:
            label = f"Navigate to '{text}' page"
            cand_type = "navigate"
        else:
            label = f"{text.rstrip('.')}"
            cand_type = "click"

        candidates[key] = {
            "type": cand_type,
            "text": text,
            "label": label,
            "coords": (el["x"], el["y"]) if el.get("x") is not None else None,
            "path": href,
        }

    return candidates


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
        confidence_threshold: float = 0.85,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "llama3.1:8b",
    ):
        self.model_id = model_id
        self.confidence_threshold = confidence_threshold
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        # ponytail: in-memory route affordance cache for current session report; no disk files needed
        self.site_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}

        try:
            self.agent = Agent(model_id, device=device)
        except Exception:
            # ponytail: fallback to CPU if Metal GPU is unavailable
            self.agent = Agent(model_id, device="cpu")

    def invalidate_cache(self, url: str):
        """Evicts cached candidates for the specified URL route."""
        route_key = normalize_route_key(url)
        self.site_cache.pop(route_key, None)

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
        )
        self.site_cache[route_key] = candidates

        if not candidates:
            return "finish", 1.0, {}, "No actionable elements on screen"

        # Shortlist candidates to stay well within Laya's 192-token head budget:
        # Prioritize tools, navigation links, and relevant buttons matching subtask tokens/verbs
        target_token_match = re.search(r"\b([a-fA-F0-9]{6,36})\b", subtask)
        target_token = target_token_match.group(1).lower() if target_token_match else ""
        if not target_token and extracted_slots:
            target_token = str(extracted_slots.get("orderId") or extracted_slots.get("order_id") or extracted_slots.get("id") or "").lower()

        sub_words = set(re.findall(r"\w+", subtask.lower()))

        # Separate candidates into buckets
        tool_cands: Dict[str, str] = {}
        matched_nav_cands: Dict[str, str] = {}
        other_nav_cands: Dict[str, str] = {}
        matched_btn_cands: Dict[str, str] = {}
        other_btn_cands: Dict[str, str] = {}

        for k, cand in candidates.items():
            if k in excluded_set:
                continue
            ctype = cand.get("type")
            lbl = cand.get("label", k)
            full_txt = f"{k} {lbl}".lower()

            if ctype == "tool":
                tool_cands[k] = lbl
            elif ctype == "navigate":
                if any(w in full_txt for w in sub_words if len(w) > 3):
                    matched_nav_cands[k] = lbl
                else:
                    other_nav_cands[k] = lbl
            else:
                if target_token and target_token in full_txt:
                    matched_btn_cands[k] = lbl
                elif any(w in full_txt for w in sub_words if len(w) > 3):
                    matched_btn_cands[k] = lbl
                else:
                    other_btn_cands[k] = lbl

        # Assemble up to 10-12 focused choices for Laya
        criteria: Dict[str, str] = {}
        # 1. Matched navigation links (e.g. Products tab when searching for products)
        for k, v in list(matched_nav_cands.items())[:3]:
            criteria[k] = v

        # 2. Matched buttons (up to 4)
        for k, v in list(matched_btn_cands.items())[:4]:
            criteria[k] = v

        # 3. Operational tools (prioritize action tools over generic navigate)
        for k, v in list(tool_cands.items()):
            if k != "tool_navigate_to_page" and len(criteria) < 8:
                criteria[k] = v
        if "tool_navigate_to_page" in tool_cands and len(criteria) < 8:
            criteria["tool_navigate_to_page"] = tool_cands["tool_navigate_to_page"]

        # 4. Other navigation links
        for k, v in list(other_nav_cands.items())[:max(0, 9 - len(criteria))]:
            criteria[k] = v

        # 5. Other buttons if space permits
        remaining_slots = max(0, 10 - len(criteria))
        for k, v in list(other_btn_cands.items())[:remaining_slots]:
            criteria[k] = v

        # 5. Mandatory negative option (Prevents forced softmax probability spills onto irrelevant buttons)
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
            fallback = self._query_screen_locator(
                subtask=subtask,
                current_url=current_url,
                available_tools=available_tools,
                screen_elements=screen_elements,
                last_result=last_result,
                extracted_slots=extracted_slots,
            )
            if fallback:
                fb_action, fb_conf, fb_params, fb_info = fallback
                fb_is_logout = fb_action == "tool_logout_user" or "logout" in fb_action.lower()
                if not (fb_is_logout and not subtask_has_logout):
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

    def _query_screen_locator(
        self,
        subtask: str,
        current_url: str,
        available_tools: List[Dict[str, Any]],
        screen_elements: List[Dict[str, Any]],
        last_result: Any = None,
        extracted_slots: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[str, float, Dict[str, Any], str]]:
        """Uses local LLM to inspect visual elements on screen and resolve the exact tool or coordinate to click."""
        screen_summary = [
            {"text": (e.get("text") or "")[:40], "tag": e.get("tag"), "x": e.get("x"), "y": e.get("y"), "href": e.get("href", "")}
            for e in screen_elements[:35]
            if e.get("text") or e.get("tag") in ("button", "input", "a")
        ]
        tool_info = [
            {"name": t.get("name"), "description": t.get("description", "")[:100]}
            for t in available_tools
        ]
        tool_names = {t.get("name") for t in available_tools}

        prompt = (
            f"You are an autonomous browser vision and routing engine.\n"
            f"Objective: {subtask}\n"
            f"Current URL: {current_url}\n"
            f"WebMCP Tools available on page: {json.dumps(tool_info)}\n"
            f"Visible Elements on screen with coordinates (x,y): {json.dumps(screen_summary)}\n"
            f"Extracted User Inputs: {json.dumps(extracted_slots or {})}\n\n"
            "Identify the best action to complete this objective:\n"
            "- If a WebMCP tool achieves it directly on this page, return action='tool_<tool_name>' and params={...}.\n"
            "- If the current page does not have the target feature, identify which navigation link/tab leads to that section and return action='click_<text>' with coords={'x': x, 'y': y}.\n"
            "- If clicking a visible screen button or input achieves it, return action='click_<text>' with coords={'x': x, 'y': y}.\n"
            "- NEVER return logout_user or click logout unless the objective explicitly asks to logout.\n\n"
            "Respond in JSON format only:\n"
            "{\n"
            '  "action": "tool_<name>" or "click_<name>",\n'
            '  "params": { ... },\n'
            '  "coords": {"x": 0, "y": 0},\n'
            '  "reasoning": "<short rationale>"\n'
            "}"
        )

        try:
            with httpx.Client(timeout=25.0) as client:
                res = client.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": self.ollama_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "format": "json",
                        "stream": False,
                    },
                )
                if res.status_code == 200:
                    data = res.json()
                    content = data.get("message", {}).get("content", "{}")
                    parsed = json.loads(content)
                    action = parsed.get("action", "")

                    raw_name = action.replace("tool_", "").strip()
                    clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", action.replace("click_", "").strip()).strip("_").lower()
                    if raw_name in tool_names:
                        action = f"tool_{raw_name}"
                    elif action.startswith("click_") or clean_name:
                        action = f"click_{clean_name}"

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
                        return action, 0.90, params, f"LLM Router Fallback ({self.ollama_model}): {reasoning}"
        except Exception:
            pass

        return None
