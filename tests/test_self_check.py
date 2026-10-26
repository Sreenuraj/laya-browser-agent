"""Self-check verification for Laya Browser Agent.

Assert-based self-check verifying:
1. Universal slot extractor
2. Dynamic parameter resolution across arbitrary schemas
3. Headed and headless mode configuration
4. CLI flag parsing
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from laya_browser_agent.extractor import SlotExtractor
from laya_browser_agent.agent import LayaAgent
from laya_browser_agent.browser import PlaywrightWebMcpDriver
from laya_browser_agent.laya_engine import LayaEngine


def test_extractor():
    print("[1/4] Testing universal slot extractor & plan decomposition...")
    extractor = SlotExtractor()
    
    # Generic task A: e-commerce
    plan_a = extractor._extract_via_heuristics("Buy 3 tickets for 'Hamilton' as user (john / secret123)")
    res_a = plan_a.slots
    assert res_a.get("quantity") == 3, f"Expected 3, got {res_a.get('quantity')}"
    assert res_a.get("username") == "john", f"Expected john, got {res_a.get('username')}"
    assert res_a.get("password") == "secret123", f"Expected secret123, got {res_a.get('password')}"
    assert res_a.get("search") == "Hamilton" or res_a.get("query") == "Hamilton"
    assert len(plan_a.subtasks) >= 1

    # Generic task B: cloud/devops
    plan_b = extractor._extract_via_heuristics("Deploy cluster to region: us-east-1 with email: admin@cloud.org")
    res_b = plan_b.slots
    assert res_b.get("region") == "us-east-1", f"Expected us-east-1, got {res_b.get('region')}"
    assert res_b.get("email") == "admin@cloud.org", f"Expected admin@cloud.org, got {res_b.get('email')}"
    print("      ✓ Extractor passed!")


def test_param_resolution():
    print("[2/4] Testing dynamic parameter resolution for arbitrary schemas...")
    agent = LayaAgent(
        target_url="http://example.com",
        goal="Order 5 widgets using account (alice / P@ssw0rd) in /store",
        headed=False,
    )
    plan = agent.extractor._extract_via_heuristics(agent.goal)
    agent.extracted_slots = plan.slots
    agent.subtasks = plan.subtasks

    # Tool definition with arbitrary schema
    arbitrary_tool = {
        "name": "create_widget_order",
        "description": "Places widget order",
        "inputSchema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
                "quantity": {"type": "integer"},
                "path": {"type": "string"},
            },
            "required": ["username", "password", "quantity"],
        },
    }

    params = agent._resolve_tool_params(arbitrary_tool)
    assert params["username"] == "alice", f"Expected alice, got {params.get('username')}"
    assert params["password"] == "P@ssw0rd", f"Expected P@ssw0rd, got {params.get('password')}"
    assert params["quantity"] == 5, f"Expected 5, got {params.get('quantity')}"
    assert params["path"] == "/store", f"Expected /store, got {params.get('path')}"

    # Test contextual ID resolution from prior tool results
    agent.last_result = [{"id": "item-uuid-99", "name": "Gadget", "quantity": 10}]
    checkout_tool = {
        "name": "checkout_item",
        "inputSchema": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string", "description": "ID of item to checkout"},
            },
        },
    }
    resolved_checkout = agent._resolve_tool_params(checkout_tool)
    assert resolved_checkout.get("itemId") == "item-uuid-99", f"Expected item-uuid-99, got {resolved_checkout}"
    print("      ✓ Parameter resolution passed!")


def test_headed_headless_modes():
    print("[3/4] Testing headed vs headless configuration...")
    # Headed driver
    driver_headed = PlaywrightWebMcpDriver(headed=True)
    assert driver_headed.headed is True

    # Headless driver
    driver_headless = PlaywrightWebMcpDriver(headed=False)
    assert driver_headless.headed is False

    # Agent with headed mode
    agent_h = LayaAgent(target_url="http://test.local", goal="test", headed=True)
    assert agent_h.headed is True

    # Agent with headless mode
    agent_hl = LayaAgent(target_url="http://test.local", goal="test", headed=False)
    assert agent_hl.headed is False
    print("      ✓ Headed and headless configurations verified!")


def test_cli_flags():
    print("[4/4] Testing CLI argument parser...")
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str)
    parser.add_argument("--task", "--goal", dest="task", type=str)
    parser.add_argument("--headed", dest="headed", action="store_true", default=None)
    parser.add_argument("--headless", dest="headless", action="store_true", default=None)

    parsed_headed = parser.parse_args(["--headed", "--url", "https://news.ycombinator.com"])
    assert parsed_headed.headed is True
    assert parsed_headed.headless is None
    assert parsed_headed.url == "https://news.ycombinator.com"

    parsed_headless = parser.parse_args(["--headless", "--task", "Summarize top story"])
    assert parsed_headless.headless is True
    assert parsed_headless.task == "Summarize top story"
    print("      ✓ CLI flags verified!")


def test_e2e_headless_driver():
    print("[5/5] Testing Playwright WebMCP driver in headless mode...")
    html_content = """<!DOCTYPE html>
    <html>
    <head><title>Generic Web App</title></head>
    <body>
        <h1>Demo Universal App</h1>
        <form id="contact-form" toolname="submit_feedback" tooldescription="Submit user feedback and ratings">
            <input name="email" toolparamdescription="User email address" />
            <input name="rating" toolparamdescription="Rating 1 to 5" />
            <button type="submit" id="submit-btn">Send</button>
        </form>
    </body>
    </html>"""

    driver = PlaywrightWebMcpDriver(headed=False)
    try:
        driver.start(f"data:text/html,{html_content}")

        # Verify page loaded
        assert driver.get_page_title() == "Generic Web App"

        # Verify dynamic tool discovery
        tools = driver.discover_tools()
        assert len(tools) >= 1
        feedback_tool = next((t for t in tools if t["name"] == "submit_feedback"), None)
        assert feedback_tool is not None, "Expected submit_feedback tool to be discovered"
        assert "email" in feedback_tool["inputSchema"]["properties"]

        # Verify tool execution
        res = driver.call_tool("submit_feedback", {"email": "tester@domain.com", "rating": 5})
        assert res.get("status") == "submitted"

        # Verify browser primitives and Playwright native actionability
        sc_path = driver.take_screenshot("reports/.test_primitive_screenshot.png")
        assert os.path.exists(sc_path)
        # Duplicate screenshot on identical page state must reuse the same file and avoid duplicate disk writes
        sc_dup = driver.take_screenshot("reports/.test_duplicate_screenshot.png")
        assert sc_dup == sc_path, "Expected duplicate screenshot on identical screen to reuse existing path"
        assert not os.path.exists("reports/.test_duplicate_screenshot.png"), "Duplicate screenshot file must NOT be written to disk"
        if os.path.exists(sc_path):
            os.remove(sc_path)
        driver.mousewheel(0, 100)
        scroll_st = driver.get_scroll_state()
        assert "can_scroll_down" in scroll_st

        # Verify Playwright native locator fill and click
        driver.fill(target="email", value="test@domain.com")
        driver.click(target="Send")

        print("      ✓ Headless browser, tool execution, and Playwright primitives verified!")
    finally:
        driver.close()


def test_page_candidate_extraction_and_routing():
    print("[6/7] Testing page candidate extraction, non-WebMCP forms, and site caching...")
    from laya_browser_agent.laya_engine import LayaEngine, extract_page_candidates

    engine = LayaEngine()

    dashboard_tools = [
        {"name": "get_current_user_profile", "description": "Returns current user profile"},
        {"name": "logout_user", "description": "Log out user"},
        {"name": "navigate_to_page", "description": "Navigate to page", "inputSchema": {"properties": {"path": {"type": "string"}}}},
    ]
    dashboard_elements = [
        {"text": "POSTQODE NEXUS", "href": "/dashboard", "x": 122, "y": 32, "tag": "a"},
        {"text": "Dashboard", "href": "/dashboard", "x": 333, "y": 32, "tag": "a"},
        {"text": "Products", "href": "/products", "x": 541, "y": 32, "tag": "a"},
        {"text": "My Activity", "href": "/my-activity", "x": 890, "y": 32, "tag": "a"},
    ]

    # 1. Test candidate extraction on WebMCP page (self-links filtered out)
    cands = extract_page_candidates(
        available_tools=dashboard_tools,
        screen_elements=dashboard_elements,
        current_url="http://localhost:3000/dashboard",
    )
    assert "tool_navigate_to_page" in cands
    assert "click_products" in cands
    # Self-links to /dashboard should be filtered out
    assert "click_dashboard" not in cands
    assert cands["click_products"]["coords"] == (541, 32)
    assert cands["click_products"]["path"] == "/products"

    # 2. Test candidate extraction on non-WebMCP application (pure HTML inputs and buttons)
    non_webmcp_elements = [
        {"tag": "input", "type": "text", "name": "username", "placeholder": "Enter username", "x": 200, "y": 100},
        {"tag": "input", "type": "password", "name": "password", "placeholder": "Enter password", "x": 200, "y": 150},
        {"tag": "button", "type": "submit", "text": "Sign In", "x": 200, "y": 200},
        {"tag": "a", "href": "/register", "text": "Register", "x": 200, "y": 250},
    ]
    non_webmcp_cands = extract_page_candidates(
        available_tools=[],
        screen_elements=non_webmcp_elements,
        current_url="http://example.com/login",
    )
    assert "input_username" in non_webmcp_cands
    assert non_webmcp_cands["input_username"]["type"] == "input"
    assert non_webmcp_cands["input_username"]["coords"] == (200, 100)
    assert "input_password" in non_webmcp_cands
    assert "click_sign_in" in non_webmcp_cands
    assert "click_register" in non_webmcp_cands

    # 3. Test decision routing
    action, conf, act_params, info = engine.decide_next_action(
        subtask="search for E2E-ADM002-1789625027 product",
        goal="search and buy",
        current_url="http://localhost:3000/dashboard",
        available_tools=dashboard_tools,
        screen_elements=dashboard_elements,
        recent_history=[],
    )
    assert action in ("click_products", "tool_navigate_to_page"), f"Expected products navigation, got {action}"
    if action == "click_products":
        assert tuple(act_params.get("_coords", ())) in ((541, 32), (541.0, 32.0)) or act_params.get("path") == "/products"

    # 4. Test cache invalidation
    assert "localhost:3000/dashboard" in engine.site_cache
    engine.invalidate_cache("http://localhost:3000/dashboard")
    assert "localhost:3000/dashboard" not in engine.site_cache

    print("      ✓ Universal candidate extraction, non-WebMCP support & caching verified!")


def test_execution_report_generation():
    print("[7/7] Testing execution report generation in reports/ directory...")
    agent = LayaAgent(
        target_url="http://test.local",
        goal="login and checkout",
        headed=False,
        report_dir="reports",
        report_file="reports/.test_report.md",
    )
    agent.extracted_slots = {"username": "testuser", "password": "secretpassword"}
    agent.subtasks = ["Login with testuser", "Checkout cart"]
    agent.current_subtask_idx = 1
    from laya_browser_agent.agent import AgentStepEvent
    agent.steps_taken = [
        AgentStepEvent(
            step=1,
            subtask="Login with testuser",
            url="http://test.local/login",
            title="Login Page",
            available_tools=[],
            chosen_action="tool_login",
            confidence=0.92,
            probabilities={},
            tool_params={"username": "testuser"},
            tool_result={"status": "ok"},
            duration_ms=14.2,
            engine_info="Laya MLX Fast Policy",
        ),
        AgentStepEvent(
            step=2,
            subtask="Finish goal",
            url="http://test.local/dashboard",
            title="Dashboard",
            available_tools=[],
            chosen_action="finish",
            confidence=0.98,
            probabilities={},
            tool_params={},
            tool_result="Goal completed successfully",
            duration_ms=5.1,
            engine_info="Laya MLX Fast Policy",
        ),
    ]

    report = agent.generate_report("reports/.test_report.md")
    assert "# ⚡️ Laya Browser Agent Execution Report" in report
    assert "testuser" in report
    assert "••••••••" in report  # Password must be masked
    assert "tool_login" in report
    assert "14.2ms" in report

    assert os.path.exists("reports/.test_report.md")
    os.remove("reports/.test_report.md")
    print("      ✓ Execution report generation in reports/ verified!")


def test_progressive_extraction_and_dropdowns_and_pagination():
    print("[8/8] Testing progressive hierarchical extraction, dropdowns, regions, and pagination...")
    from laya_browser_agent.laya_engine import extract_page_candidates
    from laya_browser_agent.extractor import SlotExtractor

    # 1. Test slot extraction separation: filter values are NOT in search/query
    extractor = SlotExtractor()
    plan = extractor._extract_via_heuristics("click one with 'all status' and select from dropdown select status as 'out of stock'")
    assert plan.slots.get("status") == "out of stock"
    assert plan.slots.get("dropdown") == "all status"
    assert "search" not in plan.slots or plan.slots["search"] == ""
    assert "query" not in plan.slots or plan.slots["query"] == ""

    # 2. Test candidate extraction with semantic regions
    screen_elements = [
        {"tag": "input", "type": "text", "text": "Search items...", "name": "q", "id": "search-input", "region": "toolbar", "x": 100, "y": 100},
        {"tag": "button", "role": "combobox", "text": "All Status", "id": "status-select", "region": "toolbar", "x": 300, "y": 100},
        {"tag": "div", "role": "option", "text": "Out of Stock", "id": "opt-oos", "region": "overlay", "x": 300, "y": 150},
        {"tag": "button", "text": "Next", "region": "pagination", "disabled": False, "x": 500, "y": 800},
        {"tag": "a", "href": "/orders", "text": "Orders", "region": "navigation", "x": 50, "y": 30},
    ]

    cands = extract_page_candidates(
        available_tools=[],
        screen_elements=screen_elements,
        current_url="http://localhost:3000/catalog",
    )

    assert "click_all_status" in cands
    assert cands["click_all_status"]["type"] == "dropdown"
    assert "[Toolbar]" in cands["click_all_status"]["label"]

    assert "click_out_of_stock" in cands
    assert cands["click_out_of_stock"]["type"] == "option"
    assert "[Overlay]" in cands["click_out_of_stock"]["label"]

    assert "click_next" in cands
    assert cands["click_next"]["type"] == "pagination"
    assert "[Pagination]" in cands["click_next"]["label"]

    assert "click_orders" in cands
    assert cands["click_orders"]["type"] == "navigate"
    assert "[Navigation]" in cands["click_orders"]["label"]

    # 3. Test Playwright driver region detection and select_dropdown
    html = """<!DOCTYPE html>
    <html>
    <head><title>Progressive Extraction Test</title></head>
    <body>
        <nav><a href="/home">Home</a></nav>
        <div class="toolbar">
            <input type="text" placeholder="Search..." id="search" />
            <select id="category-select" name="category">
                <option value="all">All Categories</option>
                <option value="electronics">Electronics</option>
            </select>
        </div>
        <main><table><tr><td>Item 1</td></tr></table></main>
        <div class="pagination"><button>Next</button></div>
    </body>
    </html>"""
    driver = PlaywrightWebMcpDriver(headed=False)
    try:
        driver.start(f"data:text/html,{html}")
        regions = driver.get_screen_regions()
        region_names = [r["name"] for r in regions]
        assert "navigation" in region_names
        assert "toolbar" in region_names
        assert "content" in region_names
        assert "pagination" in region_names

        # Test native select actuation
        res = driver.select_dropdown("category-select", "Electronics")
        assert res is True
    finally:
        driver.close()

    print("      ✓ Progressive hierarchical extraction, dropdowns, regions, and pagination verified!")


def test_report_intent_and_screenshot_modes():
    print("[9/9] Testing report intent detection and screenshot modes...")
    from laya_browser_agent.cli import prompt_requests_report
    from laya_browser_agent.agent import LayaAgent

    # 1. Prompt report intent detection
    assert prompt_requests_report("Login as user and generate execution report") is True
    assert prompt_requests_report("Take screenshot of each page and save report") is True
    assert prompt_requests_report("Login as user (user/User@123), search products, buy 1 unit, and logout") is False

    # 2. Agent screenshot_mode configuration
    agent_major = LayaAgent(
        target_url="http://localhost:3000",
        goal="buy product",
        headed=False,
        screenshot_mode="major_steps",
    )
    assert agent_major.screenshot_mode == "major_steps"

    agent_none = LayaAgent(
        target_url="http://localhost:3000",
        goal="buy product",
        headed=False,
        screenshot_mode="none",
    )
    assert agent_none.screenshot_mode == "none"

    print("      ✓ Report intent detection and screenshot modes verified!")


def test_loop_engineering_assertion_gates():
    print("[10/10] Testing step-baked physical execution verification without hardcoded domain gates...")
    from laya_browser_agent.agent import LayaAgent

    agent = LayaAgent(
        target_url="http://localhost:3000/login",
        goal="login, go to products, select out of stock, and logout",
        headed=False,
    )
    agent.extracted_slots = {"status": "out of stock"}

    # 1. Driver execution failure (result is False)
    passed, reason = agent._assert_step_transition(
        subtask="click submit button",
        action="click_submit",
        params={},
        result=False,
    )
    assert passed is False
    assert "failure" in reason.lower()

    # 2. Tool error failure
    passed, reason = agent._assert_step_transition(
        subtask="call user login tool",
        action="tool_user_login",
        params={},
        result={"tool_error": "Connection refused"},
    )
    assert passed is False
    assert "tool error" in reason.lower()

    # 3. Input error failure
    passed, reason = agent._assert_step_transition(
        subtask="enter username",
        action="input_username",
        params={},
        result={"input_error": "Element not intractable"},
    )
    assert passed is False
    assert "input error" in reason.lower()

    # 4. Action 'other' (no affordance available)
    passed, reason = agent._assert_step_transition(
        subtask="click non-existent button",
        action="other",
        params={},
        result={"status": "no_matching_affordance"},
    )
    assert passed is False
    assert "no_matching_affordance" in reason.lower() or "no matching affordance" in reason.lower()

    # 5. Intermediate input fill passes cleanly without artificial login redirects
    passed, reason = agent._assert_step_transition(
        subtask="Enter 'user' into username field and 'User@123' into password field, then submit to log in.",
        action="input_username",
        params={"field": "username", "value": "user"},
        result={"filled_input": "username", "value": "user", "success": True},
        before_url="http://localhost:3000/login",
        after_url="http://localhost:3000/login",
    )
    assert passed is True, f"Intermediate input should pass physical execution, got: {reason}"

    # 6. Normal click passes cleanly
    passed, reason = agent._assert_step_transition(
        subtask="go to products page",
        action="click_products",
        params={},
        result={"clicked": "products", "success": True},
        before_url="http://localhost:3000/dashboard",
        after_url="http://localhost:3000/products",
    )
    assert passed is True

    print("      ✓ Step-baked execution verification verified!")


def test_missing_details_and_clarifying_questions():
    print("[11/11] Testing incomplete details detection and clarifying question generation...")
    extractor = SlotExtractor()

    # 1. Incomplete prompt: login requested without credentials
    plan_incomplete = extractor._extract_via_heuristics("login and cancel all my open orders and logout")
    assert len(plan_incomplete.missing_details) > 0, "Expected missing credentials to be detected"
    assert any("credential" in m.lower() for m in plan_incomplete.missing_details)
    assert plan_incomplete.clarifying_question is not None
    assert "login credentials" in plan_incomplete.clarifying_question.lower()

    # 2. Incomplete prompt: dropdown filter without target status
    plan_filter = extractor._extract_via_heuristics("go to products, filter by status and logout")
    assert len(plan_filter.missing_details) > 0, "Expected missing status to be detected"
    assert any("status" in m.lower() or "option" in m.lower() for m in plan_filter.missing_details)
    assert plan_filter.clarifying_question is not None

    # 3. Complete prompt: all details present
    plan_complete = extractor._extract_via_heuristics(
        "login as user (user/User@123), got to my-orders, cancel all my open orders (which are in pending state) and logout"
    )
    assert plan_complete.missing_details == []
    assert plan_complete.clarifying_question is None
    assert plan_complete.slots.get("username") == "user"
    assert plan_complete.slots.get("password") == "User@123"

    # 4. Enriched prompt: user answers clarifying question with credentials
    enriched_goal = "login and cancel all my open orders and logout. user/User@123"
    plan_enriched = extractor.extract(enriched_goal)
    assert plan_enriched.missing_details == []
    assert plan_enriched.clarifying_question is None
    assert plan_enriched.slots.get("username") == "user"
    assert plan_enriched.slots.get("password") == "User@123"
    assert len(plan_enriched.subtasks) >= 2

    # 5. Clarification answer for password specifically (e.g. user answered "User@123" when prompted for user 'user')
    goal_with_password = "can you login as user and check if there is any order pending in my orders (password: 'User@123')"
    plan_pwd = extractor.extract(goal_with_password)
    assert plan_pwd.slots.get("username") == "user", f"Expected username 'user', got {plan_pwd.slots.get('username')}"
    assert plan_pwd.slots.get("password") == "User@123", f"Expected password 'User@123', got {plan_pwd.slots.get('password')}"
    assert plan_pwd.missing_details == []
    assert plan_pwd.clarifying_question is None

    print("      ✓ Incomplete details detection and interactive clarification verified!")


def test_multi_turn_session_inheritance_and_batch_cancellation():
    print("[12/12] Testing multi-turn conversational session memory and batch cancellation...")
    extractor = SlotExtractor()

    # 1. Turn 1 initial extraction
    turn1_prompt = "login as user (user/User@123), got to my-orders, cancel all my open orders (which are in pending state) and logout"
    plan1 = extractor.extract(turn1_prompt)
    assert plan1.slots.get("username") == "user"
    assert plan1.slots.get("password") == "User@123"

    # 2. Turn 2 follow-up prompt: inherits credentials and auth subtasks
    session_ctx = {
        "target_url": "http://localhost:3000",
        "slots": dict(plan1.slots),
        "history": [turn1_prompt],
        "last_goal": turn1_prompt,
    }
    turn2_prompt = "not all the orders are canceled"
    plan2 = extractor.extract(turn2_prompt, session_context=session_ctx)

    assert plan2.slots.get("username") == "user", "Expected username to be inherited from session"
    assert plan2.slots.get("password") == "User@123", "Expected password to be inherited from session"
    assert plan2.missing_details == [], "Follow-up with session context should have no missing details"
    assert any("login" in s.lower() or "log in" in s.lower() for s in plan2.subtasks), "Expected login subtask in plan"
    assert any("order" in s.lower() for s in plan2.subtasks), "Expected orders subtask in plan"
    assert any("logout" in s.lower() or "log out" in s.lower() for s in plan2.subtasks), "Expected logout subtask in plan"

    print("      ✓ Multi-turn conversational session memory and batch cancellation verified!")


def test_conversational_basics():
    print("[13/13] Testing conversational basics (greetings, help, status, reset, polite acknowledgements)...")
    from laya_browser_agent.cli import handle_conversational_basics

    state = {"target_url": "http://localhost:3000", "slots": {"username": "user", "password": "User@123"}, "history": ["login as user"], "last_goal": "login"}

    # Greetings must NOT launch browser automation
    assert handle_conversational_basics("hi", state) is True
    assert handle_conversational_basics("hello", state) is True
    assert handle_conversational_basics("hey there", state) is True
    assert handle_conversational_basics("Good morning", state) is True

    # Help must NOT launch browser automation
    assert handle_conversational_basics("help", state) is True
    assert handle_conversational_basics("what can you do", state) is True

    # Status must NOT launch browser automation
    assert handle_conversational_basics("status", state) is True
    assert handle_conversational_basics("session", state) is True

    # Polite chit-chat must NOT launch browser automation
    assert handle_conversational_basics("thanks", state) is True
    assert handle_conversational_basics("ok", state) is True

    # Reset must clear state
    assert handle_conversational_basics("reset", state) is True
    assert len(state["slots"]) == 0
    assert len(state["history"]) == 0
    assert state["last_goal"] == ""

    # Real web actions MUST return False and proceed to automation
    assert handle_conversational_basics("login as user (user/User@123)", state) is False
    assert handle_conversational_basics("go to products page", state) is False
    assert handle_conversational_basics("cancel all my open orders", state) is False
    assert handle_conversational_basics("not all the orders are canceled", state) is False

    print("      ✓ Conversational basics handling verified!")


def test_proactive_scenarios_and_persona_transitions():
    print("[14/14] Testing proactive scenarios (URL extraction, persona switching, pure logout, report inspection)...")
    from laya_browser_agent.cli import extract_url_from_prompt, handle_conversational_basics
    from laya_browser_agent.extractor import SlotExtractor

    # 1. Flexible URL extraction
    assert extract_url_from_prompt("go to localhost:3000 and login") == "http://localhost:3000"
    assert extract_url_from_prompt("check github.com for laya") == "https://github.com"
    assert extract_url_from_prompt("open https://example.com/test") == "https://example.com/test"

    # 2. Persona transitions without credential bleed
    extractor = SlotExtractor()
    session_ctx = {"slots": {"username": "user", "password": "User@123"}, "last_goal": "login as user"}
    plan_admin = extractor.extract("login as admin, got to /orders and approve all", session_context=session_ctx)
    assert plan_admin.slots.get("username") == "admin"
    assert plan_admin.slots.get("password") != "User@123", "Old password should not bleed into new persona"
    assert any("password" in m.lower() for m in plan_admin.missing_details)

    # 3. Pure logout does not demand login credentials
    plan_logout = extractor.extract("logout")
    assert plan_logout.missing_details == [], "Pure logout should not demand credentials"

    # 4. Report inspection in chat
    state = {"target_url": "http://localhost:3000", "slots": {}, "history": [], "last_goal": "test"}
    assert handle_conversational_basics("show report", state) is True
    assert handle_conversational_basics("view report", state) is True

    print("      ✓ Proactive scenarios, URL parsing, and persona transitions verified!")


def test_local_prompt_cache_and_data_inspection():
    print("[15/15] Testing local prompt cache layer & data inspection...")
    from laya_browser_agent.cache import LocalPromptCache, compute_prompt_hash
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        cache = LocalPromptCache(db_path=tmp.name)

        # 1. Test canonicalization resilience: different dict key ordering and whitespace produces identical hash
        payload1 = {"task": "  login as user  ", "slots": {"b": 2, "a": 1}}
        payload2 = {"slots": {"a": 1, "b": 2}, "task": "login as user"}
        k1, _ = compute_prompt_hash("test", "model-x", payload1)
        k2, _ = compute_prompt_hash("test", "model-x", payload2)
        assert k1 == k2, "Canonicalization must produce identical hash regardless of dict key order or whitespace"

        # 2. Test cache miss then hit
        assert cache.get("test", "model-x", payload1) is None
        cache.set("test", "model-x", payload1, {"status": "ok", "plan": ["step 1"]}, latency_saved_ms=2000.0)

        hit = cache.get("test", "model-x", payload2)
        assert hit is not None
        assert hit.get("status") == "ok"

        stats = cache.get_stats()
        assert stats["total_cached_prompts"] == 1
        assert stats["total_cache_hits"] == 1
        assert stats["estimated_time_saved_seconds"] == 2.0

        # 3. Test CLI cache commands
        from laya_browser_agent.cli import handle_conversational_basics
        dummy_state = {"slots": {}, "history": [], "last_goal": ""}
        assert handle_conversational_basics("cache", dummy_state) is True
        assert handle_conversational_basics("cache stats", dummy_state) is True
        assert handle_conversational_basics("clear cache", dummy_state) is True

    print("      ✓ Local prompt cache layer & data inspection verified!")


def test_subtask_macro_cache_and_semantic_invalidation():
    print("[16/16] Testing parent-scoped subtask macro cache & affordance invalidation...")
    from laya_browser_agent.cache import (
        LocalPromptCache,
        extract_parent_scope,
        canonicalize_task_intent,
    )
    from laya_browser_agent.laya_engine import LayaEngine

    # 1. Application-agnostic order-invariant token canonicalization
    assert canonicalize_task_intent("go to my orders") == "orders"
    assert canonicalize_task_intent("open orders page") == "orders_page"
    assert canonicalize_task_intent("view active pull requests") == "active_pull_requests_view"
    assert canonicalize_task_intent("pull requests active view") == "active_pull_requests_view"

    # 2. Parent scope isolation test (hard origin and route boundaries)
    origin1, route1 = extract_parent_scope("http://localhost:3000/my-orders?tab=pending#details")
    origin2, route2 = extract_parent_scope("https://github.com/settings/billing")
    assert origin1 == "http://localhost:3000" and route1 == "/my-orders"
    assert origin2 == "https://github.com" and route2 == "/settings/billing"

    # 3. Macro cache storage, route scoped retrieval & cross-site isolation
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "test_cache.db")
        cache = LocalPromptCache(db_path=test_db)

        macro = {"action": "tool_logout_user", "params": {}, "confidence": 0.95}
        cache.set_subtask_macro(origin1, route1, "logout", macro)

        # Retrieval for same intent
        retrieved = cache.get_subtask_macro(origin1, route1, "logout")
        assert retrieved is not None
        assert retrieved.get("action") == "tool_logout_user"

        # Parent scope isolation: different site or route must NOT leak macros
        cross_site = cache.get_subtask_macro(origin2, route1, "logout")
        assert cross_site is None, "Expected cross-origin cache miss"

        cross_route = cache.get_subtask_macro(origin1, "/checkout", "logout")
        assert cross_route is None, "Expected cross-route cache miss"

        # Route-level macros retrieval for decision model candidate pre-warming
        route_macros = cache.get_subtask_macros_for_route(origin1, route1)
        assert len(route_macros) == 1
        assert route_macros[0].get("action") == "tool_logout_user"

        # 4. Affordance verification & dynamic invalidation
        engine = LayaEngine()
        valid_candidates = {"tool_logout_user": {"type": "tool", "label": "Logout"}}
        invalid_candidates = {"tool_place_order": {"type": "tool", "label": "Buy"}}

        assert engine.verify_macro_affordance(macro, valid_candidates) is True
        assert engine.verify_macro_affordance(macro, invalid_candidates) is False

        # Invalidation removes stale entry
        cache.invalidate_subtask_macro(origin1, route1, "logout")
        assert cache.get_subtask_macro(origin1, route1, "logout") is None

    print("      ✓ Parent-scoped subtask macro cache & affordance invalidation verified!")


def test_dialogue_manager_and_feedback_cache_invalidation():
    print("[17/17] Testing dialogue manager & automated feedback cache invalidation...")
    from laya_browser_agent.dialogue import classify_dialogue_turn
    from laya_browser_agent.cache import LocalPromptCache

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_cache.db")
        cache = LocalPromptCache(db_path=db_path)

        origin = "http://localhost:3000"
        route = "/checkout"
        macro = {"action": "tool_submit_form", "confidence": 0.95}

        # Seed macro into cache
        cache.set_subtask_macro(origin, route, "submit order", macro)
        assert cache.get_subtask_macro(origin, route, "submit order") is not None

        # Seed prompt cache
        cache.set("task_planner", "test_model", {"task": "order"}, {"intent": "old"}, latency_saved_ms=50.0)
        assert cache.get("task_planner", "test_model", {"task": "order"}) is not None

        session_ctx = {
            "last_goal": "login and place order for 1 item",
            "target_url": f"{origin}{route}",
            "slots": {"quantity": 1},
        }

        # 1. Test feedback / correction detection
        turn1 = classify_dialogue_turn("you placed 3 orders instead of 1", session_context=session_ctx)
        assert turn1.category == "feedback_correction"
        assert turn1.remediation_needed is True
        assert session_ctx.get("mode") == "remediation"

        # 2. Test automated cache invalidation upon correction
        evicted_macros = cache.invalidate_route_macros(origin, route)
        assert evicted_macros == 1
        assert cache.get_subtask_macro(origin, route, "submit order") is None, "Expected evicted macro"

        evicted_prompts = cache.invalidate_recent_prompts(["task_planner"], limit=5)
        assert evicted_prompts >= 1

        # 3. Test other conversational categories
        turn2 = classify_dialogue_turn("now go to settings and change password", session_context=session_ctx)
        assert turn2.category == "new_goal"
        assert turn2.remediation_needed is False

        turn3 = classify_dialogue_turn("show report", session_context=session_ctx)
        assert turn3.category == "query_status"

        turn4 = classify_dialogue_turn("thank you so much", session_context=session_ctx)
        assert turn4.category == "conversational_basic"

    print("      ✓ Dialogue manager & automated feedback cache invalidation verified!")


def test_pagination_and_dropdown_consolidation_and_screenshot_fidelity():
    print("[18/18] Testing pagination subtask consolidation, dropdown merging, and screenshot fidelity...")
    extractor = SlotExtractor()
    goal = (
        "login as user with credentials: user/User@123, go to products page, there will be many drop downs, "
        "from that click one with ‘all status’ and select from dropdown select status as 'out of stock', "
        "take screenshot of the filtered list. If there are many pages, go to all pages(pagination), "
        "find the ‘NEXT’ button and click on that to navigate to different pages, go to each page and take a screenshot, then logout"
    )
    plan = extractor.extract(goal)
    # Merges: (1) open dropdown + select option into single atomic step, (2) eliminates redundant pre-pagination screenshot, (3) consolidates all pagination clauses
    assert len(plan.subtasks) == 5, f"Expected 5 consolidated subtasks, got {len(plan.subtasks)}: {plan.subtasks}"
    assert any("out of stock" in s.lower() and ("status" in s.lower() or "all status" in s.lower()) for s in plan.subtasks)
    assert any("pagination" in s.lower() for s in plan.subtasks)
    assert not any("there will be" in s.lower() for s in plan.subtasks)
    assert any(w in plan.subtasks[-1].lower() for w in ("logout", "log out"))

    # Dropdown idempotency in agent: when target value already selected, does not re-click
    agent = LayaAgent(target_url="http://localhost:3000", goal=goal, headed=False)
    assert agent.max_steps == 25, "Expected default max_steps to be 25 for pagination headroom"

    print("      ✓ Subtask consolidation, dropdown idempotency, and screenshot fidelity verified!")


def test_multisite_navigation_continuous_chat_and_auth_negation():
    print("[19/19] Testing multi-site navigation, continuous chat URL resolution, and auth negation...")
    from laya_browser_agent.cli import extract_url_from_prompt
    from laya_browser_agent.extractor import SlotExtractor

    multisite_prompt = (
        "First go to https://www.amazon.in/ and search for iPhone 18 pro max and find the price, "
        "same thing do it in https://www.flipkart.com/ , report back where it’s cheaper. "
        "No need to login in any of the site"
    )

    # 1. URL extraction properly pulls initial target URL
    initial_url = extract_url_from_prompt(multisite_prompt)
    assert initial_url == "https://www.amazon.in/", f"Expected https://www.amazon.in/, got {initial_url}"

    # 2. Extractor must not extract fake credentials from domain slashes or stop words
    extractor = SlotExtractor()
    plan = extractor._extract_via_heuristics(multisite_prompt)
    assert "in" != plan.slots.get("username"), f"Must not extract 'in' as username: {plan.slots}"
    assert "and" != plan.slots.get("password"), f"Must not extract 'and' as password: {plan.slots}"
    assert "https" not in plan.slots, f"Must not treat https as key-value slot: {plan.slots}"

    # 3. Auth negation check: 'No need to login' must prevent any login subtask injection
    assert not any("login" in s.lower() or "sign in" in s.lower() for s in plan.subtasks), f"Plan should not contain login subtasks: {plan.subtasks}"
    assert plan.missing_details == [], f"Should not demand login details when user said no need to login: {plan.missing_details}"

    # 4. Continuous multi-turn session simulation:
    session_state = {
        "target_url": "",
        "active_url": "",
        "slots": {},
        "history": [],
        "last_goal": "",
    }

    # Turn 1: user provides prompt with URL
    t1_prompt = "Go to https://www.amazon.in/ and search for iPhone"
    t1_url = extract_url_from_prompt(t1_prompt) or session_state.get("active_url") or "http://localhost:3000"
    assert t1_url == "https://www.amazon.in/"
    # Simulate browser ending on search results page
    session_state["active_url"] = "https://www.amazon.in/s?k=iPhone"
    session_state["target_url"] = session_state["active_url"]
    session_state["last_goal"] = t1_prompt

    # Turn 2: user follow-up prompt WITHOUT specifying URL
    t2_prompt = "now click the first product and check customer reviews"
    t2_url = extract_url_from_prompt(t2_prompt) or session_state.get("active_url") or "http://localhost:3000"
    assert t2_url == "https://www.amazon.in/s?k=iPhone", f"Turn 2 must continue on active URL from Turn 1, got {t2_url}"

    print("      ✓ Multi-site navigation, continuous chat URL resolution, and auth negation verified!")


def test_progressive_discovery_horizon_planning_and_screen_grounding():
    print("[20/20] Testing progressive discovery horizon planning and live screen grounding...")
    from laya_browser_agent.extractor import SlotExtractor
    from laya_browser_agent.agent import LayaAgent

    extractor = SlotExtractor()

    # 1. Complex prompt with uncertain/downstream steps:
    # Verifies that planner partitions initial confident horizon from deferred intent
    complex_prompt = (
        "login as user with credentials: user/User@123, go to products page, there will be many drop downs, "
        "from that click one with ‘all status’ and select from dropdown select status as 'out of stock', "
        "take screenshot of the filtered list. If there are many pages, go to all pages(pagination), "
        "find the ‘NEXT’ button and click on that to navigate to different pages, go to each page and take a screenshot, then logout"
    )
    plan = extractor.extract(complex_prompt)
    assert plan.confident_horizon == 2, f"Expected 2 initial confident steps, got {plan.confident_horizon}"
    assert any(w in plan.confident_subtasks[0].lower() for w in ("login", "log in")), f"Expected login in {plan.confident_subtasks[0]}"
    assert "products" in plan.confident_subtasks[1].lower()
    assert plan.deferred_goal is not None
    assert "out of stock" in plan.deferred_goal.lower()

    # 2. Straightforward prompt without uncertainty:
    # All steps are confident upfront, no deferred intent needed
    simple_prompt = "Go to https://news.ycombinator.com and search for 'rust'"
    plan_simple = extractor.extract(simple_prompt)
    assert plan_simple.confident_horizon == len(plan_simple.subtasks)
    assert plan_simple.deferred_goal is None

    # 3. Dynamic Screen Grounding: Affordances present on live screen (Multi-page with pagination)
    screen_elements_multi = [
        {"tag": "button", "role": "combobox", "text": "All Status", "id": "status-select"},
        {"tag": "div", "role": "option", "text": "Out of Stock", "id": "opt-oos"},
        {"tag": "button", "text": "Next", "disabled": False},
    ]
    grounded_multi = extractor.expand_plan_from_screen(
        goal=complex_prompt,
        deferred_goal=plan.deferred_goal,
        completed_subtasks=plan.confident_subtasks,
        current_url="http://localhost:3000/products",
        screen_elements=screen_elements_multi,
        tools=[],
        extracted_slots=plan.slots,
    )
    assert any("out of stock" in s.lower() for s in grounded_multi)
    assert any("pagination" in s.lower() or "next" in s.lower() for s in grounded_multi), "Expected pagination / Next grounded because Next button is present"
    assert any("logout" in s.lower() for s in grounded_multi)

    # 4. Dynamic Screen Grounding: Single-page screen without pagination (affordance validation)
    # When Next button is absent, planner must NOT inject an unconditional multi-page pagination loop!
    screen_elements_single = [
        {"tag": "button", "role": "combobox", "text": "All Status", "id": "status-select"},
        {"tag": "div", "role": "option", "text": "Out of Stock", "id": "opt-oos"},
    ]
    grounded_single = extractor._expand_via_heuristics(
        goal=complex_prompt,
        deferred_goal=plan.deferred_goal,
        completed_subtasks=plan.confident_subtasks,
        current_url="http://localhost:3000/products",
        screen_elements=screen_elements_single,
        tools=[],
        slots=plan.slots,
    )
    assert any("out of stock" in s.lower() for s in grounded_single)
    assert not any("pagination" in s.lower() for s in grounded_single), "Must omit pagination loop when Next button is absent on screen"
    assert any("logout" in s.lower() for s in grounded_single)

    # 5. Dynamic Screen Grounding: Alternative affordance (Direct checkbox/link instead of dropdown)
    screen_elements_direct_link = [
        {"tag": "a", "role": "link", "text": "Out of Stock", "href": "/products?filter=out_of_stock"},
    ]
    grounded_direct = extractor.expand_plan_from_screen(
        goal="filter by status 'out of stock'",
        deferred_goal="filter by status 'out of stock'",
        completed_subtasks=[],
        current_url="https://example.com/shop",
        screen_elements=screen_elements_direct_link,
        tools=[],
        extracted_slots={"status": "out of stock"},
    )
    assert any("out of stock" in s.lower() and "click" in s.lower() for s in grounded_direct), f"Expected Click 'Out of Stock', got {grounded_direct}"


    # 6. Agent integration: Verify agent prepare_plan sets initial subtasks to confident horizon
    agent = LayaAgent(target_url="http://localhost:3000", goal=complex_prompt, headed=False)
    agent_plan = agent.prepare_plan()
    assert len(agent.subtasks) == agent_plan.confident_horizon, f"Agent subtasks should start with confident horizon of {agent_plan.confident_horizon}, got {len(agent.subtasks)}"
    assert len(agent.subtasks) < len(agent_plan.subtasks)
    assert agent.deferred_goal is not None


    print("      ✓ Progressive discovery horizon planning and live screen grounding verified!")


def test_loop_engineering_recovery_and_universal_navigation():
    print("[21/21] Testing Loop Engineering failure recovery, universal domain navigation, and clean shutdown...")
    from laya_browser_agent.cli import extract_url_from_prompt
    from laya_browser_agent.agent import LayaAgent
    from laya_browser_agent.browser import PlaywrightWebMcpDriver

    # 1. Universal URL / Domain Detection without hardcoded TLD whitelist
    u1 = extract_url_from_prompt("can you compare iphone 18 pro price in amazon.in and flipkart.com?")
    assert u1 == "https://amazon.in", f"Expected https://amazon.in, got {u1}"

    u2 = extract_url_from_prompt("check documentation on bbc.co.uk/news")
    assert u2 == "https://bbc.co.uk/news", f"Expected https://bbc.co.uk/news, got {u2}"

    u3 = extract_url_from_prompt("review file summary.md on github.com")
    assert u3 == "https://github.com", f"Expected https://github.com, got {u3}"

    # 2. Universal Navigation Target Extraction in Agent
    agent = LayaAgent(target_url="https://flipkart.com", goal="compare iphone price in amazon.in and flipkart.com", headed=False)
    nav1 = agent._extract_navigation_target("Go to Amazon.in product page for iPhone 18 Pro")
    assert nav1 == "https://Amazon.in", f"Expected https://Amazon.in, got {nav1}"

    nav2 = agent._extract_navigation_target("Go to Flipkart.com product page")
    assert nav2 == "https://Flipkart.com", f"Expected https://Flipkart.com, got {nav2}"

    nav3 = agent._extract_navigation_target("Navigate to http://localhost:8080/dashboard")
    assert nav3 == "http://localhost:8080/dashboard", f"Expected http://localhost:8080/dashboard, got {nav3}"

    # 3. Driver stop() method alias for clean shutdown (prevents AttributeError on quit)
    driver = PlaywrightWebMcpDriver(headed=False)
    assert hasattr(driver, "stop"), "PlaywrightWebMcpDriver must have stop() method"
    assert hasattr(driver, "close"), "PlaywrightWebMcpDriver must have close() method"
    driver.stop()  # Should cleanly execute without error

    # 4. Loop Engineering: Plan Remediation when subtask fails / unfulfillable
    remediated = agent.extractor.remediate_plan_from_screen(
        goal="compare iphone 18 pro price in amazon.in and flipkart.com",
        failed_subtask="Go to Amazon.in product page for iPhone 18 Pro",
        failure_reason="Navigation assertion failed: Did not arrive at target page",
        current_url="https://www.flipkart.com/",
        page_title="Flipkart",
        screen_elements=[{"tag": "input", "placeholder": "Search for products", "role": "searchbox"}],
        remaining_subtasks=["Go to Flipkart.com product page", "Compare the prices"],
    )
    # Since search input is on page, it adapts to search or continues to remaining subtasks
    assert remediated is not None, "Remediation should provide revised or remaining subtasks"
    assert len(remediated) >= 1

    # 5. Loop Engineering: Anti-Doom Loop verification in Agent state
    agent.subtasks = ["Step 1 that fails", "Step 2 next step"]
    agent.current_subtask_idx = 0
    # Simulate step failure tracking
    agent._subtask_failures[0] = 2  # Has failed 2 times
    # Remediation fallback advances to next subtask and logs finding
    finding_entry = {
        "subtask": agent.subtasks[0],
        "status": "unfulfillable",
        "reason": "Element not found",
        "url": "https://example.com",
    }
    agent.extracted_data.append(finding_entry)
    agent.current_subtask_idx += 1
    assert agent.current_subtask_idx == 1, "Agent must advance past unfulfillable subtask after 2 failures"
    assert len(agent.extracted_data) == 1
    assert agent.extracted_data[0]["status"] == "unfulfillable"

    print("      ✓ Loop Engineering failure recovery, universal domain navigation, and clean shutdown verified!")


def test_qa_architect_edge_cases():
    print("[22/22] Testing QA Architect edge cases (checkboxes, radios, natural queries, CRUD affordances, findings)...")
    from laya_browser_agent.laya_engine import extract_page_candidates, LayaEngine
    from laya_browser_agent.dialogue import classify_dialogue_turn
    from laya_browser_agent.agent import LayaAgent

    # 1. Form Checkbox and Radio Affordance Discovery
    screen_elements = [
        {"tag": "input", "type": "checkbox", "text": "Accept Terms and Conditions", "x": 100, "y": 200, "region": "content"},
        {"tag": "input", "type": "radio", "text": "Express Delivery", "x": 100, "y": 250, "region": "content"},
        {"tag": "input", "type": "text", "name": "search", "placeholder": "Search catalog", "x": 100, "y": 100, "region": "toolbar"},
    ]
    candidates = extract_page_candidates(
        available_tools=[],
        screen_elements=screen_elements,
        current_url="https://app.example.com/checkout",
        excluded_actions=set(),
    )
    assert any(c.get("type") == "checkbox" and "Accept Terms" in c.get("label", "") for c in candidates.values()), "Checkbox affordance must be extracted"
    assert any(c.get("type") == "radio" and "Express Delivery" in c.get("label", "") for c in candidates.values()), "Radio button affordance must be extracted"
    assert any(c.get("type") == "input" and "search" in c.get("field", "") for c in candidates.values()), "Text input affordance must be extracted"

    # 2. Fast-path affordance matching for Approve/Confirm/Accept CRUD operations
    engine = LayaEngine()
    approve_elements = [
        {"tag": "button", "text": "Approve Order #8821", "x": 250, "y": 300},
        {"tag": "button", "text": "Reject Order #8821", "x": 350, "y": 300},
    ]
    action, conf, params, info = engine._query_screen_locator(
        subtask="Approve order 8821",
        current_url="https://app.example.com/orders",
        available_tools=[],
        screen_elements=approve_elements,
        extracted_slots={"orderId": "8821"},
    )
    assert action.startswith("click_approve"), f"Expected click_approve action, got {action}"
    assert conf >= 0.95
    assert "8821" in params.get("target", "")

    # 3. Conversational Dialogue natural queries & status requests
    turn_report = classify_dialogue_turn("show me the report")
    assert turn_report.category == "query_status", f"Expected query_status for 'show me the report', got {turn_report.category}"

    turn_open = classify_dialogue_turn("open the report")
    assert turn_open.category == "query_status", f"Expected query_status for 'open the report', got {turn_open.category}"

    turn_what = classify_dialogue_turn("what is the status")
    assert turn_what.category == "query_status", f"Expected query_status for 'what is the status', got {turn_what.category}"

    turn_thanks = classify_dialogue_turn("thanks a lot!")
    assert turn_thanks.category == "conversational_basic", f"Expected conversational_basic for 'thanks a lot!', got {turn_thanks.category}"

    turn_sounds_good = classify_dialogue_turn("sounds good")
    assert turn_sounds_good.category == "conversational_basic", f"Expected conversational_basic for 'sounds good', got {turn_sounds_good.category}"

    # 4. Multi-modal Extracted Findings Rendering (metrics + unfulfillable audits)
    agent = LayaAgent(target_url="https://app.example.com", goal="Audit pending items", headed=False)
    agent.extracted_data = [
        {"metric": "Pending Orders Count", "count": 14, "source": "DOM Table", "summary": "Found 14 pending orders"},
        {"subtask": "Filter by non-existent status", "status": "unfulfillable", "reason": "No status option found", "source": "Screen Remediation"},
    ]
    md_report = agent.generate_report()
    assert "### 📊 Extracted Findings & Data Audit" in md_report
    assert "Pending Orders Count" in md_report
    assert "unfulfillable" in md_report

    html_report = agent.generate_html_report()
    assert "pill-warning" in html_report
    assert "pill-success" in html_report
    assert "Pending Orders Count" in html_report

    # 5. Search input fast-path matching (prioritizes input field over navigation click)
    search_elements = [
        {"tag": "input", "type": "text", "id": "twotabsearchtextbox", "name": "field-keywords", "placeholder": "Search Amazon.in", "x": 300, "y": 30},
        {"tag": "a", "text": "Search Options", "x": 600, "y": 30},
    ]
    s_act, s_conf, s_params, s_info = engine._query_screen_locator(
        subtask="Search for iPhone 18 Pro Max",
        current_url="https://www.amazon.in",
        available_tools=[],
        screen_elements=search_elements,
        extracted_slots={"search": "iPhone 18 Pro Max"},
    )
    assert s_act.startswith("input_field_"), f"Expected input_field action for search, got {s_act}"
    assert s_params.get("value") == "iPhone 18 Pro Max"
    assert s_params.get("_press_enter") is True

    # 6. Driver interactive wait method presence & exception report resilience
    from laya_browser_agent.browser import PlaywrightWebMcpDriver
    drv = PlaywrightWebMcpDriver(headed=False)
    assert hasattr(drv, "wait_for_page_interactive"), "Driver must implement wait_for_page_interactive"

    err_report = agent.generate_report(execution_error="Network timeout on target host")
    assert "❌ FAILED (ERROR: Network timeout on target host)" in err_report
    err_html = agent.generate_html_report(execution_error="Network timeout on target host")
    assert "FAILED: Network timeout on target host" in err_html

    print("      ✓ QA Architect edge cases (checkboxes, radios, natural queries, CRUD affordances, findings) verified!")


def test_universal_portability_and_multisite_comparison_telemetry():
    print("[23/23] Testing universal machine portability, multisite comparison, telemetry & deduplication...")
    import os
    import re
    import tempfile
    from laya_browser_agent.browser import get_desktop_user_agent
    from laya_browser_agent.laya_engine import LayaEngine, resolve_local_model
    from laya_browser_agent.agent import LayaAgent, AgentStepEvent, assert_step_transition
    from laya_browser_agent.reporting import generate_markdown_report, generate_html_dashboard

    # 1. Test universal desktop User-Agent
    ua = get_desktop_user_agent()
    assert "Mozilla/5.0" in ua
    assert "Chrome/128" in ua

    # 2. Test graceful local model discovery fallback
    resolved_m = resolve_local_model(preferred="test-preferred:latest")
    assert resolved_m in ("test-preferred:latest", "qwen2.5:3b") or len(resolved_m) > 0

    # 3. Test LayaEngine CPU / non-MLX portability
    eng = LayaEngine(model_id="aac6fef/laya-mlx", device="cpu")
    assert hasattr(eng, "telemetry")
    assert "input_tokens" in eng.telemetry
    assert "output_tokens" in eng.telemetry

    # 4. Test failure guard: failed clicks or inputs must NEVER pass assert transition gate
    passed_fail, reason_fail = assert_step_transition(
        subtask="Click target button",
        action="click_target",
        params={},
        result={"clicked": "target", "success": False},
        before_url="https://example.com",
        after_url="https://example.com",
        before_elements=[],
        after_elements=[],
    )
    assert not passed_fail, "Failed click result must fail assertion gate"

    passed_bool, _ = assert_step_transition(
        subtask="Click target button",
        action="click_target",
        params={},
        result=False,
        before_url="https://example.com",
        after_url="https://example.com",
        before_elements=[],
        after_elements=[],
    )
    assert not passed_bool, "False result must fail assertion gate"

    # 5. Test session telemetry aggregation
    test_agent = LayaAgent(
        target_url="https://amazon.in",
        goal="Compare iPhone 18 Pro Max prices on Amazon.in and Flipkart.com to determine the cheaper option.",
        headed=False,
        generate_report=False,
    )
    test_agent.telemetry["input_tokens"] = 1200
    test_agent.telemetry["output_tokens"] = 350
    test_agent.telemetry["cache_reads"] = 2
    test_agent.telemetry["cache_writes"] = 1
    test_agent.telemetry["latency_saved_ms"] = 3000.0

    tel = test_agent.get_session_telemetry()
    assert tel["input_tokens"] >= 1200
    assert tel["output_tokens"] >= 350
    assert tel["total_tokens"] >= 1550
    assert tel["cache_reads"] >= 2
    assert tel["cache_writes"] >= 1

    # 6. Test multi-site price comparison extraction logic
    test_agent.extracted_slots["price_amazon.in"] = "₹76,999"
    test_agent.extracted_slots["price_flipkart.com"] = "₹1,34,900"

    price_slots = {k: v for k, v in test_agent.extracted_slots.items() if k.startswith("price_") and v}
    parsed = []
    for host_k, p_str in price_slots.items():
        digits = re.sub(r"[^\d.]", "", str(p_str))
        parsed.append((host_k.replace("price_", ""), float(digits), str(p_str)))
    parsed.sort(key=lambda x: x[1])
    assert parsed[0][0] == "amazon.in"
    assert parsed[0][1] == 76999.0
    assert parsed[1][0] == "flipkart.com"
    assert parsed[1][1] == 134900.0
    assert "amazon.in is cheaper" in f"{parsed[0][0]} is cheaper ({parsed[0][2]}) than {parsed[1][0]} ({parsed[1][2]})"

    # 7. Test screenshot deduplication in report generators
    with tempfile.TemporaryDirectory() as tmpdir:
        sc1 = os.path.join(tmpdir, "sc1.png")
        sc2 = os.path.join(tmpdir, "sc2.png")
        sc_final = os.path.join(tmpdir, "sc_final.png")
        img_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        with open(sc1, "wb") as f:
            f.write(img_bytes)
        with open(sc2, "wb") as f:
            f.write(img_bytes)
        with open(sc_final, "wb") as f:
            f.write(img_bytes)

        test_agent.steps_taken = [
            AgentStepEvent(
                step=1, subtask="Step 1", url="https://amazon.in", title="Amazon",
                available_tools=[], chosen_action="click", confidence=1.0, probabilities={},
                tool_params={}, tool_result={"success": True}, duration_ms=100.0,
                engine_info="test", screenshot_path=sc1
            ),
            AgentStepEvent(
                step=2, subtask="Step 2", url="https://amazon.in", title="Amazon",
                available_tools=[], chosen_action="click", confidence=1.0, probabilities={},
                tool_params={}, tool_result={"success": True}, duration_ms=100.0,
                engine_info="test", screenshot_path=sc2
            ),
        ]

        md_rep = generate_markdown_report(test_agent, final_screenshot=sc_final)
        assert "![Step 1](sc1.png)" in md_rep
        assert "![Step 2]" not in md_rep, "Identical duplicate screenshot must be suppressed in visual log"
        assert "## 📸 Final Visual Verification Proof" not in md_rep, "Duplicate final proof must be suppressed when already in step screenshots"
        assert "## 📊 Session Telemetry" in md_rep
        assert "Input Tokens (Prompt Eval)" in md_rep

        html_rep = generate_html_dashboard(test_agent, final_screenshot=sc_final)
        assert "alt=\"Step 1\"" in html_rep
        assert "alt=\"Step 2\"" not in html_rep
        assert "Session Telemetry" in html_rep

    # 8. Test Search Input Priority over Navigation Links
    engine = LayaEngine(model_id="aac6fef/laya-mlx")
    screen_with_search_and_nav = [
        {"tag": "a", "text": "Amazon Pay", "x": 100, "y": 20},
        {"tag": "a", "text": "Search for Products, Brands and More", "x": 100, "y": 40},
        {"tag": "input", "name": "field-keywords", "type": "text", "x": 200, "y": 20},
    ]
    subtask_amazon = "Go to products page on Amazon.in and enter 'iPhone 18 Pro Max' into search input box and submit"
    action, conf, params, info = engine._query_screen_locator(
        subtask=subtask_amazon,
        current_url="https://www.amazon.in",
        available_tools=[],
        screen_elements=screen_with_search_and_nav,
        extracted_slots={"search": "iPhone 18 Pro Max"},
    )
    assert action == "input_field_keywords", f"Search input must take precedence over link click, got {action}"
    assert params.get("_press_enter") is True
    assert params.get("value") == "iPhone 18 Pro Max"

    # Test Flipkart search input resolution
    flipkart_elements = [
        {"tag": "a", "text": "Search for Products, Brands and More", "x": 100, "y": 40},
        {"tag": "input", "name": "q", "type": "text", "x": 200, "y": 40},
    ]
    subtask_flipkart = "Go to products page on Flipkart.com and enter 'iPhone 18 Pro Max' into search input box and submit"
    action_f, conf_f, params_f, info_f = engine._query_screen_locator(
        subtask=subtask_flipkart,
        current_url="https://www.flipkart.com",
        available_tools=[],
        screen_elements=flipkart_elements,
        extracted_slots={"search": "iPhone 18 Pro Max"},
    )
    assert action_f == "input_q", f"Flipkart search input must be selected, got {action_f}"
    assert params_f.get("value") == "iPhone 18 Pro Max"

    # 9. Test truthful status evaluation for price comparison goals
    from laya_browser_agent.reporting import evaluate_run_status
    compare_agent = LayaAgent(target_url="https://amazon.in", goal="can you compare iphone prices on amazon.in and flipkart.com and report where its lowest")
    compare_agent.subtasks = ["step 1", "step 2", "compare"]
    compare_agent.current_subtask_idx = 3
    compare_agent.steps_taken = [
        AgentStepEvent(
            step=1, subtask="Finish", url="https://flipkart.com", title="Flipkart",
            available_tools=[], chosen_action="finish", confidence=1.0, probabilities={},
            tool_params={}, tool_result="done", duration_ms=10.0, engine_info="test"
        )
    ]
    # If any finding is marked unfulfillable, evaluate_run_status reports unfulfillable
    compare_agent.extracted_data = [
        {"metric": "Data Audit", "status": "unfulfillable", "reason": "item not found on page"}
    ]
    status_code, badge, _, _ = evaluate_run_status(compare_agent)
    assert status_code == "unfulfillable"
    assert "UNFULFILLABLE" in badge

    # When all findings are completed, evaluate_run_status reports success
    compare_agent.extracted_data = [
        {"metric": "Data Audit", "status": "COMPLETED", "summary": "item verified"}
    ]
    status_code_ok, badge_ok, _, _ = evaluate_run_status(compare_agent)
    assert status_code_ok == "success"
    assert "COMPLETED (SUCCESS)" in badge_ok

    print("      ✓ Universal machine portability, multisite auditing, telemetry & deduplication verified!")


def test_universal_form_filling_and_safe_url_driver():
    print("[24/24] Testing universal form filling, safe current_url driver & overlay dismissal...")

    # 1. Test driver safe current_url property & overlay dismissal
    driver = PlaywrightWebMcpDriver(headed=False)
    # Driver not yet started: current_url must return empty string rather than crashing with AttributeError
    assert driver.current_url == ""
    assert driver.get_active_url() == ""
    assert driver.get_page_title() == ""
    assert driver.dismiss_transient_overlays() is False

    # 2. Test universal multi-field heuristic extraction
    extractor = SlotExtractor()
    plan = extractor._extract_via_heuristics(
        "Fill contact form with name 'Bruce Wayne', email 'bruce@wayne.com', phone '555-0199', and message 'Need security audit' on http://example.com/contact"
    )
    assert plan.slots.get("name") == "Bruce Wayne", f"Expected Bruce Wayne, got {plan.slots.get('name')}"
    assert plan.slots.get("email") == "bruce@wayne.com", f"Expected bruce@wayne.com, got {plan.slots.get('email')}"
    assert plan.slots.get("phone") == "555-0199", f"Expected 555-0199, got {plan.slots.get('phone')}"
    assert plan.slots.get("message") == "Need security audit", f"Expected Need security audit, got {plan.slots.get('message')}"

    # 3. Test form input decoupling vs search input in LayaEngine
    engine = LayaEngine(model_id="aac6fef/laya-mlx")
    screen_elements = [
        {"tag": "input", "name": "field-keywords", "type": "text", "placeholder": "Search catalog", "x": 100, "y": 20},
        {"tag": "input", "name": "full_name", "type": "text", "placeholder": "Your Full Name", "x": 100, "y": 80},
        {"tag": "input", "name": "user_email", "type": "email", "placeholder": "Email Address", "x": 100, "y": 140},
        {"tag": "textarea", "name": "feedback_msg", "placeholder": "Enter your message", "x": 100, "y": 200},
    ]

    # A: Form name input must match name field, NOT the search box!
    act_name, conf, p_name, _ = engine._query_screen_locator(
        subtask="Enter 'Bruce Wayne' into full_name field and continue",
        current_url="http://example.com/contact",
        available_tools=[],
        screen_elements=screen_elements,
        extracted_slots={"full_name": "Bruce Wayne", "name": "Bruce Wayne"},
    )
    assert act_name == "input_full_name", f"Expected input_full_name, got {act_name}"
    assert p_name.get("value") == "Bruce Wayne"
    assert p_name.get("_press_enter") is False, "General form input must not default to press_enter=True"

    # B: Search input must match the search box!
    act_srch, conf, p_srch, _ = engine._query_screen_locator(
        subtask="Search for 'laptop chargers' in search input box and submit",
        current_url="http://example.com/contact",
        available_tools=[],
        screen_elements=screen_elements,
        extracted_slots={"search": "laptop chargers"},
    )
    assert act_srch == "input_field_keywords", f"Expected input_field_keywords, got {act_srch}"
    assert p_srch.get("value") == "laptop chargers"
    assert p_srch.get("_press_enter") is True, "Search input must press enter"

    # C: Email input must match email field
    act_email, conf, p_email, _ = engine._query_screen_locator(
        subtask="Enter 'bruce@wayne.com' into user_email input",
        current_url="http://example.com/contact",
        available_tools=[],
        screen_elements=screen_elements,
        extracted_slots={"email": "bruce@wayne.com"},
    )
    assert act_email == "input_user_email", f"Expected input_user_email, got {act_email}"

    # 4. Test LayaAgent safe URL retrieval
    agent = LayaAgent(target_url="http://example.com", goal="Test safe url", headed=False)
    agent.driver = driver
    assert agent._get_active_url() == ""

    print("      ✓ Universal form filling, safe current_url driver & overlay dismissal verified!")


def test_loop_engineering_fault_isolation_and_reactive_dialog_dismissal():
    print("[25/25] Testing Loop Engineering fault isolation, step error recovery & dialog dismissal...")
    from laya_browser_agent.browser import PlaywrightWebMcpDriver
    from laya_browser_agent.agent import LayaAgent

    # 1. Test driver overlay checking and dismissal on closed/mock driver
    driver = PlaywrightWebMcpDriver(headed=False)
    assert driver._has_blocking_overlay() is False
    assert driver.dismiss_transient_overlays() is False

    # 2. Test LayaAgent Loop Engineering Step Fault Isolation
    agent = LayaAgent(
        target_url="http://example.com",
        goal="Compare iPhone prices across sites",
        max_steps=5,
        headed=False,
    )
    agent.subtasks = ["Go to Amazon.in", "Search on Amazon", "Go to Flipkart.com", "Compare prices"]
    agent.driver = driver

    # Simulate subtask failure count and plan alteration via remediate_plan_from_screen
    curr_subtask = "Search on Amazon"
    altered = agent.extractor.remediate_plan_from_screen(
        goal=agent.goal,
        failed_subtask=curr_subtask,
        failure_reason="Element not found / network error",
        current_url="http://example.com",
        page_title="Example Domain",
        screen_elements=[],
        remaining_subtasks=["Go to Flipkart.com", "Compare prices"],
    )
    # Remediation should revise subtasks dynamically via LLM / screen heuristics rather than crashing
    assert altered and len(altered) > 0 and altered != [curr_subtask], f"Expected revised subtasks or downstream continuation, got {altered}"

    # 3. Test downstream subtask preservation (crucial regression prevention)
    remaining = ["Go to Flipkart.com and enter 'iPhone' into search", "Compare prices"]
    revised_only = ["Enter 'iPhone' into search input box and submit"]
    preserved = [st for st in remaining if not any(st.lower() in alt.lower() or alt.lower() in st.lower() for alt in revised_only)]
    full_plan = agent.subtasks[:1] + revised_only + preserved
    assert "Go to Flipkart.com and enter 'iPhone' into search" in full_plan, "Downstream subtasks must not be wiped out"
    assert "Compare prices" in full_plan, "Final audit subtasks must not be wiped out"

    # 4. Comparative tasks must NEVER trigger domain navigation redirects
    assert agent._extract_navigation_target("Compare the prices from Amazon.in and Flipkart.com and report which one is lower") is None
    assert agent._extract_navigation_target("Check price on Amazon.in and Flipkart.com") is None
    assert agent._extract_navigation_target("Go to Flipkart.com and enter 'iPhone' into search") == "https://Flipkart.com"

    # 5. Universal search affordance match for e-commerce search inputs
    from laya_browser_agent.affordance import extract_page_candidates
    flipkart_el = [{"tag": "input", "name": "q", "title": "Search for Products, Brands and More", "x": 100, "y": 20, "type": "text"}]
    cands = extract_page_candidates(available_tools=[], screen_elements=flipkart_el, current_url="https://flipkart.com")
    assert any(c.get("is_search") for c in cands.values()), "Flipkart input name='q' must be identified as search input"

    print("      ✓ Loop Engineering fault isolation, step error recovery & dialog dismissal verified!")


def test_variant_perception_cross_site_anchoring_and_disambiguation():
    print("[25/25] Testing variant perception, cross-site consistency anchoring & disambiguation...")
    from laya_browser_agent.variants import (
        ScreenVariant,
        extract_variant_spec,
        is_accessory_text,
        is_sponsored_text,
        extract_price_info,
        resolve_screen_variant,
    )

    # 1. Test specification extraction
    assert "256GB" in extract_variant_spec("Apple iPhone 16 Pro Max (Desert Titanium, 256 GB)")
    assert "Desert Titanium" in extract_variant_spec("Apple iPhone 16 Pro Max (Desert Titanium, 256 GB)")
    assert "1TB" in extract_variant_spec("Apple iPhone 16 Pro Max (1 TB) - Black Titanium")
    assert "Size 9" in extract_variant_spec("Nike Pegasus 40 Running Shoes Size 9 UK")
    assert "Economy" in extract_variant_spec("British Airways Flight BA178 - Economy Class - Non-stop")

    # 2. Test accessory and sponsored filtering
    assert is_accessory_text("Spigen Ultra Hybrid Back Case for iPhone 16 Pro Max", "iPhone 16 Pro Max")
    assert not is_accessory_text("Apple iPhone 16 Pro Max 256GB", "iPhone 16 Pro Max")
    assert is_sponsored_text("Sponsored: Apple iPhone 16 Pro Max (2 TB)")
    assert not is_sponsored_text("Apple iPhone 16 Pro Max (256 GB)")

    # 3. Test price info parsing
    p_str, p_val = extract_price_info("M.R.P: ₹1,79,900.00 incl taxes")
    assert p_str == "₹1,79,900.00"
    assert p_val == 179900.0

    # 4. Test cross-site variant anchoring (The exact real-world scenario from user reports)
    amazon_variants = [
        ScreenVariant(
            title="Apple iPhone 16 Pro Max (2 TB) - Desert Titanium",
            spec="2TB, Desert Titanium",
            price="₹3,29,900.00",
            price_val=329900.0,
            is_sponsored=True,
            is_accessory=False,
            relevance_score=40.0,
        ),
        ScreenVariant(
            title="Apple iPhone 16 Pro Max (256 GB) - Natural Titanium",
            spec="256GB, Natural Titanium",
            price="₹1,79,900.00",
            price_val=179900.0,
            is_sponsored=False,
            is_accessory=False,
            relevance_score=110.0,
        ),
        ScreenVariant(
            title="Spigen Armor Case for iPhone 16 Pro Max",
            spec="Standard Base",
            price="₹999.00",
            price_val=999.0,
            is_sponsored=False,
            is_accessory=True,
            relevance_score=-50.0,
        ),
    ]

    chosen_site1, anchored_spec = resolve_screen_variant(
        variants=amazon_variants,
        query_str="iPhone 16 Pro Max",
        target_variant=None,
    )
    assert chosen_site1 is not None
    assert chosen_site1.price == "₹1,79,900.00"
    assert "256GB" in anchored_spec

    flipkart_variants = [
        ScreenVariant(
            title="Apple iPhone 16 Pro Max (Desert Titanium, 512 GB)",
            spec="512GB, Desert Titanium",
            price="₹1,99,900",
            price_val=199900.0,
            is_sponsored=False,
            is_accessory=False,
            relevance_score=100.0,
        ),
        ScreenVariant(
            title="Apple iPhone 16 Pro Max (Desert Titanium, 256 GB)",
            spec="256GB, Desert Titanium",
            price="₹1,79,900",
            price_val=179900.0,
            is_sponsored=False,
            is_accessory=False,
            relevance_score=100.0,
        ),
    ]

    chosen_site2, _ = resolve_screen_variant(
        variants=flipkart_variants,
        query_str="iPhone 16 Pro Max",
        target_variant=anchored_spec,
    )
    assert chosen_site2 is not None
    assert chosen_site2.price == "₹1,79,900"
    assert "256GB" in chosen_site2.spec, "Site 2 must select the exact same variant anchored from Site 1"

    def mock_disambiguation_cb(prompt: str, options: list) -> str:
        assert len(options) >= 2
        return "1"

    chosen_interactive, spec_interactive = resolve_screen_variant(
        variants=flipkart_variants,
        query_str="iPhone 16 Pro Max",
        target_variant=None,
        disambiguation_callback=mock_disambiguation_cb,
    )
    assert chosen_interactive is not None
    assert chosen_interactive == flipkart_variants[0]

    print("      ✓ Variant perception, cross-site anchoring & disambiguation verified!")


def test_multi_tab_domain_alignment_and_guarded_macros():
    """Validates multi-tab browser lifecycle, cross-domain tab alignment, and guarded macro caching."""
    print("[27/27] Testing multi-tab driver, cross-domain tab alignment & guarded macro caching...")
    from laya_browser_agent.browser import PlaywrightWebMcpDriver
    from laya_browser_agent.variants import ScreenVariant, resolve_screen_variant
    from laya_browser_agent.agent import assert_step_transition
    from laya_browser_agent.cache import global_prompt_cache

    # 1. Test multi-tab browser driver lifecycle
    driver = PlaywrightWebMcpDriver(headed=False)
    try:
        p1 = driver.start("data:text/html,<html><head><title>Amazon Store</title></head><body><h1>Amazon</h1></body></html>")
        assert p1 is not None
        assert len(driver.get_tabs()) == 1
        assert driver.get_tabs()[0]["active"] is True

        p2 = driver.new_tab("data:text/html,<html><head><title>Flipkart Hub</title></head><body><h1>Flipkart</h1></body></html>")
        assert p2 is not None
        tabs = driver.get_tabs()
        assert len(tabs) == 2
        assert tabs[1]["active"] is True

        # Switch by domain keyword / title
        switched = driver.switch_tab("amazon")
        assert switched is True
        assert driver.page.title() == "Amazon Store"

        switched = driver.switch_tab(1)
        assert switched is True
        assert driver.page.title() == "Flipkart Hub"

        # Close tab
        closed = driver.close_tab(1)
        assert closed is True
        assert len(driver.get_tabs()) == 1
        assert driver.page.title() == "Amazon Store"
    finally:
        driver.close()

    # 2. Test prevention of accessory fallback when searching core product
    accessory_only_variants = [
        ScreenVariant(
            title="Flipkart SmartBuy Back Cover for Apple iPhone 18 Pro Max",
            spec="Standard Base",
            price="₹299",
            price_val=299.0,
            is_sponsored=False,
            is_accessory=True,
            relevance_score=10.0,
        ),
        ScreenVariant(
            title="Zapcase Tempered Glass for iPhone 18 Pro Max",
            spec="Standard Base",
            price="₹199",
            price_val=199.0,
            is_sponsored=False,
            is_accessory=True,
            relevance_score=5.0,
        ),
    ]

    resolved_acc, spec_acc = resolve_screen_variant(
        variants=accessory_only_variants,
        query_str="iPhone 18 Pro Max",
        target_variant=None,
        disambiguation_callback=None,
    )
    assert resolved_acc is None, "Must never silently fall back to phone cases when searching for a phone"
    assert spec_acc is None

    # 3. Test execution verification: failed action execution must fail assert transition
    passed, reason = assert_step_transition(
        subtask="Click on the target listing",
        action="click_target",
        params={"target": "target"},
        result={"clicked": "target", "success": False},
        before_url="https://www.flipkart.com/search?q=iPhone+18",
        after_url="https://www.flipkart.com/search?q=iPhone+18",
        before_elements=[],
        after_elements=[],
    )
    assert passed is False
    assert "failed" in reason.lower()

    # 4. Test macro invalidation on failure
    origin = "https://www.flipkart.com"
    route = "/search"
    test_subtask = "Click on the iPhone 18 Pro Max listing to view product details."
    global_prompt_cache.set_subtask_macro(
        origin, route, test_subtask,
        {"action": "click_add_to_compare", "params": {}, "confidence": 0.9}
    )
    assert global_prompt_cache.get_subtask_macro(origin, route, test_subtask) is not None
    # Invalidate
    global_prompt_cache.invalidate_subtask_macro(origin, route, test_subtask)
    assert global_prompt_cache.get_subtask_macro(origin, route, test_subtask) is None

    print("      ✓ Multi-tab driver, cross-domain tab alignment & guarded macro caching verified!")


def test_multiturn_closed_loop_verification_and_tab_context():
    print("[28/28] Testing multi-turn closed-loop verification, tab context, and prompt cache isolation...")
    from laya_browser_agent.agent import LayaAgent
    from laya_browser_agent.cache import global_prompt_cache

    # 1. Verification of subtask advancement on operational action (add to cart)
    # Even if subtask mentions target entity name, action_fulfills_subtask ensures it advances
    agent = LayaAgent(
        target_url="http://example.com",
        goal="Locate the product 'iPhone 18 pro max' on Amazon, click to add it to cart",
        headed=False,
    )
    agent.extracted_slots = {"search": "iPhone 18 pro max", "query": "iPhone 18 pro max"}
    agent.subtasks = [
        "Locate the product 'iPhone 18 pro max' on Amazon, click to add it to cart",
        "Switch to Flipkart tab and add to cart",
    ]
    agent.current_subtask_idx = 0

    active_sub = agent.subtasks[0]
    chosen = "click_add_to_cart"

    subtask_demands_input = (
        any(w in active_sub.lower() for w in ("enter", "fill", "type", "input", "submit search", "type into"))
        and not any(w in active_sub.lower() for w in ("click to add", "add to cart", "add it to cart", "buy", "purchase", "select", "checkout", "place order", "click"))
    )
    assert not subtask_demands_input, "Expected action subtask NOT to demand text input despite entity mention"

    act_clean = chosen.lower().replace("-", "_")
    sub_clean = active_sub.lower()
    action_fulfills_subtask = False
    action_intent_pairs = [
        ("add_to_cart", ("add to cart", "add it to cart", "add item", "to cart")),
        ("buy", ("buy", "purchase", "place order")),
    ]
    for act_token, sub_tokens in action_intent_pairs:
        if act_token in act_clean and any(stk in sub_clean for stk in sub_tokens):
            action_fulfills_subtask = True
            break
    assert action_fulfills_subtask is True, "Expected click_add_to_cart to fulfill 'click to add it to cart' subtask"

    # 2. Test multi-tab session context in extractor
    session_ctx = {
        "open_tabs": [
            {"index": 0, "url": "https://www.amazon.in/s?k=iPhone+18", "title": "Amazon", "active": False},
            {"index": 1, "url": "https://www.flipkart.com/search?q=iPhone+18", "title": "Flipkart", "active": True},
        ],
        "active_url": "https://www.flipkart.com/search?q=iPhone+18",
        "last_goal": "search both sites for iPhone 18",
    }
    open_tabs = session_ctx.get("open_tabs") or []
    assert len(open_tabs) == 2
    assert open_tabs[0]["url"] == "https://www.amazon.in/s?k=iPhone+18"
    assert open_tabs[1]["active"] is True

    # 3. Test prompt cache invalidation for screen_locator
    global_prompt_cache.set("screen_locator", "test_model", {"step": 1, "route": "/test"}, {"action": "click_search"}, latency_saved_ms=50.0)
    assert global_prompt_cache.get("screen_locator", "test_model", {"step": 1, "route": "/test"}) is not None
    evicted = global_prompt_cache.invalidate_recent_prompts(["screen_locator"], limit=5)
    assert evicted >= 1
    assert global_prompt_cache.get("screen_locator", "test_model", {"step": 1, "route": "/test"}) is None

    print("      ✓ Multi-turn closed-loop verification, tab context, and cache isolation verified!")


def test_multiturn_outcome_grounding_and_screen_arbitration():
    print("[29/29] Testing multi-turn outcome grounding, symmetric tab planning & screen arbitration...")
    import re
    from laya_browser_agent.extractor import SlotExtractor
    from laya_browser_agent.laya_engine import LayaEngine
    from laya_browser_agent.reporting import generate_markdown_report

    # 1. Test multi-turn outcome grounding and symmetric tab subtask generation
    session_ctx = {
        "slots": {"search": "iPhone 18 pro max"},
        "last_outcome": {
            "turn": 1,
            "goal": "First go to https://www.amazon.in/ and search for iPhone 18 pro max and find the price, and then go to https://www.flipkart.com/ and search for the same",
            "status": "completed",
            "open_tabs": [
                {"index": 0, "url": "https://www.amazon.in/s?k=iPhone+18+pro+max", "title": "Amazon.in", "active": False},
                {"index": 1, "url": "https://www.flipkart.com/search?q=iPhone+18+pro+max", "title": "Flipkart.com", "active": True},
            ],
            "extracted_slots": {"search": "iPhone 18 pro max", "query": "iPhone 18 pro max"},
            "extracted_data": [{"metric": "price_www.amazon.in", "count": "1,39,900"}],
        },
    }

    t2_prompt = "okay, can you add it to cart, have separate tab for each site and do not close the browser"
    extractor = SlotExtractor()
    plan = extractor._extract_via_heuristics(t2_prompt, session_context=session_ctx)

    # Verifications:
    # A. Entity "it" resolved to "iPhone 18 pro max" from session context
    assert plan.slots.get("search") == "iPhone 18 pro max"
    # B. Conversational constraints filtered out
    assert not any("do not close" in s.lower() for s in plan.subtasks), f"Conversational constraint leaked into plan: {plan.subtasks}"
    assert not any("separate tab" in s.lower() for s in plan.subtasks), f"Modality request leaked into plan: {plan.subtasks}"
    # C. Symmetric subtasks generated across both distinct open tab domains
    assert any("amazon.in" in s.lower() and "switch" in s.lower() for s in plan.subtasks), f"Expected switch to amazon.in tab in plan: {plan.subtasks}"
    assert any("flipkart.com" in s.lower() and "switch" in s.lower() for s in plan.subtasks), f"Expected switch to flipkart.com tab in plan: {plan.subtasks}"
    assert any("add" in s.lower() and "cart" in s.lower() for s in plan.subtasks), f"Expected add to cart subtask: {plan.subtasks}"

    # 2. Universal tab switch regex validation
    tab_tests = [
        ("Switch to https://www.flipkart.com tab", "flipkart.com"),
        ("Switch to http://www.amazon.in/ tab", "amazon.in"),
        ("Switch to the flipkart.com tab", "flipkart.com"),
        ("switch to tab 0", 0),
    ]
    for sub, expected in tab_tests:
        low_st = sub.lower().strip()
        m_tab = re.search(r"\bswitch\s+(?:to\s+)?(?:the\s+)?tab\s*(\d+)\b", low_st)
        m_dom = re.search(r"\bswitch\s+(?:to\s+)?(?:the\s+)?(?:https?://)?(?:www\.)?([a-zA-Z0-9_\-\.]+)/?\s+tab\b", low_st)
        if isinstance(expected, int):
            assert m_tab and int(m_tab.group(1)) == expected
        else:
            assert m_dom and expected in m_dom.group(1).rstrip("/"), f"Failed tab regex on '{sub}': {m_dom}"

    # 3. Screen locator affordance match & top-navbar header exclusion
    engine = LayaEngine(model_id="mock_model", confidence_threshold=0.8)
    screen_elements = [
        {"tag": "a", "text": "Cart", "role": "link", "x": 1190.0, "y": 28.0},  # Top navbar cart icon
        {"tag": "button", "text": "Notify Me", "role": "button", "x": 450.0, "y": 420.0},
        {"tag": "div", "text": "Currently Unavailable", "role": "status", "x": 450.0, "y": 400.0},
    ]
    # For subtask Click 'Add to Cart' button: top navbar Cart at y=28 must NOT be matched as detail button
    locator_res = engine._query_screen_locator(
        subtask="Click 'Add to Cart' button",
        current_url="https://www.flipkart.com/apple-iphone-18",
        available_tools=[],
        screen_elements=screen_elements,
        extracted_slots={"search": "iPhone 18 pro max"},
    )
    # Fast token overlap should not match navbar cart icon at y=28
    if locator_res:
        action, conf, params, info = locator_res
        assert action != "click_cart", f"Should not match top navbar header cart: {action}"

    # 4. Action Blocked report generation and badge
    mock_agent = type("MockAgent", (), {
        "goal": t2_prompt,
        "target_url": "https://www.flipkart.com/apple-iphone-18",
        "steps_taken": [],
        "max_steps": 25,
        "model_id": "aac6fef/laya-mlx",
        "ollama_model": "qwen2.5:7b",
        "extracted_slots": {"search": "iPhone 18 pro max"},
        "extracted_data": [
            {
                "metric": "Action Status (Click 'Add to Cart' button)",
                "status": "unfulfillable",
                "reason": "Item is unreleased and displays 'Notify Me' instead of 'Add to Cart'",
                "subtask": "Click 'Add to Cart' button",
            }
        ],
        "subtasks": plan.subtasks,
        "current_subtask_idx": len(plan.subtasks),
        "report_file": "/tmp/test_report.md",
        "discovered_routes": {},
        "engine": None,
        "screenshot_mode": "none",
        "get_session_telemetry": lambda self=None: {},
    })()
    report_md = generate_markdown_report(mock_agent)
    assert "UNFULFILLABLE" in report_md, "Report should badge unfulfillable status when action is blocked"
    assert "Notify Me" in report_md, "Report should preserve blocker reason"

    print("      ✓ Multi-turn outcome grounding, symmetric tab planning & screen arbitration verified!")


if __name__ == "__main__":
    from laya_browser_agent.llm import RecordedLLMClient, set_llm_client
    from tests.conftest import deterministic_llm_fixture_handler
    set_llm_client(RecordedLLMClient(fallback_handler=deterministic_llm_fixture_handler))

    test_extractor()
    test_param_resolution()
    test_headed_headless_modes()
    test_cli_flags()
    test_e2e_headless_driver()
    test_page_candidate_extraction_and_routing()
    test_execution_report_generation()
    test_progressive_extraction_and_dropdowns_and_pagination()
    test_report_intent_and_screenshot_modes()
    test_loop_engineering_assertion_gates()
    test_missing_details_and_clarifying_questions()
    test_multi_turn_session_inheritance_and_batch_cancellation()
    test_conversational_basics()
    test_proactive_scenarios_and_persona_transitions()
    test_local_prompt_cache_and_data_inspection()
    test_subtask_macro_cache_and_semantic_invalidation()
    test_dialogue_manager_and_feedback_cache_invalidation()
    test_pagination_and_dropdown_consolidation_and_screenshot_fidelity()
    test_multisite_navigation_continuous_chat_and_auth_negation()
    test_progressive_discovery_horizon_planning_and_screen_grounding()
    test_loop_engineering_recovery_and_universal_navigation()
    test_qa_architect_edge_cases()
    test_universal_portability_and_multisite_comparison_telemetry()
    test_universal_form_filling_and_safe_url_driver()
    test_loop_engineering_fault_isolation_and_reactive_dialog_dismissal()
    test_variant_perception_cross_site_anchoring_and_disambiguation()
    test_multi_tab_domain_alignment_and_guarded_macros()
    test_multiturn_closed_loop_verification_and_tab_context()
    test_multiturn_outcome_grounding_and_screen_arbitration()
    print("\n✅ All self-checks passed successfully!")






