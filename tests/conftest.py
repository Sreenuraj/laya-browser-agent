"""Pytest configuration for deterministic offline test suites (Tier 1)."""

import pytest
from laya_browser_agent.llm import RecordedLLMClient, set_llm_client
from laya_browser_agent.extractor import SlotExtractor


def deterministic_llm_fixture_handler(messages, schema=None):
    """Fallback handler mapping prompt texts to deterministic plan/routing structures."""
    user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    sys_msg = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")

    import re

    # 1. Parameter and plan organization
    if "parameter and plan organizer" in sys_msg or "task_planner" in sys_msg or "User request:" in user_msg:
        m = re.search(r"User request:\s*(.*?)(?:\n\nRespond in valid JSON|\Z)", user_msg, re.DOTALL)
        actual_task = m.group(1).strip() if m else user_msg
        ext = SlotExtractor()
        plan = ext._extract_via_heuristics(actual_task)
        return {
            "intent": actual_task[:60],
            "slots": plan.slots,
            "subtasks": plan.subtasks,
            "missing_details": plan.missing_details,
            "clarifying_question": plan.clarifying_question,
        }

    # 2. Dynamic screen grounding / progressive expansion
    if "grounding subtasks from live screen" in sys_msg:
        return None

    # 3. Visual screen locator fallback
    if "autonomous browser vision and routing" in user_msg:
        if "products" in user_msg.lower():
            return {"action": "click_products", "coords": {"x": 541, "y": 32}, "reasoning": "Navigate to products"}
        return {"action": "other", "coords": {"x": 0, "y": 0}, "reasoning": "No matching element"}

    # 4. Remediation
    if "Analyze why the subtask failed" in user_msg:
        return ["search for product in search bar"]

    return {}


@pytest.fixture(scope="session", autouse=True)
def isolated_laya_home(tmp_path_factory):
    """Redirects the prompt cache DB and trajectory logs to a temp dir so tests never touch ~/.laya."""
    import os
    from laya_browser_agent.cache import global_prompt_cache

    home = tmp_path_factory.mktemp("laya_home")
    prev_env = os.environ.get("LAYA_HOME")
    prev_db = global_prompt_cache.db_path
    os.environ["LAYA_HOME"] = str(home)
    global_prompt_cache.db_path = str(home / "prompt_cache.db")
    global_prompt_cache._init_db()
    yield home
    global_prompt_cache.db_path = prev_db
    if prev_env is None:
        os.environ.pop("LAYA_HOME", None)
    else:
        os.environ["LAYA_HOME"] = prev_env


@pytest.fixture(autouse=True)
def setup_deterministic_recorded_llm():
    """Activates RecordedLLMClient for all tests to ensure 100% offline determinism."""
    client = RecordedLLMClient(fallback_handler=deterministic_llm_fixture_handler)
    set_llm_client(client)
    from laya_browser_agent.cache import global_prompt_cache
    global_prompt_cache.clear()
    yield client
    set_llm_client(None)
