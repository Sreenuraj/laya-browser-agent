"""Test Benchmark Oracles and Task Initial State Validation.

Verifies that:
1. Every T2 task oracle strictly FAILS on the application's clean starting state.
2. Every T3 task oracle strictly FAILS when no actions or invalid actions were taken.
3. Undeclared, empty, or always-true oracles are rejected with failure.
4. DotDict attribute access and safe builtins (len, any, all) evaluate correctly.
"""

from bench.loader import load_benchmark_tasks
from bench.oracles import BenchmarkOracle, DotDict, wrap_dotdict
from bench.server import FixtureServer, global_store


def test_dotdict_attribute_and_safe_builtins():
    """Verifies that DotDict allows attribute access and safe builtins work in expressions."""
    raw = {
        "orders": [
            {"product": "Wireless Ergonomic Mouse", "quantity": 1, "status": "PENDING"},
            {"product": "Mechanical Keyboard", "quantity": 2, "status": "APPROVED"},
        ],
        "state": {"selected_filter": "approved", "current_page": 2},
    }
    wrapped = wrap_dotdict(raw)
    assert isinstance(wrapped, DotDict)
    assert wrapped.orders[0].product == "Wireless Ergonomic Mouse"
    assert wrapped.orders[1].quantity == 2
    assert wrapped.state.selected_filter == "approved"
    assert wrapped.state.current_page == 2

    # Test safe eval with len, any, all
    context = {"orders": wrapped.orders, "state": wrapped.state}
    safe_builtins = {"len": len, "any": any, "all": all}
    assert eval("len(orders) == 2", {"__builtins__": safe_builtins}, context) is True
    assert eval("any(o.status == 'APPROVED' for o in orders)", {"__builtins__": safe_builtins}, context) is True


def test_undeclared_or_always_true_oracle_fails():
    """Verifies that missing, empty, or 'True' oracles are strictly rejected."""
    # Empty oracle
    passed, msg = BenchmarkOracle.evaluate({}, "http://localhost:8080", "shop", None, [], [])
    assert passed is False
    assert "no strict oracle" in msg.lower()

    # Always-true check expression
    passed, msg = BenchmarkOracle.evaluate({"type": "state", "check": "True"}, "http://localhost:8080", "shop", None, [], [])
    assert passed is False
    assert "always-true" in msg.lower()

    # Unknown oracle type
    passed, msg = BenchmarkOracle.evaluate({"type": "unknown_type"}, "http://localhost:8080", "shop", None, [], [])
    assert passed is False
    assert "unsupported" in msg.lower()


def test_all_t2_tasks_fail_on_starting_state():
    """Every single T2 task MUST fail against the clean initial fixture state."""
    server = FixtureServer(port=8989)
    server.start()
    try:
        global_store.reset_all()
        tasks = load_benchmark_tasks(tier="T2")
        assert len(tasks) == 40, f"Expected 40 T2 tasks, found {len(tasks)}"

        for task in tasks:
            site_route = task.site.replace("fixture/", "")
            passed, msg = BenchmarkOracle.evaluate(
                oracle_spec=task.oracle,
                base_url=server.base_url,
                site_route=site_route,
                agent=None,
                steps_taken=[],
                extracted_data=[],
            )
            assert passed is False, f"Task {task.id} falsely passed on starting state! Msg: {msg}"
    finally:
        server.stop()


def test_all_t3_tasks_fail_on_empty_execution():
    """Every single T3 task MUST fail when no actions were performed."""
    tasks = load_benchmark_tasks(tier="T3")
    assert len(tasks) == 40, f"Expected 40 T3 tasks, found {len(tasks)}"

    for task in tasks:
        passed, msg = BenchmarkOracle.evaluate(
            oracle_spec=task.oracle,
            base_url="http://localhost:3000",
            site_route="",
            agent=None,
            steps_taken=[],
            extracted_data=[],
        )
        assert passed is False, f"Task {task.id} falsely passed on starting state! Msg: {msg}"


def test_tool_row_selectors_reject_unrelated_rows():
    """A pre-existing unrelated row must never satisfy a webmcp oracle."""
    spec = {"field": "status", "expected": "CANCELLED", "id_prefix": {"id": "ORD-12"}, "match": "any"}
    rows = [{"id": "ORD-99", "status": "CANCELLED"}, {"id": "ORD-12", "status": "PENDING"}]
    assert BenchmarkOracle._check_tool_data(rows, spec)[0] is False
    rows.append({"id": "ORD-120", "status": "CANCELLED"})
    assert BenchmarkOracle._check_tool_data(rows, spec)[0] is True
    assert BenchmarkOracle._check_tool_data([], spec)[0] is False

    qty_spec = {"field": "status", "expected": "PENDING", "where": {"quantity": 2}, "match": "any"}
    assert BenchmarkOracle._check_tool_data([{"quantity": 1, "status": "PENDING"}], qty_spec)[0] is False
    assert BenchmarkOracle._check_tool_data([{"quantity": 2, "status": "PENDING"}], qty_spec)[0] is True


def test_url_oracle_requires_visited_page():
    class Step:
        def __init__(self, url):
            self.url = url

    spec = {"type": "url", "contains": "command-center"}
    run = lambda steps: BenchmarkOracle.evaluate(spec, "http://x", "", None, steps, [])[0]  # noqa: E731
    assert run([]) is False
    assert run([Step("http://x/login")]) is False
    assert run([Step("http://x/login"), Step("http://x/command-center")]) is True
    assert BenchmarkOracle.evaluate({"type": "url", "contains": ""}, "http://x", "", None, [Step("a")], [])[0] is False


def test_runner_routes_external_sites_without_fixture_prefix():
    """T3/T4 tasks (absolute site URL) must not be concatenated onto the fixture base URL."""
    from bench.runner import resolve_task_routing

    base, route, url = resolve_task_routing("http://localhost:3000", "http://127.0.0.1:8765")
    assert (base, route, url) == ("http://localhost:3000", "", "http://localhost:3000")
    base, route, url = resolve_task_routing("fixture/shop", "http://127.0.0.1:8765")
    assert (base, route, url) == ("http://127.0.0.1:8765", "shop", "http://127.0.0.1:8765/shop")
