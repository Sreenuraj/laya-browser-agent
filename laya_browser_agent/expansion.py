"""Progressive discovery plan expansion and live screen remediation."""

import re
from typing import Any, Dict, List, Optional


def expand_plan_from_screen(
    extractor: Any,
    goal: str,
    deferred_goal: str,
    completed_subtasks: List[str],
    current_url: str,
    screen_elements: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    extracted_slots: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Dynamically synthesizes grounded subtasks to fulfill the deferred user intent
    strictly using real affordances available on the live screen.
    """
    slots = extracted_slots or {}

    # 1. First attempt fast local LLM grounding if Ollama is available
    try:
        grounded = _expand_via_ollama(
            extractor=extractor,
            goal=goal,
            deferred_goal=deferred_goal,
            completed_subtasks=completed_subtasks,
            current_url=current_url,
            screen_elements=screen_elements,
            tools=tools,
        )
        if grounded:
            return grounded
    except Exception:
        pass

    # 2. Deterministic heuristic affordance grounding
    return _expand_via_heuristics(
        goal=goal,
        deferred_goal=deferred_goal,
        completed_subtasks=completed_subtasks,
        current_url=current_url,
        screen_elements=screen_elements,
        tools=tools,
        slots=slots,
    )


def _expand_via_ollama(
    extractor: Any,
    goal: str,
    deferred_goal: str,
    completed_subtasks: List[str],
    current_url: str,
    screen_elements: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Optional[List[str]]:
    affordance_items = []
    for el in screen_elements:
        tag = el.get("tag", "")
        role = el.get("role", "")
        text = (el.get("text") or el.get("aria_label") or el.get("name") or "").strip()
        if text and len(text) < 50 and (tag in ("button", "a", "select", "input") or role in ("button", "link", "combobox", "option", "tab", "menuitem")):
            affordance_items.append(f"[{role or tag}] '{text}'")
    for t in tools:
        affordance_items.append(f"[tool] '{t.get('name')}'")

    affordance_str = "\n".join(affordance_items[:40]) if affordance_items else "No specialized interactive controls."
    prompt = (
        "You are an autonomous web agent task planner expanding a plan from live screen affordances.\n"
        f"User goal: {goal}\n"
        f"Completed steps: {completed_subtasks}\n"
        f"Remaining user intent to fulfill: {deferred_goal}\n"
        f"Active page URL: {current_url}\n\n"
        f"Visible Live Screen Affordances:\n{affordance_str}\n\n"
        "Rules for Progressive Discovery:\n"
        "1. Synthesize the next 1-3 grounded sequential subtasks strictly using elements present in the visible affordances to fulfill the remaining intent.\n"
        "2. If pagination or multiple pages was requested, ONLY include a pagination subtask if a Next or pagination button/link actually exists in the visible affordances. If none exists, omit pagination.\n"
        "3. If a requested filter/option exists on screen, specify the exact element text.\n"
        "4. If logout was requested at the end, include 'Logout'.\n"
        "Respond in valid JSON only:\n"
        '{"grounded_subtasks": ["step 1", "step 2", ...]}'
    )
    cache_payload = {
        "goal": goal,
        "deferred_goal": deferred_goal,
        "completed": completed_subtasks,
        "url": current_url,
        "affordances": affordance_items[:40],
    }
    from laya_browser_agent.cache import global_prompt_cache
    cached = global_prompt_cache.get("progressive_expansion", extractor.ollama_model, cache_payload)
    if cached and isinstance(cached, dict) and "grounded_subtasks" in cached:
        return [str(s).strip() for s in cached["grounded_subtasks"] if str(s).strip()]

    from .llm import get_llm_client
    client = get_llm_client()
    messages = [
        {"role": "system", "content": "You are a web automation planner grounding subtasks from live screen affordances. Output valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    parsed, in_tokens, out_tokens, elapsed_ms = client.chat_json(messages, timeout=10.0)
    if isinstance(parsed, dict) and "grounded_subtasks" in parsed:
        res = [str(s).strip() for s in parsed["grounded_subtasks"] if str(s).strip()]
        if res:
            global_prompt_cache.set("progressive_expansion", extractor.ollama_model, cache_payload, parsed, latency_saved_ms=elapsed_ms)
            return res
    return None


def _expand_via_heuristics(
    goal: str,
    deferred_goal: str,
    completed_subtasks: List[str],
    current_url: str,
    screen_elements: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    slots: Dict[str, Any],
) -> List[str]:
    grounded_subtasks: List[str] = []
    low_def = deferred_goal.lower()

    # A. Dropdown / Filter Affordance Grounding
    target_status = slots.get("status") or slots.get("option")
    target_dropdown = slots.get("dropdown")
    if target_status and slots.get("option") and (
        (target_dropdown and target_status.lower() == target_dropdown.lower())
        or target_status.lower() in ("all status", "select status", "status", "select", "all", "any")
    ):
        target_status = slots.get("option")
    if not target_status:
        st_m = re.search(r"\b(?:status\s+(?:as|to|is)\s+|option\s*[:=]?\s*)['\"]([^'\"]+)['\"]", deferred_goal, re.IGNORECASE)
        if st_m:
            target_status = st_m.group(1).strip()
    if not target_dropdown:
        dd_m = re.search(r"\b(?:dropdown\s+(?:with|named)?|click\s+one\s+with)\s*['\"]([^'\"]+)['\"]", deferred_goal, re.IGNORECASE)
        if dd_m:
            target_dropdown = dd_m.group(1).strip()

    if target_status or target_dropdown or any(w in low_def for w in ("dropdown", "status", "filter")):
        found_dd = None
        found_option = None
        if target_dropdown:
            found_dd = next((
                e for e in screen_elements
                if target_dropdown.lower() in e.get("text", "").lower() or e.get("role") == "combobox"
            ), None)

        if target_status:
            found_option = next((
                e for e in screen_elements
                if target_status.lower() in e.get("text", "").lower() or target_status.lower() in (e.get("aria_label") or "").lower()
            ), None)

        if found_dd and target_status:
            dd_label = found_dd.get("text", target_dropdown or "status").strip()
            grounded_subtasks.append(f"Select '{target_status}' from '{dd_label}' dropdown")
        elif found_option:
            opt_label = found_option.get("text", target_status).strip()
            grounded_subtasks.append(f"Click '{opt_label}'")
        elif target_status and target_dropdown:
            grounded_subtasks.append(f"Select '{target_status}' from '{target_dropdown}' dropdown")

    # B. Screenshot Affordance Grounding
    wants_screenshot = any(w in low_def for w in ("screenshot", "snapshot", "capture"))

    # C. Pagination Affordance Grounding
    wants_pagination = any(w in low_def for w in ("pagination", "all pages", "each page", "every page", "different pages", "next page"))
    if wants_pagination:
        next_btn = next((
            el for el in screen_elements
            if el.get("tag") in ("button", "a") and (
                el.get("text", "").strip().lower() in ("next", "next >", ">", "next page")
                or "next" in (el.get("aria_label") or "").lower()
            )
        ), None)
        has_next = bool(next_btn and not next_btn.get("disabled"))

        if has_next:
            if wants_screenshot:
                grounded_subtasks.append("Go to all pages via pagination and take screenshot of each page")
            else:
                grounded_subtasks.append("Go to all pages via pagination")
        else:
            # Live validation: screen only has a single page! Do NOT inject a pagination loop!
            if wants_screenshot and not any("screenshot" in s.lower() for s in grounded_subtasks):
                grounded_subtasks.append("Take screenshot of the filtered list")
    elif wants_screenshot and not any("screenshot" in s.lower() for s in grounded_subtasks):
        grounded_subtasks.append("Take screenshot of the current page")

    # D. Data Inspection / Counting Affordance Grounding
    wants_inspect = any(w in low_def for w in ("check and count", "how many", "check if there are", "count "))
    if wants_inspect:
        count_match = re.search(r"\b(?:check\s+and\s+count|count|check\s+if\s+there\s+are\s+any|how\s+many)\s+([^;\.\n]+)", deferred_goal, re.IGNORECASE)
        entity = count_match.group(1).strip() if count_match else "items"
        grounded_subtasks.append(f"Check and count {entity}")

    # E. Trailing Logout Grounding
    wants_logout = any(w in low_def for w in ("logout", "log out", "sign out", "signout"))
    if wants_logout and not any("logout" in s.lower() for s in grounded_subtasks):
        grounded_subtasks.append("Logout")

    return grounded_subtasks


def remediate_plan_from_screen(
    extractor: Any,
    goal: str,
    failed_subtask: str,
    failure_reason: str,
    current_url: str,
    page_title: str,
    screen_elements: List[Dict[str, Any]],
    remaining_subtasks: List[str],
) -> Optional[List[str]]:
    """
    Adapts or alters the sequential plan when a subtask fails assertion or stagnates.
    Introspects current browser URL, title, and live visible affordances to decide whether
    to alter the task, substitute an affordance, or advance past an unfulfillable step.
    """
    # 1. Fast local LLM remediation if Ollama is available
    try:
        remediated = _remediate_via_ollama(
            extractor=extractor,
            goal=goal,
            failed_subtask=failed_subtask,
            failure_reason=failure_reason,
            current_url=current_url,
            page_title=page_title,
            screen_elements=screen_elements,
            remaining_subtasks=remaining_subtasks,
        )
        if remediated:
            return remediated
    except Exception:
        pass

    # 2. Universal deterministic heuristic remediation
    return _remediate_via_heuristics(
        goal=goal,
        failed_subtask=failed_subtask,
        failure_reason=failure_reason,
        current_url=current_url,
        page_title=page_title,
        screen_elements=screen_elements,
        remaining_subtasks=remaining_subtasks,
    )


def _remediate_via_ollama(
    extractor: Any,
    goal: str,
    failed_subtask: str,
    failure_reason: str,
    current_url: str,
    page_title: str,
    screen_elements: List[Dict[str, Any]],
    remaining_subtasks: List[str],
) -> Optional[List[str]]:
    affordance_items = []
    for el in screen_elements:
        tag = el.get("tag", "")
        role = el.get("role", "")
        text = (el.get("text") or el.get("aria_label") or el.get("placeholder") or el.get("name") or "").strip()
        if text and len(text) < 60 and (tag in ("button", "a", "select", "input", "textarea") or role in ("button", "link", "combobox", "option", "tab", "searchbox")):
            affordance_items.append(f"[{role or tag}] '{text}'")

    affordance_str = "\n".join(affordance_items[:35]) if affordance_items else "No specialized interactive controls."
    prompt = (
        "You are an autonomous web agent task planner remediating a step failure.\n"
        f"Overall Goal: {goal}\n"
        f"Failed Subtask: {failed_subtask}\n"
        f"Failure Reason: {failure_reason}\n"
        f"Current Browser URL: {current_url}\n"
        f"Page Title: {page_title}\n\n"
        f"Visible Screen Affordances:\n{affordance_str}\n\n"
        f"Upcoming Planned Subtasks: {remaining_subtasks}\n\n"
        "Task:\n"
        "Analyze why the subtask failed and the current browser state. Decide the next subtasks to achieve the overall goal.\n"
        "1. If the current page offers an alternative action or input to make progress (e.g. search bar, related link), provide revised subtask(s).\n"
        "2. If this subtask cannot be completed here, but upcoming subtasks address other parts of the goal (e.g. checking another website), return the remaining subtasks to continue the workflow.\n"
        "3. If the goal is complete or nothing more can be done, return an empty array [].\n"
        "Respond ONLY with a valid JSON array of subtask strings (e.g. [\"subtask 1\", \"subtask 2\"]).\n"
    )
    try:
        from .llm import get_llm_client
        client = get_llm_client()
        messages = [
            {"role": "system", "content": "You are a web automation planner. Output valid JSON array only."},
            {"role": "user", "content": prompt},
        ]
        parsed, _, _, _ = client.chat_json(messages, timeout=10.0)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            revised = [s.strip() for s in parsed if s.strip()]
            if revised:
                # Preserve upcoming planned subtasks for subsequent phases/sites
                for rem_st in remaining_subtasks:
                    if not any(rem_st.lower() in r.lower() or r.lower() in rem_st.lower() for r in revised):
                        revised.append(rem_st)
                return revised
    except Exception:
        pass
    return None


def _remediate_via_heuristics(
    goal: str,
    failed_subtask: str,
    failure_reason: str,
    current_url: str,
    page_title: str,
    screen_elements: List[Dict[str, Any]],
    remaining_subtasks: List[str],
) -> Optional[List[str]]:
    low_st = failed_subtask.lower()

    # Check if looking for an entity/item and page has search input
    has_search_input = any(
        (el.get("tag") == "input" and any(k in (el.get("name") or el.get("placeholder") or el.get("id") or "").lower() for k in ("search", "query", "find")))
        for el in screen_elements
    )
    has_entity = any(w in low_st for w in ("search", "find", "product", "item", "page for", "for "))
    if has_search_input and has_entity:
        m = re.search(r"\b(?:for|find|search)\s+([a-zA-Z0-9\s\-_]+?)(?:\s+(?:on|in|at|page|from)\b|$)", failed_subtask, flags=re.IGNORECASE)
        query = m.group(1).strip() if m else ""
        if query and len(query) > 2:
            clean_query = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", query, flags=re.IGNORECASE).strip()
            return [f"Enter '{clean_query}' into search input box and submit"] + remaining_subtasks

    # If there are remaining subtasks for other websites or subsequent phases, advance with them
    if remaining_subtasks:
        return remaining_subtasks

    return None
