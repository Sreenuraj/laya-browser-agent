# Laya Browser Agent: Product Overview & Capabilities

> A 100% local, zero-cloud-cost autonomous web agent for private enterprise workflows.

---

## 1. Product Overview

Laya Browser Agent is an autonomous web automation agent designed for developers, QA engineers, and enterprise teams requiring fast, reliable web automation without the cost, latency, or data privacy risks associated with cloud AI APIs.

The system runs entirely on-device on Apple Silicon Metal GPUs (via Laya MLX) paired with local Ollama language models. It interacts with web applications through two unified interfaces:
1. **WebMCP (Web Model Context Protocol)**: Programmatic tool RPCs exposed via `navigator.modelContext`, `document.modelContext`, or `<form toolname="...">`.
2. **Universal Visual & DOM Actuation**: Coordinate clicks, table row scanning down to 8,000px, and keyboard input typing for web applications without WebMCP.

---

## 2. Comparison: Cloud Agents vs. Laya Browser Agent

| Dimension | Cloud Browser Agents (Claude / GPT-4o) | Laya Browser Agent |
| :--- | :--- | :--- |
| **Cloud API Cost** | $0.50 – $2.50 per workflow | **$0.00 (Zero cloud tokens)** |
| **Decision Latency** | 4,000 – 12,000 ms per step | **<15 ms per step (Metal GPU)** |
| **Data Privacy** | Credentials & PII transmitted over network | **100% On-Premise / Local Device** |
| **Workflow Duration** | 30 – 90 seconds | **3 – 8 seconds** |
| **Actuation Interface** | Brittle DOM scraping | **WebMCP Typed Tools + Deep Table Perception** |

---

## 3. Supported Web Applications & Protocols

Laya Browser Agent operates across modern web applications regardless of architecture:

### 3.1 WebMCP Native Applications
Exposes web capabilities directly to browser agents as typed, high-level JSON tools:
```javascript
await window.navigator.modelContext.getTools();
// Returns available tools: place_order, cancel_order, approve_order, search_products, etc.
```

### 3.2 Declarative WebMCP Forms
Standard HTML forms annotated with agent attributes:
```html
<form toolname="submit_feedback" tooldescription="Submit user feedback">
    <input name="email" toolparamdescription="User email" />
    <input name="rating" toolparamdescription="Rating 1 to 5" />
    <button type="submit">Submit</button>
</form>
```

### 3.3 Classic & Single-Page Web Applications (Without WebMCP)
Standard HTML5 elements, client-side routing, e-commerce stores, and complex tables:
- Interactive navigation bars, tabs, and links across multiple domains.
- Deep table row scanning with contextual icon button labeling (`Cancel`, `Approve`, `Delete`).
- Universal multi-field form entry (names, emails, passwords, phones, addresses, feedback textareas).
- Dynamic combobox and dropdown filtering across native `<select>` and ARIA components.
- Multi-page pagination loops with auto-scrolling to reveal controls below the fold.
- Automated transient overlay dismissal (cookie banners, popups, login modals).
- Multi-site workflows, cross-app research, and universal content audits (articles, cards, lists).

---

## 4. Key Capabilities

### Zero-Shot Intent Handling
- Operates without hardcoded intent tables, routes, or synonym lists.
- Decomposes multi-step user instructions into sequential subtasks dynamically.

### Progressive Discovery Horizon Planning
- Partitions user goals into a ~100% confident initial horizon (URL navigation, explicit credential login, primary search query) and deferred intent.
- Dynamically discovers and grounds downstream subtasks strictly against live screen affordances once the page settles.
- Eliminates speculative downstream hallucination of non-existent dropdowns or impossible pagination loops on single-page results.

### Sub-15ms Decision Loop on Metal GPU
- Evaluates candidate on-screen affordances in a single forward pass using Laya MLX on Apple Silicon Metal.
- Enforces candidate budgeting (<= 12 options) and a negative sink (`"other"`) to eliminate hallucinated actions.
- Powered by non-autoregressive encoder architecture (ModernBERT / mmBERT, ~322M–421M parameters) with calibrated probabilities and zero token generation lag.

### Machine-Actionable Sequential Planning
- Automatically decomposes human goals into concrete, machine-actionable browser steps (`Enter '<term>' into search input box and submit`, `Inspect search results and note the price of '<item>'`, `Select '<status>' from '<dropdown>' dropdown`).
- Accurately detects and maps search fields (`twotabsearchtextbox`, `field-keywords`, `q`) to query slots without parameter collision.

### Closed-Loop Remediation & Downstream Subtask Preservation
- Step errors, missing elements, or assertion failures trigger live DOM introspection and dynamic re-routing via local models rather than aborting.
- Explicit downstream preservation guarantees that plan revisions never drop upcoming phases or subsequent sites in multi-domain workflows.

### Proactive & Reactive Overlay Dismissal
- Automatically clears transient popups, newsletter dialogs, cookie consent banners, and e-commerce login walls (e.g. Flipkart, Amazon) proactively during screen settlement and reactively upon click interception.

### Truthful Run Status Evaluation & Subtask Integrity
- Eliminates false-positive completion reports by deterministically evaluating unhandled errors, unfulfillable findings, and completed subtask ratios.
- Markdown reports and HTML dashboards truthfully stamp runs as `COMPLETED (SUCCESS)`, `INCOMPLETE`, `UNFULFILLABLE`, or `FAILED`.
- Subtasks display faithful status markers: `[✓]` (done), `[✗]` (unfulfillable / failed with reason), and `[○]` (pending).

### High-Speed Batch Mutations
- Iterates and executes matching batch actions (e.g., *"cancel all open orders"*, *"approve all pending"*) in a single subtask step.
- Verifies completion using direct DOM assertions (`table tr:has-text('PENDING').count() == 0`) before advancing.

### Multi-Turn Conversational Memory & Outcome Grounding
- Maintains authentication credentials, active routes, and entity slots across chat turns at `laya > `.
- **Outcome Inheritance**: Subsequent turns inherit rich prior turn state (`last_outcome`), grounding follow-up requests on open tabs, discovered findings, and target entities.
- **Symmetric Multi-Tab Planning**: Follow-up requests operating across multiple sites automatically generate symmetric action sequences for each loaded tab without duplicate navigations.
- **Screen Arbitration & Action Blockers**: Low-confidence or blocked actions (e.g. unreleased or out-of-stock items displaying "Notify Me") are arbitrated via local LLM without guessing or clicking irrelevant navbar links, recording clear `unfulfillable` diagnostics for user action.
- Isolate ephemeral execution state (DOM nodes, action logs, screenshots) to maintain a bounded $O(1)$ token overhead (~40 tokens).

### Interactive Clarification & Persona Transitions
- Automatically prompts for missing credentials or parameters upfront.
- Clears credentials when switching user roles (e.g., from `user` to `admin`) to prevent cross-persona credential leakage.

### Two-Tier Persistent Caching (SQLite WAL)
- Caches planner prompts via deterministic SHA-256 fingerprinting (`prompt_cache`, `<0.2ms` retrieval).
- Caches subtask action macros partitioned by `(origin, route, intent)` (`subtask_cache`) with live screen affordance validation before replay.
- Evicts route macros and recent prompts automatically when user feedback indicates an outcome discrepancy.

### Autonomous Data Auditing
- Validates quantitative queries (e.g., counting pending orders or out-of-stock items) via WebMCP endpoints or DOM table scanning.
- Records structured counts directly into CLI receipts and reports.

### Dual Audit Reports (HTML & Markdown)
- Generates an interactive dark-themed HTML dashboard (`reports/execution_report.html`) and structured Markdown report (`reports/execution_report.md`).
- Includes execution summaries, step telemetry, masked credentials, and embedded visual screenshots with content-hash deduplication.

### Universal Machine & Model Portability
- Runs seamlessly across any machine capable of running local models: Apple Silicon Metal GPU, Linux (CUDA/ROCm/CPU), and Windows (WSL2/DirectML/CPU).
- Conditional `laya_mlx` accelerator import ensures graceful fallback to CPU and local LLM runners (Ollama / Llama.cpp) without crashes.
- Dynamic local model discovery auto-introspects available models on Ollama (`/api/tags`) and respects `OLLAMA_MODEL`.

### Minimalist Session Telemetry
- Detailed token accounting (prompt eval tokens, completion tokens, total tokens) and local cache statistics (reads, writes, estimated latency saved).
- Formatted in a clean, minimalist monospace table without over-beautification or redundant decorative elements.

### Clean Modular Scalability
- Adheres to a strict **500–600 line limit per module** with modular decomposition into dedicated components (`affordance.py`, `expansion.py`, `reporting.py`, `resolver.py`, `laya_engine.py`, `cache.py`, `variants.py`).

### Universal Variant Perception & Cross-Site Consistency Anchoring
- **Dynamic Spec Parsing (`variants.py`)**: Automatically detects hardware storage (`256 GB`, `1 TB`), shoe and apparel sizes (`Size 9`, `XL`), finishes (`Desert Titanium`), travel class (`Economy`), and unit quantities.
- **Accessory & Sponsored Filtering**: Suppresses phone cases and cables when searching for devices; deprioritizes sponsored ads that push maxed-out variants.
- **Repeatable Multi-Site Comparisons**: Anchors selected specs across sites (e.g., Amazon.in to Flipkart.com), ensuring apples-to-apples comparisons and eliminating false price differences.
- **Interactive Disambiguation**: Prompts users in CLI when multiple variants exist without explicit instructions, or on product pages to select size swatches before clicking "Add to Cart".

### Content-Hash Frame Deduplication
- Computes MD5 image hashes on raw browser frames to suppress duplicate PNG files on disk across consecutive idle steps and eliminate redundant visual verification proofs in Markdown and HTML dashboards.

### True Multi-Tab Browser Orchestration & Multi-Turn Tab Continuity
- Supports parallel browser tabs (`new_tab`, `switch_tab`, `get_tabs`, `close_tab`), allowing agents to execute multi-site workflows (e.g. comparing prices and adding items to cart across multiple websites) concurrently without overwriting existing tabs.
- Multi-turn conversational persistence: when users issue follow-up commands (e.g. *"add it to cart on each site without closing browser"*), the agent introspects open tabs, passes tab state to the planner, and smoothly switches between tabs rather than re-navigating from scratch.
- Re-navigation defense: calling `driver.start()` on an active shared session preserves the current tab workspace without redundant page reloads.

### Closed-Loop Subtask Verification & Anti-Stagnation Guardrails
- **Affirmative Action Fulfillment**: Intelligently separates text input directives from operational actions (add to cart, checkout, buy, cancel, approve, delete). When an operational action completes with verified success, the subtask immediately advances even if the subtask references an entity name.
- **Anti-Stagnation Engine**: Enforces step budgets per subtask; if actions fail or stagnate, transient exploratory prompt caches are evicted (`screen_locator`) and the agent remediates or advances, eliminating cyclic doom loops.
- **Guarded Subtask Macro Caching**: Enforces an advancement gate where subtask macros are written to `subtask_cache` strictly upon verified subtask completion. Ineffective or stagnating actions are isolated in `failed_actions` and existing macros are evicted to prevent repetitive action loops.

---

## 5. Reference Testing

Laya Browser Agent is verified against **[PostQode Nexus](https://github.com/Sreenuraj/postqode-nexus)**:
- **E-Commerce Workflows**: User login, product catalog navigation, item purchasing, and order cancellation.
- **Operations & Admin**: Order management, multi-order approvals and rejections, Command Center system inspection.

---

## 6. Testing & Practice Sites

- **Offline tests**: deterministic suite (~15 s, no network or Ollama) using `RecordedLLMClient`.
- **Practice sites**: `python -m bench serve` starts three small local apps (shop with WebMCP, admin table, multi-step form) for trying the agent by hand. An optional runner (`python -m bench run`) replays ready-made tasks and checks the final app state.
- **Trajectory log**: credential-masked per-step records in `$LAYA_HOME/trajectories/<run_id>.jsonl` (default `~/.laya`), useful for debugging and as future fine-tuning data.
- **Quality gate**: `make check` runs compilation, flake8 and the offline tests.
