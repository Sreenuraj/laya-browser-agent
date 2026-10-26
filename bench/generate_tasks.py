"""Generates standard benchmark task definitions into bench/tasks/.

Run: python -m bench.generate_tasks
"""

from pathlib import Path
import yaml

TASKS_DIR = Path(__file__).resolve().parent / "tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)


def generate_t2_tasks():
    # 20 Shop tasks
    shop_tasks = [
        # (id, goal, check, split, tags)
        ("t2-shop-001", "Login as user (user/User@123), order 1 unit of Wireless Ergonomic Mouse", "len(orders) > 0 and orders[-1].product == 'Wireless Ergonomic Mouse' and orders[-1].quantity == 1 and orders[-1].status == 'PENDING'", "train", ["login", "order", "shop"]),
        ("t2-shop-002", "Login as user (user/User@123), order 2 units of Mechanical Keyboard", "len(orders) > 0 and orders[-1].product == 'Mechanical Keyboard' and orders[-1].quantity == 2 and orders[-1].status == 'PENDING'", "heldout", ["login", "order", "shop", "qty"]),
        ("t2-shop-003", "Order 1 unit of Wireless Ergonomic Mouse and confirm order is pending", "len(orders) > 0 and orders[-1].status == 'PENDING'", "train", ["order", "shop"]),
        ("t2-shop-004", "Login as user (user/User@123), order 1 unit of Mechanical Keyboard, then cancel the order", "len(orders) > 0 and orders[-1].status == 'CANCELLED'", "heldout", ["login", "order", "cancel", "shop"]),
        ("t2-shop-005", "Login as user (user/User@123), navigate to Products, order 3 units of Wireless Ergonomic Mouse, and logout", "len(orders) > 0 and orders[-1].quantity == 3", "train", ["login", "order", "logout", "shop"]),
        ("t2-shop-006", "Check products catalog and verify Wireless Ergonomic Mouse is in stock", {"type": "answer", "substring": "in_stock"}, "heldout", ["read", "shop"]),
        ("t2-shop-007", "Login as user (user/User@123), place an order for Mechanical Keyboard with quantity 1", "len(orders) > 0 and orders[-1].quantity == 1", "train", ["login", "order"]),
        ("t2-shop-008", "Navigate to Acme Hardware Store, login as user (user/User@123), order 2 units of Wireless Ergonomic Mouse, then view orders", "len(orders) >= 1 and orders[-1].quantity == 2", "heldout", ["login", "order", "nav"]),
        ("t2-shop-009", "Order 1 unit of Mechanical Keyboard as user (user/User@123), then check orders list", "len(orders) >= 1 and orders[-1].product == 'Mechanical Keyboard'", "train", ["order", "shop"]),
        ("t2-shop-010", "Login as user (user/User@123), buy 1 Wireless Ergonomic Mouse, then logout", "len(orders) > 0 and orders[-1].product == 'Wireless Ergonomic Mouse' and user is None", "heldout", ["login", "buy", "logout"]),
        ("t2-shop-011", "Buy 2 units of Mechanical Keyboard using user (user/User@123)", "len(orders) > 0 and orders[-1].quantity == 2", "train", ["buy", "shop"]),
        ("t2-shop-012", "Login as user (user/User@123), place order and cancel it in my orders", "len(orders) > 0 and orders[-1].status == 'CANCELLED'", "heldout", ["login", "cancel", "shop"]),
        ("t2-shop-013", "Order 1 Wireless Ergonomic Mouse", "len(orders) > 0 and orders[-1].product == 'Wireless Ergonomic Mouse'", "train", ["order", "shop"]),
        ("t2-shop-014", "Login as user (user/User@123), order 1 Mechanical Keyboard, verify pending order", "len(orders) > 0 and orders[-1].status == 'PENDING'", "heldout", ["login", "order", "verify"]),
        ("t2-shop-015", "Go to products, order 1 unit of Wireless Ergonomic Mouse as user (user/User@123)", "len(orders) > 0 and orders[-1].product == 'Wireless Ergonomic Mouse'", "train", ["order"]),
        ("t2-shop-016", "Sign in as user (user/User@123), buy 3 units of Wireless Ergonomic Mouse, then confirm order exists", "len(orders) > 0 and orders[-1].quantity == 3", "heldout", ["buy", "qty"]),
        ("t2-shop-017", "Login as user (user/User@123), place an order for Mechanical Keyboard, cancel it", "len(orders) > 0 and orders[-1].status == 'CANCELLED'", "train", ["cancel"]),
        ("t2-shop-018", "Order 1 unit of Wireless Ergonomic Mouse and logout", "len(orders) > 0 and orders[-1].product == 'Wireless Ergonomic Mouse' and user is None", "heldout", ["order", "logout"]),
        ("t2-shop-019", "Login with user/User@123, buy 1 unit of Mechanical Keyboard", "len(orders) > 0 and orders[-1].product == 'Mechanical Keyboard'", "train", ["buy"]),
        ("t2-shop-020", "Login as user (user/User@123), order 2 units of any in-stock product, confirm it is pending and logout", "len(orders) > 0 and orders[-1].quantity == 2 and orders[-1].status == 'PENDING' and user is None", "heldout", ["multi-step", "verify", "regression"]),
    ]

    for tid, goal, check, split, tags in shop_tasks:
        oracle = check if isinstance(check, dict) else {"type": "state", "check": check}
        spec = {
            "id": tid,
            "tier": "T2",
            "site": "fixture/shop",
            "goal": goal,
            "oracle": oracle,
            "max_steps": 20,
            "tags": tags,
            "split": split,
        }
        with open(TASKS_DIR / f"{tid}.yaml", "w", encoding="utf-8") as f:
            yaml.dump(spec, f, sort_keys=False)

    # 10 Admin tasks
    admin_tasks = [
        ("t2-admin-001", "Filter admin candidates by status 'pending'", "state.selected_filter == 'pending'", "train", ["filter", "admin"]),
        ("t2-admin-002", "Approve candidate Alice Smith (USR-001)", "items[0].status == 'APPROVED'", "heldout", ["approve", "admin"]),
        ("t2-admin-003", "Reject candidate Charlie Brown (USR-003)", "items[2].status == 'REJECTED'", "train", ["reject", "admin"]),
        ("t2-admin-004", "Select status 'approved' from dropdown filter", "state.selected_filter == 'approved'", "heldout", ["filter", "dropdown"]),
        ("t2-admin-005", "Navigate to next page of candidates via pagination", "state.current_page == 2", "train", ["pagination"]),
        ("t2-admin-006", "Approve pending candidate with ID USR-003", "items[2].status == 'APPROVED'", "heldout", ["approve"]),
        ("t2-admin-007", "Search candidates for 'Alice'", "state.search_query.lower() == 'alice'", "train", ["search"]),
        ("t2-admin-008", "Reject pending candidate USR-001", "items[0].status == 'REJECTED'", "heldout", ["reject"]),
        ("t2-admin-009", "Filter table by status 'rejected' and check list", "state.selected_filter == 'rejected'", "train", ["filter"]),
        ("t2-admin-010", "Navigate to page 2 using Next button", "state.current_page == 2", "heldout", ["pagination", "nav"]),
    ]

    for tid, goal, check, split, tags in admin_tasks:
        spec = {
            "id": tid,
            "tier": "T2",
            "site": "fixture/admin",
            "goal": goal,
            "oracle": {"type": "state", "check": check},
            "max_steps": 15,
            "tags": tags,
            "split": split,
        }
        with open(TASKS_DIR / f"{tid}.yaml", "w", encoding="utf-8") as f:
            yaml.dump(spec, f, sort_keys=False)

    # 10 Forms tasks
    form_tasks = [
        ("t2-forms-001", "Dismiss cookie consent notice overlay", "state.banner_dismissed is True", "train", ["overlay", "forms"]),
        ("t2-forms-002", "Fill full name 'Alice Smith' and email 'alice@example.com', then click Next Step", "state.current_step == 2 and state.data.full_name == 'Alice Smith'", "heldout", ["form", "step"]),
        ("t2-forms-003", "Dismiss cookie overlay, enter name 'Bob Jones' and email 'bob@example.com', click Next", "state.banner_dismissed is True and state.current_step >= 2", "train", ["overlay", "form"]),
        ("t2-forms-004", "Complete 3-step registration wizard with name 'Charlie', email 'charlie@test.com' and submit", "state.submitted is True", "heldout", ["wizard", "submit"]),
        ("t2-forms-005", "Accept cookies and start registration step 2", "state.banner_dismissed is True and state.current_step >= 2", "train", ["overlay"]),
        ("t2-forms-006", "Complete wizard registration for user David (david@work.org) and submit", "state.submitted is True", "heldout", ["wizard"]),
        ("t2-forms-007", "Dismiss cookie notice banner", "state.banner_dismissed is True", "train", ["banner"]),
        ("t2-forms-008", "Enter registration details for Eva (eva@lab.io), advance steps and submit", "state.submitted is True", "heldout", ["wizard", "submit"]),
        ("t2-forms-009", "Fill in account info and advance to step 2", "state.current_step >= 2", "train", ["input"]),
        ("t2-forms-010", "Dismiss overlay banner and submit application form", "state.banner_dismissed is True and state.submitted is True", "heldout", ["overlay", "submit"]),
    ]

    for tid, goal, check, split, tags in form_tasks:
        spec = {
            "id": tid,
            "tier": "T2",
            "site": "fixture/forms",
            "goal": goal,
            "oracle": {"type": "state", "check": check},
            "max_steps": 15,
            "tags": tags,
            "split": split,
        }
        with open(TASKS_DIR / f"{tid}.yaml", "w", encoding="utf-8") as f:
            yaml.dump(spec, f, sort_keys=False)


def generate_t3_tasks():
    # 40 Nexus tasks (5 scenarios + variations + report goal regression)
    for i in range(1, 41):
        tid = f"t3-nexus-{i:03d}"
        if i == 1:
            goal = "login as user, go to products, order 2 qty of any available product, go to my orders and confirm it is pending."
            tags = ["regression", "report_goal", "order"]
            oracle = {"type": "webmcp", "tool": "get_my_orders", "login": {"username": "user", "password": "User@123"}, "path": "/my-orders", "field": "status", "expected": "PENDING", "where": {"quantity": 2}, "match": "any"}
        elif i % 5 == 1:
            goal = f"login as user (user/User@123), search for E2E-ADM{i:03d} product, buy 1 unit and logout"
            tags = ["scenario1", "user", "order"]
            oracle = {"type": "webmcp", "tool": "get_my_orders", "login": {"username": "user", "password": "User@123"}, "path": "/my-orders", "field": "status", "expected": "PENDING", "where": {"quantity": 1}, "match": "any"}
        elif i % 5 == 2:
            goal = f"login as user (user/User@123), got to my order, cancel my order starting with ORD-{i} and logout"
            tags = ["scenario2", "user", "cancel"]
            oracle = {"type": "webmcp", "tool": "get_my_orders", "login": {"username": "user", "password": "User@123"}, "path": "/my-orders", "field": "status", "expected": "CANCELLED", "id_prefix": {"id": f"ORD-{i}"}, "match": "any"}
        elif i % 5 == 3:
            goal = f"login as admin (admin/Admin@123), go to order management, approve order starting with ORD-{i} and logout"
            tags = ["scenario3", "admin", "approve"]
            oracle = {"type": "webmcp", "tool": "get_orders", "login": {"username": "admin", "password": "Admin@123"}, "path": "/orders", "field": "status", "expected": "APPROVED", "id_prefix": {"id": f"ORD-{i}"}, "match": "any"}
        elif i % 5 == 4:
            goal = f"login as admin (admin/Admin@123), go to order management, reject order starting with ORD-{i} and logout"
            tags = ["scenario4", "admin", "reject"]
            oracle = {"type": "webmcp", "tool": "get_orders", "login": {"username": "admin", "password": "Admin@123"}, "path": "/orders", "field": "status", "expected": "REJECTED", "id_prefix": {"id": f"ORD-{i}"}, "match": "any"}
        else:
            goal = "login as admin (admin/Admin@123), navigate to /command-center and logout"
            tags = ["scenario5", "admin", "nav"]
            oracle = {"type": "url", "contains": "command-center"}

        spec = {
            "id": tid,
            "tier": "T3",
            "site": "http://localhost:3000",
            "goal": goal,
            "oracle": oracle,
            "max_steps": 15,
            "tags": tags,
            "split": "heldout" if i % 2 == 0 else "train",
        }
        with open(TASKS_DIR / f"{tid}.yaml", "w", encoding="utf-8") as f:
            yaml.dump(spec, f, sort_keys=False)


if __name__ == "__main__":
    generate_t2_tasks()
    generate_t3_tasks()
    print(f"Generated {len(list(TASKS_DIR.glob('*.yaml')))} tasks in {TASKS_DIR}")
