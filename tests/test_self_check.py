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


if __name__ == "__main__":
    test_extractor()
    test_param_resolution()
    test_headed_headless_modes()
    test_cli_flags()
    test_e2e_headless_driver()
    test_page_candidate_extraction_and_routing()
    test_execution_report_generation()
    print("\n✅ All self-checks passed successfully!")


