"""Unit tests for Phase 0 infrastructure (LLM client, Trajectory Logger, Stats).

Ensures AGENTS.md §5.3 adherence with runnable assertions for all new tooling.
"""

from pathlib import Path
from laya_browser_agent.llm import (
    extract_json_payload,
    RecordedLLMClient,
    resolve_local_model,
    strip_thinking_tags,
)
from laya_browser_agent.trajectory import TrajectoryLogger, mask_sensitive_values
from bench.stats import wilson_interval


def test_thinking_tag_scrubbing_and_json_extraction():
    """Verifies that <think> blocks are stripped and JSON extracted from various formats."""
    raw = "<think>I should formulate the plan now.</think>\n```json\n{\"action\": \"click\", \"id\": 1}\n```"
    cleaned = strip_thinking_tags(raw)
    assert "<think>" not in cleaned
    assert "click" in cleaned

    parsed = extract_json_payload(raw)
    assert isinstance(parsed, dict)
    assert parsed.get("action") == "click"
    assert parsed.get("id") == 1


def _fake_tags(monkeypatch, models):
    """Mocks Ollama /api/tags so model resolution is deterministic and offline."""
    import laya_browser_agent.llm as llm_mod

    class _Resp:
        status_code = 200

        def json(self):
            return {"models": [{"name": m} for m in models]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(llm_mod.httpx, "Client", _Client)


def test_resolve_local_model_discovery(monkeypatch):
    """Preferred is honoured only when installed; URL-as-first-arg is refused; env var wins."""
    import pytest

    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    _fake_tags(monkeypatch, ["qwen2.5:7b", "other:1b"])
    assert resolve_local_model("other:1b") == "other:1b"
    # Not installed -> falls back to best installed, never trusts a missing model
    assert resolve_local_model("missing:70b") == "qwen2.5:7b"
    assert resolve_local_model() == "qwen2.5:7b"
    with pytest.raises(ValueError):
        resolve_local_model("http://localhost:11434")

    monkeypatch.setenv("OLLAMA_MODEL", "env-override:latest")
    assert resolve_local_model("other:1b") == "env-override:latest"


def test_recorded_llm_client_replay():
    """Verifies RecordedLLMClient replays recorded fixture dictionaries."""
    recordings = {"test-prompt": {"intent": "login", "slots": {"user": "alice"}}}
    client = RecordedLLMClient(recordings=recordings)
    res, in_tok, out_tok, lat = client.chat_json([{"role": "user", "content": "test-prompt"}])
    assert res == {"intent": "login", "slots": {"user": "alice"}}
    assert lat < 1.0


def test_trajectory_logger_credential_masking(tmp_path: Path):
    """Verifies that sensitive credentials are masked in trajectory logs."""
    logger = TrajectoryLogger(run_id="test_run_masking", base_dir=str(tmp_path))
    logger.log_step(
        step=1,
        subtask="login with user and password secret123",
        goal="login",
        url="http://test.local/login",
        candidates={"pass_field": {"name": "password"}},
        chosen_action="type",
        confidence=0.99,
        action_params={"username": "alice", "password": "SuperSecretPassword123!"},
        duration_ms=12.5,
        engine_info="Laya MLX",
    )

    entries = logger.get_entries()
    assert len(entries) == 1
    logged_params = entries[0]["action_params"]
    assert logged_params["password"] == "••••••••"
    assert "SuperSecretPassword123!" not in str(entries[0])

    # Direct helper validation
    masked = mask_sensitive_values({"api_token": "secret_abc", "role": "admin"})
    assert masked["api_token"] == "••••••••"
    assert masked["role"] == "admin"


def test_trajectory_masks_goal_inline_credentials_and_keeps_unrelated_keys(tmp_path: Path):
    """Goal text with user/Password pairs is masked; keys merely containing 'auth' are not."""
    logger = TrajectoryLogger(run_id="test_goal_masking", base_dir=str(tmp_path))
    logger.log_step(
        step=1, subtask="login (user/User@123)", goal="Login as user (user/User@123), order 1 unit",
        url="http://t/", candidates={"author": "bob"}, chosen_action="click", confidence=0.5,
        action_params={}, duration_ms=1.0, engine_info="Laya",
    )
    entry = logger.get_entries()[0]
    assert "User@123" not in str(entry)
    assert entry["goal"].startswith("Login as user (user/")
    assert "order 1 unit" in entry["goal"]
    assert entry["candidates"]["author"] == "bob"
    assert entry["verified"] is None  # unknown until Phase 1 verifies post-conditions


def test_strict_recorded_client_raises_on_unrecorded_prompt():
    import pytest

    client = RecordedLLMClient(strict=True)
    client.add_recording("known", {"ok": True})
    assert client.chat_json([{"role": "user", "content": "a KNOWN prompt"}])[0] == {"ok": True}
    assert client.llm_call_count == 1
    with pytest.raises(RuntimeError):
        client.chat_json([{"role": "user", "content": "something else"}])


def test_tests_never_touch_real_laya_home(isolated_laya_home):
    """conftest redirects the prompt cache DB and trajectory logs to a temp dir."""
    from laya_browser_agent.cache import global_prompt_cache

    assert str(isolated_laya_home) in global_prompt_cache.db_path
    assert TrajectoryLogger(run_id="x").log_path.parent == isolated_laya_home / "trajectories"


def test_fixture_store_reset_and_unknown_app():
    import pytest
    from bench.server import FixtureStateStore

    store = FixtureStateStore()
    store.shop_state["orders"].append({"id": "x"})
    store.reset("shop")
    assert store.state("shop")["orders"] == []
    with pytest.raises(KeyError):
        store.state("nope")
    with pytest.raises(KeyError):
        store.reset("nope")


def test_wilson_interval_bounds():
    """Verifies 95% Wilson score interval calculation and edge case handling."""
    # Zero successes
    p, low, high = wilson_interval(0, 10)
    assert p == 0.0
    assert 0.0 <= low <= high <= 1.0

    # 100% successes
    p, low, high = wilson_interval(10, 10)
    assert p == 1.0
    assert 0.0 <= low <= high <= 1.0

    # Zero total
    p, low, high = wilson_interval(0, 0)
    assert p == 0.0
    assert low == 0.0
    assert high == 0.0

def test_email_address_is_not_a_navigation_target():
    """'david@work.org' must never be parsed as the site 'work.org' (caused a bogus navigation in T2 forms-006)."""
    from laya_browser_agent.agent import LayaAgent

    nav = LayaAgent._extract_navigation_target
    assert nav(None, "Complete wizard registration for user David (david@work.org) and submit") is None
    assert nav(None, "Open the form and enter alice@example.com") is None
    assert nav(None, "Go to amazon.in") == "https://amazon.in"
    assert nav(None, "Visit https://example.org/login") == "https://example.org/login"
    # 'on' inside a word (e.g. 'confirmation') must not count as a navigation verb
    assert nav(None, "Check confirmation for page.example.com") is None


def test_ollama_client_disables_thinking_by_default(monkeypatch):
    """Reasoning models take ~20 s per call with thinking on; the client must send think=false."""
    import laya_browser_agent.llm as llm_mod

    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"message": {"content": '{"ok": true}'}, "prompt_eval_count": 1, "eval_count": 1}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json):
            sent.update(json)
            return _Resp()

    monkeypatch.setattr(llm_mod.httpx, "Client", _Client)
    client = llm_mod.OllamaClient(base_url="http://x", model="m")
    parsed, _, _, _ = client.chat_json([{"role": "user", "content": "hi"}])
    assert parsed == {"ok": True}
    assert sent["think"] is False
    assert client.llm_call_count == 1
    assert llm_mod.OllamaClient(base_url="http://x", model="m", think=True).think is True



def test_call_tool_never_hangs_on_unresolved_native_webmcp(tmp_path: Path):
    """A native executeTool that never resolves must time out, not freeze the step loop."""
    import time
    from laya_browser_agent.browser import PlaywrightWebMcpDriver

    page_file = tmp_path / "hang.html"
    page_file.write_text(
        "<script>navigator.modelContext = {getTools: async () => [{name: 'stuck'}],"
        " executeTool: () => new Promise(() => {})};</script>"
    )
    driver = PlaywrightWebMcpDriver(headed=False)
    try:
        driver.start(page_file.as_uri())
        t0 = time.time()
        out = driver.call_tool("stuck", {})
        assert out == {"status": "timeout", "tool": "stuck"}
        assert time.time() - t0 < 10
    finally:
        driver.close()


def test_shop_fixture_webmcp_tools_mutate_state():
    """Fixture WebMCP form tools must resolve promptly and actually change server state."""
    import time
    import httpx
    from bench.runner import get_fixture_server
    from laya_browser_agent.browser import PlaywrightWebMcpDriver

    server = get_fixture_server()
    server.start()
    httpx.get(f"{server.base_url}/shop/__reset", timeout=2.0)
    driver = PlaywrightWebMcpDriver(headed=False)
    try:
        driver.start(f"{server.base_url}/shop/")
        t0 = time.time()
        driver.call_tool("sign_in", {"username": "user", "password": "User@123"})
        driver.call_tool("submit_purchase", {"product": "Wireless Ergonomic Mouse", "quantity": "2"})
        assert time.time() - t0 < 5
        driver.page.wait_for_timeout(300)
        state = httpx.get(f"{server.base_url}/shop/__state", timeout=2.0).json()
        assert state["logged_in_user"] == "user"
        assert state["orders"][-1]["quantity"] == 2
    finally:
        driver.close()
