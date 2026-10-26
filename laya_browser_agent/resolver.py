"""Dynamic parameter and entity resolver for WebMCP tools and DOM elements."""

import json
import re
from typing import Any, Dict, List, Optional


def parse_tool_result(raw_result: Any) -> Any:
    """Parses tool return value into structured JSON if returned as a string."""
    if isinstance(raw_result, str):
        try:
            return json.loads(raw_result)
        except Exception:
            return raw_result
    return raw_result


def find_matching_screen_element(tool_name: str, elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Finds screen element that semantically matches a tool name for visual fallback."""
    tool_clean = tool_name.lower().replace("_", " ")
    for el in elements:
        text = el.get("text", "").lower()
        if text and (text in tool_clean or tool_clean in text):
            return el
    return None


def find_matching_id_in_data(data: Any, prefix: str) -> Optional[str]:
    """Recursively searches for any 'id' field starting with prefix."""
    if isinstance(data, list):
        for item in data:
            res = find_matching_id_in_data(item, prefix)
            if res:
                return res
    elif isinstance(data, dict):
        if "id" in data and str(data["id"]).lower().startswith(prefix):
            return str(data["id"])
        for v in data.values():
            if isinstance(v, (dict, list)):
                res = find_matching_id_in_data(v, prefix)
                if res:
                    return res
    return None


def expand_id_prefix(
    prefix: str,
    prop_name: str,
    last_result: Any = None,
    driver: Any = None,
) -> Optional[str]:
    """Resolves a short ID prefix (e.g. '360f30c4') to full 36-char UUID via getter tools or DOM."""
    clean_prefix = str(prefix).strip().lower()
    if not clean_prefix:
        return None

    # 1. Try resolving from prior tool results
    if last_result:
        matched = find_matching_id_in_data(last_result, clean_prefix)
        if matched:
            return matched

    # 2. Try calling query/getter tool dynamically discovered for this entity
    if driver and hasattr(driver, "discover_tools") and hasattr(driver, "call_tool"):
        entity = re.sub(r"[_\-]id$", "", prop_name.lower()).replace("id", "")
        available = driver.discover_tools() or []
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
                raw = driver.call_tool(getter_tool, {})
                items = parse_tool_result(raw)
                matched = find_matching_id_in_data(items, clean_prefix)
                if matched:
                    return matched
            except Exception:
                pass

    return None


def find_contextual_entity(
    prop_name: str,
    prop_desc: str,
    context: Any,
    extracted_slots: Optional[Dict[str, Any]] = None,
) -> Any:
    """Finds entity identifiers or fields from previous tool output."""
    if prop_name.lower() in ("status", "message", "currentpath", "tool", "error"):
        return None

    slots = extracted_slots or {}
    is_id_field = "id" in prop_name.lower() or "id" in prop_desc
    is_product_field = any(w in prop_name.lower() or w in prop_desc for w in ("product", "sku", "item"))
    is_order_field = any(w in prop_name.lower() or w in prop_desc for w in ("order", "orderid"))

    if isinstance(context, list) and context:
        target_sku = str(slots.get("search") or slots.get("product") or "").lower()
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
        for k, v in context.items():
            if is_product_field and "order" in k.lower():
                continue
            if is_order_field and any(w in k.lower() for w in ("product", "item")):
                continue
            if isinstance(v, list) and v:
                nested_res = find_contextual_entity(prop_name, prop_desc, v, slots)
                if nested_res is not None:
                    return nested_res

        if is_id_field and "id" in context:
            is_order_ctx = any(k in context for k in ("orderNumber", "totalAmount", "order")) or "order" in str(context.get("status", "")).lower()
            if not (is_product_field and is_order_ctx):
                return context["id"]

        if is_id_field and "product" in context and isinstance(context["product"], dict) and "id" in context["product"]:
            if not is_order_field:
                return context["product"]["id"]

        if prop_name in context:
            return context[prop_name]

        for k, val in context.items():
            if is_product_field and "order" in k.lower():
                continue
            if is_order_field and any(w in k.lower() for w in ("product", "item")):
                continue
            if isinstance(val, dict):
                if is_id_field and "id" in val:
                    return val["id"]
                if prop_name in val:
                    return val[prop_name]

    return None


def infer_path_from_subtask(
    subtask: str,
    goal: str = "",
    screen_elements: Optional[List[Dict[str, Any]]] = None,
    extracted_slots: Optional[Dict[str, Any]] = None,
) -> str:
    """Dynamically discovers destination path from page links matching the subtask or goal."""
    subtask_text = subtask or goal
    slots = extracted_slots or {}

    # 1. Dynamic discovery from screen elements on the active page
    if screen_elements:
        subtask_tokens = [w for w in re.findall(r"\w+", subtask_text.lower()) if len(w) > 2 and w not in ("the", "and", "for", "got", "get")]
        for el in screen_elements:
            href = el.get("href", "")
            if href and href.startswith("/"):
                el_text = el.get("text", "").lower()
                if any(token in el_text or token in href.lower() for token in subtask_tokens):
                    return href

    # 2. Check for explicit path token in subtask
    match = re.search(r"(?<![a-zA-Z0-9])(/[a-zA-Z0-9_\-]+)(?![@a-zA-Z0-9])", subtask_text)
    if match:
        return match.group(1)

    # 3. Check for explicit path token in overall goal
    if goal:
        for token in re.findall(r"(?<![a-zA-Z0-9])(/[a-zA-Z0-9_\-]+)(?![@a-zA-Z0-9])", goal):
            if "@" not in token and not any(token.lower().endswith(str(u).lower()) for u in slots.values() if u):
                return token

    return "/"


def resolve_tool_params(
    tool_def: Optional[Dict[str, Any]],
    extracted_slots: Optional[Dict[str, Any]] = None,
    last_result: Any = None,
    subtask: str = "",
    goal: str = "",
    screen_elements: Optional[List[Dict[str, Any]]] = None,
    fallback_params: Optional[Dict[str, Any]] = None,
    driver: Any = None,
) -> Dict[str, Any]:
    """Universally maps slots, schema properties, and prior step results to tool parameters."""
    if not tool_def:
        return {}

    schema = tool_def.get("inputSchema") or {}
    properties: Dict[str, Any] = schema.get("properties") or {}
    resolved: Dict[str, Any] = {}
    slots = extracted_slots or {}

    if not properties:
        return {}

    for prop_name, prop_meta in properties.items():
        prop_type = prop_meta.get("type", "string") if isinstance(prop_meta, dict) else "string"
        prop_desc = (prop_meta.get("description", "") if isinstance(prop_meta, dict) else "").lower()
        value = None

        # 0. High-confidence credentials from extracted_slots take precedence
        if prop_name in ("username", "password") and slots.get(prop_name):
            value = slots[prop_name]
        elif fallback_params and prop_name in fallback_params and fallback_params[prop_name] not in (None, ""):
            value = fallback_params[prop_name]

        # A. Exact or normalized slot match
        if value is None:
            norm_prop = prop_name.lower().replace("_", "").replace("-", "")
            for slot_k, slot_v in slots.items():
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
                        if syn in slots:
                            value = slots[syn]
                            break
                if value is not None:
                    break

        # C. Contextual entity binding from previous tool output
        if value is None and last_result is not None:
            value = find_contextual_entity(prop_name, prop_desc, last_result, slots)

        # D. Dynamic navigation paths discovered from page links
        if value is None and prop_name in ("path", "url", "route"):
            value = infer_path_from_subtask(subtask, goal, screen_elements, slots)

        # E. Short ID prefix resolution
        if value and isinstance(value, str) and len(value) < 36 and ("id" in prop_name.lower() or "id" in prop_desc):
            full_id = expand_id_prefix(value, prop_name, last_result, driver)
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
