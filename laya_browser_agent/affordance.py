"""Dynamic DOM affordance discovery, semantic labeling, and candidate extraction."""

import re
from typing import Any, Dict, List, Optional, Set


def normalize_route_key(url: str) -> str:
    """Normalizes URL to origin + path (e.g. 'localhost:3000/orders')."""
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    if "://" in clean:
        clean = clean.split("://", 1)[-1]
    return clean or "root"


def tool_matches_subtask(tool_name: str, subtask: str) -> bool:
    """Verifies whether a tool's action capability aligns with the subtask's action verb."""
    st = subtask.lower()
    t = tool_name.lower().replace("tool_", "")

    # Dropdown / combobox selections: keyword search or mutating tools do NOT match!
    is_dropdown_task = any(w in st for w in ("dropdown", "combobox", "select option", "choose option", "select status", "filter by status", "from dropdown"))
    if is_dropdown_task and any(w in t for w in ("search", "find", "query", "submit", "create", "delete", "post")):
        return False

    # Action Category Guard:
    # Pure navigation/inspection to an index or listing must never trigger a mutating submission tool.
    is_nav_or_view = any(w in st for w in ("go to", "navigate to", "open", "visit", "view", "head to", "check", "inspect")) and not any(w in st for w in ("place", "buy", "purchase", "submit", "create", "delete", "cancel"))
    is_mutating_tool = any(w in t for w in ("place", "submit", "create", "delete", "post", "checkout"))
    if is_nav_or_view and is_mutating_tool:
        return False

    # Token overlap between tool name segments and subtask words
    t_words = [w for w in re.split(r"[_\W]+", t) if len(w) > 2 and w not in ("tool", "get", "set", "the", "page", "user")]
    s_words = [w for w in re.findall(r"\w+", st) if len(w) > 2]

    # 1. Direct word overlap
    overlap = set(t_words) & set(s_words)
    if overlap:
        return True

    # 2. Universal action verb equivalences (browser agent primitives)
    ACTION_GROUPS = [
        {"login", "signin", "authenticate", "credential"},
        {"logout", "signout", "logoff", "exit"},
        {"navigate", "goto", "open", "visit", "head"},
        {"search", "query", "find", "lookup", "filter"},
        {"buy", "purchase", "order", "checkout"},
        {"approve", "accept", "confirm"},
        {"reject", "decline", "deny"},
        {"cancel", "abort", "delete", "remove"},
    ]
    for group in ACTION_GROUPS:
        if any(w in t for w in group) and any(w in st for w in group):
            return True

    return False


def extract_page_candidates(
    available_tools: List[Dict[str, Any]],
    screen_elements: List[Dict[str, Any]],
    current_url: str,
    excluded_actions: Optional[Set[str]] = None,
    scroll_state: Optional[Dict[str, Any]] = None,
    can_go_back: bool = False,
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

    # 2. Form Inputs & Textareas (Only if page has no WebMCP auth tool or non-auth inputs)
    if not has_auth_tool:
        for el in screen_elements:
            tag = el.get("tag", "")
            el_type = el.get("type", "")
            if tag in ("input", "textarea") and el_type not in ("submit", "button", "checkbox", "radio", "hidden"):
                field_name = el.get("name") or el.get("id") or el.get("placeholder") or el.get("text") or "input"
                clean_field = re.sub(r"[^a-zA-Z0-9_]", "_", field_name).strip("_").lower()
                key = f"input_{clean_field[:25]}"
                if key in candidates or key in excluded:
                    continue
                region = el.get("region", "toolbar")

                # Semantic search input detection
                is_search_box = (
                    el_type == "search"
                    or el.get("role") == "searchbox"
                    or field_name.lower() in ("q", "query", "search", "search_query", "field-keywords", "twotabsearchtextbox")
                    or any(
                        sw in (field_name.lower() + " " + (el.get("placeholder") or "").lower() + " " + (el.get("aria_label") or "").lower() + " " + (el.get("title") or "").lower())
                        for sw in ("search", "query", "find", "twotabsearchtextbox", "field-keywords", "searchbox")
                    )
                )
                if is_search_box:
                    label = f"[{region.capitalize()}] Search input box - type query and press Enter (field={field_name})"
                else:
                    label = f"[{region.capitalize()}] Enter {field_name} (type={el_type or 'text'})"

                candidates[key] = {
                    "type": "input",
                    "field": field_name,
                    "input_type": "search" if is_search_box else (el_type or "text"),
                    "region": region,
                    "label": label,
                    "coords": (el["x"], el["y"]) if el.get("x") is not None else None,
                    "is_search": is_search_box,
                }

    # 3. Action Buttons, Navigation Links, Dropdowns, and Overlay Elements
    for el in screen_elements:
        tag = el.get("tag", "")
        el_type = el.get("type", "")
        role = el.get("role", "")
        is_checkbox = bool(el_type == "checkbox" or role in ("checkbox", "switch"))
        is_radio = bool(el_type == "radio" or role == "radio")

        if tag in ("input", "textarea") and el_type not in ("submit", "button") and not is_checkbox and not is_radio:
            continue

        text = (el.get("text") or el.get("aria_label") or el.get("name") or el.get("id") or el.get("value") or "").strip()
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
        region = el.get("region", "content")

        # Filter out self-links to prevent navigation loops
        if href and (current_url.rstrip("/").endswith(href.rstrip("/")) or href.rstrip("/") in current_url):
            continue

        is_nav = bool(href or tag == "a" or role in ("link", "tab") or region == "navigation")
        is_combobox = bool(role == "combobox" or tag == "select")
        is_option = bool(role in ("option", "menuitem"))
        is_pagination = bool(region == "pagination" or any(w in text.lower() for w in ("next", "previous", "prev", "page ")))

        # Filter out logo/home links when on deeper page to avoid resetting session/looping
        if is_nav and href in ("/", "#"):
            current_path = current_url.split("://")[-1].split("/", 1)[-1].strip("/")
            if current_path:
                continue

        if is_checkbox:
            label = f"[{region.capitalize()}] Toggle checkbox '{text}'"
            cand_type = "checkbox"
        elif is_radio:
            label = f"[{region.capitalize()}] Select radio option '{text}'"
            cand_type = "radio"
        elif is_combobox:
            label = f"[{region.capitalize()}] Select '{text}' dropdown filter"
            cand_type = "dropdown"
        elif is_option:
            label = f"[Overlay] Choose option '{text}'"
            cand_type = "option"
        elif is_pagination:
            label = f"[Pagination] Go to '{text}'"
            cand_type = "pagination"
        elif is_nav:
            label = f"[Navigation] Navigate to '{text}' page"
            cand_type = "navigate"
        elif region == "overlay":
            label = f"[Modal] Click '{text}'"
            cand_type = "overlay"
        elif region == "toolbar":
            label = f"[Toolbar] Click '{text}'"
            cand_type = "toolbar"
        else:
            label = f"{text.rstrip('.')}"
            cand_type = "click"

        candidates[key] = {
            "type": cand_type,
            "text": text,
            "region": region,
            "label": label,
            "coords": (el["x"], el["y"]) if el.get("x") is not None else None,
            "path": href,
        }

    # 4. Universal Browser Primitives (Always available affordances)
    can_down = bool(scroll_state.get("can_scroll_down", True)) if scroll_state is not None else True
    can_up = bool(scroll_state.get("can_scroll_up", False)) if scroll_state is not None else False

    if can_down and "browser_scroll_down" not in excluded:
        candidates["browser_scroll_down"] = {
            "type": "primitive",
            "label": "Scroll down to reveal elements below the fold",
        }
    if can_up and "browser_scroll_up" not in excluded:
        candidates["browser_scroll_up"] = {
            "type": "primitive",
            "label": "Scroll up to reveal elements above the fold",
        }
    if "browser_take_screenshot" not in excluded:
        candidates["browser_take_screenshot"] = {
            "type": "primitive",
            "label": "Capture screenshot of current screen state",
        }
    if "browser_reload" not in excluded:
        candidates["browser_reload"] = {
            "type": "primitive",
            "label": "Reload or refresh current page",
        }
    if can_go_back and "browser_go_back" not in excluded:
        candidates["browser_go_back"] = {
            "type": "primitive",
            "label": "Navigate back to previous page",
        }

    return candidates
