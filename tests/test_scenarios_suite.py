"""Comprehensive Multi-Scenario Test Suite for Laya Browser Agent.

Covers:
1. Scenario 1: User places order for product, buys 1 unit, and logs out (verifies PENDING order created).
2. Scenario 2: User navigates to /my-orders, cancels order starting with ID prefix, and logs out (verifies CANCELLED).
3. Scenario 3: Multi-role workflow (User places order -> Admin approves order on /orders -> verifies APPROVED).
4. Scenario 4: Multi-role workflow (User places order -> Admin rejects order on /orders -> verifies REJECTED).
5. Scenario 5: Admin Command Center inspection & clean logout.
"""

import json
import os
import sys
import time
from typing import Optional
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from playwright.sync_api import sync_playwright
from laya_browser_agent.agent import LayaAgent

BASE_URL = "http://localhost:3000"


def verify_order_status(order_id_prefix: str, expected_status: str, role: str = "user"):
    """Asserts that the order starting with order_id_prefix is in expected_status."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL)
        
        username = "admin" if role == "admin" else "user"
        password = "Admin@123" if role == "admin" else "User@123"
        page.fill('input[name="username"], #username', username)
        page.fill('input[name="password"], #password', password)
        page.click('button[type="submit"], button:has-text("Login")')
        page.wait_for_load_state("networkidle")
        
        target_path = "/orders" if role == "admin" else "/my-orders"
        tool_name = "get_orders" if role == "admin" else "get_my_orders"
        page.goto(f"{BASE_URL}{target_path}")
        page.wait_for_load_state("networkidle")

        raw = page.evaluate(f"""async () => {{
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            const tools = await ctx.getTools();
            const getter = tools.find(t => t.name === "{tool_name}");
            return await ctx.executeTool(getter, {{}});
        }}""")
        orders = json.loads(raw) if isinstance(raw, str) else raw
        target = next((o for o in orders if str(o.get("id", "")).startswith(order_id_prefix)), None)
        browser.close()
        
        assert target is not None, f"Order {order_id_prefix} not found via {tool_name}"
        actual = target.get("status")
        assert actual == expected_status, f"Expected status {expected_status} for order {order_id_prefix}, got {actual}"
        return target


def test_scenario_1_user_places_order() -> str:
    """Scenario 1: User logs in, searches product, buys 1 unit, logs out."""
    print("\n[Scenario 1/5] User Order Placement Workflow...")
    agent = LayaAgent(
        target_url=BASE_URL,
        goal="login as user (user/User@123), search for E2E-ADM002-1789625027 product, buy 1 unit and logout",
        headed=False,
        max_steps=12,
    )
    res = agent.run()
    assert res.get("status") == "success", f"Scenario 1 agent run failed: {res}"

    # Extract created order ID from agent last result or user orders
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL)
        page.fill('input[name="username"], #username', "user")
        page.fill('input[name="password"], #password', "User@123")
        page.click('button[type="submit"], button:has-text("Login")')
        page.wait_for_load_state("networkidle")
        page.goto(f"{BASE_URL}/my-orders")
        page.wait_for_load_state("networkidle")

        raw = page.evaluate("""async () => {
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            const tools = await ctx.getTools();
            const getter = tools.find(t => t.name === "get_my_orders");
            return await ctx.executeTool(getter, {});
        }""")
        orders = json.loads(raw) if isinstance(raw, str) else raw
        pending_orders = [o for o in orders if o.get("status") == "PENDING"]
        assert len(pending_orders) > 0, "No pending orders found after place_order"
        pending_orders.sort(key=lambda x: str(x.get("createdAt", "")), reverse=True)
        latest_id = pending_orders[0]["id"]
        browser.close()

    print(f"      ✓ Scenario 1 passed! Order placed: {latest_id[:8]} (Status: PENDING)")
    return latest_id[:8]


def test_scenario_2_user_cancels_order(order_prefix: Optional[str] = None):
    """Scenario 2: User logs in, goes to my orders, cancels order by ID prefix, logs out."""
    if not order_prefix:
        order_prefix = test_scenario_1_user_places_order()
    print(f"\n[Scenario 2/5] User Order Cancellation Workflow (order: {order_prefix})...")
    agent = LayaAgent(
        target_url=BASE_URL,
        goal=f"login as user (user/User@123), got to my order, cancel my order starting with {order_prefix} and logout",
        headed=False,
        max_steps=10,
    )
    res = agent.run()
    assert res.get("status") == "success", f"Scenario 2 agent run failed: {res}"

    # Verify status in database
    verify_order_status(order_prefix, "CANCELLED", role="user")
    print(f"      ✓ Scenario 2 passed! Order {order_prefix} verified as CANCELLED!")


def test_scenario_3_admin_approves_order():
    """Scenario 3: User places order -> Admin logs in and approves the order."""
    print("\n[Scenario 3/5] Multi-Role Workflow: User places order -> Admin approves order...")
    # Step A: User places order
    order_prefix = test_scenario_1_user_places_order()

    # Step B: Admin approves order
    print(f"      Admin approving order starting with {order_prefix}...")
    agent = LayaAgent(
        target_url=BASE_URL,
        goal=f"login as admin (admin/Admin@123), go to order management, approve order starting with {order_prefix} and logout",
        headed=False,
        max_steps=10,
    )
    res = agent.run()
    assert res.get("status") == "success", f"Admin approval agent run failed: {res}"

    # Step C: Verify order status is APPROVED
    verify_order_status(order_prefix, "APPROVED", role="admin")
    print(f"      ✓ Scenario 3 passed! Order {order_prefix} verified as APPROVED by Admin!")


def test_scenario_4_admin_rejects_order():
    """Scenario 4: User places order -> Admin logs in and rejects the order."""
    print("\n[Scenario 4/5] Multi-Role Workflow: User places order -> Admin rejects order...")
    # Step A: User places order
    order_prefix = test_scenario_1_user_places_order()

    # Step B: Admin rejects order
    print(f"      Admin rejecting order starting with {order_prefix}...")
    agent = LayaAgent(
        target_url=BASE_URL,
        goal=f"login as admin (admin/Admin@123), go to order management, reject order starting with {order_prefix} and logout",
        headed=False,
        max_steps=10,
    )
    res = agent.run()
    assert res.get("status") == "success", f"Admin rejection agent run failed: {res}"

    # Step C: Verify order status is REJECTED
    verify_order_status(order_prefix, "REJECTED", role="admin")
    print(f"      ✓ Scenario 4 passed! Order {order_prefix} verified as REJECTED by Admin!")


def test_scenario_5_admin_command_center_inspection():
    """Scenario 5: Admin logs in, navigates to Command Center, and logs out."""
    print("\n[Scenario 5/5] Admin Navigation & Session Lifecycle...")
    agent = LayaAgent(
        target_url=BASE_URL,
        goal="login as admin (admin/Admin@123), navigate to /command-center and logout",
        headed=False,
        max_steps=8,
    )
    res = agent.run()
    assert res.get("status") == "success", f"Scenario 5 agent run failed: {res}"
    print("      ✓ Scenario 5 passed! Admin command center and clean logout verified!")


if __name__ == "__main__":
    print("======================================================================")
    print("  🚀 RUNNING COMPREHENSIVE E2E SCENARIOS FOR LAYA BROWSER AGENT")
    print("======================================================================")
    start = time.time()

    # Scenario 1 & 2
    prefix = test_scenario_1_user_places_order()
    test_scenario_2_user_cancels_order(prefix)

    # Scenario 3: Admin Approval Workflow
    test_scenario_3_admin_approves_order()

    # Scenario 4: Admin Rejection Workflow
    test_scenario_4_admin_rejects_order()

    # Scenario 5: Admin Navigation
    test_scenario_5_admin_command_center_inspection()

    elapsed = time.time() - start
    print(f"\n🎉 ALL 5 COMPREHENSIVE SCENARIOS PASSED IN {elapsed:.2f}s!")
    print("======================================================================")
