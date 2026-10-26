# Technical Architecture Specification

> Architecture and implementation specification for Laya Browser Agent: a zero-cost, sub-second autonomous web agent running locally on Apple Silicon Metal GPU.

---

## 1. System Architecture

Laya Browser Agent separates web automation into three decoupled tiers: intent planning, semantic decision evaluation, and native browser actuation.

```
+------------------------------------------------------------------------+
|                          User / CLI Interface                          |
|         ./run.sh --task "<Goal>"  OR  Interactive Chat Session         |
+-----------------------------------+-+----------------------------------+
                                    |
                                    v
+------------------------------------------------------------------------+
| Tier 1: Intent & Planning Layer (Local LLM: Ollama qwen2.5)           |
|   - Decomposes natural language instructions into ordered subtasks    |
|   - Extracts parameter slots (credentials, queries, quantities, IDs)   |
|   - Preserves user destination names (Scope Anchoring)                 |
|   - Formulates targeted clarifying questions for missing parameters   |
+-----------------------------------+-+----------------------------------+
                                    |
                                    v
+------------------------------------------------------------------------+
| Tier 2: Semantic Decision & Policy Layer (Laya MLX on Metal GPU)       |
|   - Evaluates active subtask against on-screen affordances in <15ms    |
|   - Curates candidates to <= 12 options fitting 192-token head budget  |
|   - Enforces "other" negative sink to prevent probability spillage     |
|   - Evaluates cached route macros against current screen affordances  |
+-----------------------------------+-+----------------------------------+
                                    |
                                    v
+------------------------------------------------------------------------+
| Tier 3: Browser Actuation & Affordance Validation (Playwright & WebMCP)|
|   - Discovers live WebMCP tools and interactive DOM elements via CDP   |
|   - Verifies target element existence before executing cached macros   |
|   - Executes native Playwright actions with auto-scroll into view      |
|   - Validates outcome state with DOM assertions                        |
+------------------------------------------------------------------------+
```

---

## 2. Dynamic Intent Planning & Zero Hardcoding Policy

The agent enforces a strict architectural invariant: **zero application-specific routes, entity names, action verbs, or domain assumptions in Python code**.

### 2.1 Intent Decomposition
- Planning is delegated entirely to the local LLM (`qwen2.5:3b` or `qwen2.5:7b` via Ollama).
- Complex instructions are decomposed into sequential subtasks without consulting predefined intent tables.
- **Scope Anchoring**: Exact destination entities specified by the user (e.g., `my orders`, `products`, `dashboard`) are preserved without rewriting into generic labels.

### 2.2 Parameter Extraction & Clarification
- Parameters are structured by `SlotExtractor`:
  - Authentication: `username`, `password`
  - Navigation: `target_url`, route identifiers
  - Search / Filtering: `search` (keywords only), `status` / `option` (dropdowns)
  - Operations: `quantity` (integer), `orderId`
- **Interactive Clarification**: If required parameters are missing (e.g., authentication requested without credentials), the planner pauses and prompts the user.
- **Direct Slot Binding**: Clarification responses are directly bound to the active missing slot rather than blindly appended to the prompt, preventing misinterpretation by subsequent planning passes.

### 2.3 Progressive Discovery Horizon Planning
To eliminate speculative hallucination of downstream UI widgets or impossible pagination loops before seeing a webpage:
1. **Confident Initial Horizon**:
   - The planner extracts only initial subtask steps it is ~100% confident can be executed immediately before seeing the live screen (e.g. target URL navigation, explicit credential login, primary search query).
   - Downstream instructions that depend on unseen page structure (conditional branches *"if there are multiple pages"*, speculative dropdowns or comboboxes, pagination loops, result audits) are partitioned into `deferred_goal`.
2. **Live Screen Affordance Grounding (`expand_plan_from_screen`)**:
   - When the initial confident horizon subtasks complete, the browser settles on the target page.
   - The agent inspects the live affordance tree (`current_url`, `screen_elements`, `tools`) and dynamically synthesizes the next 1–3 grounded subtasks strictly using elements present on the real screen.
   - **Single vs Multi-Page Affordance Validation**: If a prompt mentions pagination but the live page only has a single page of results (no "Next" button), the planner omits the pagination loop and takes a single capture, preventing impossible loops.
   - **Alternative Affordances**: If a requested filter exists as a direct checkbox or facet link rather than a dropdown, the agent grounds directly to the live element (`Click '<option>'`).

### 2.4 Multi-Turn Outcome Grounding & Symmetric Tab Planning
In multi-turn interactive sessions:
- **Outcome Inheritance**: Subsequent turns inherit rich prior turn state (`session_context["turn_outcomes"]` and `last_outcome`), including completed subtasks, extracted slots, discovered metrics/data, and currently open browser tabs (`index`, `url`, `title`, `active`).
- **Anaphora Resolution**: Pronouns and generic references (*"it"*, *"that"*, *"the item"*, *"them"*) are automatically grounded against the established entity slots from prior turn outcomes.
- **Symmetric Multi-Site Execution**: When follow-up requests target multiple loaded sites (*"add it to cart, have separate tab for each site"*), the planner synthesizes symmetric subtask sequences across each open domain (`Switch to <domain> tab` -> `perform action`) without redundant navigations or opening duplicate tabs.
- **Conversational Constraint Decoupling**: User instructions about session modality or browser tab retention (*"do not close any browser tabs"*, *"keep browser open"*) are decoupled from web page actuation, preventing them from leaking into the plan as spurious button clicks.

---

## 3. Real-Time Affordance Perception

Interactive elements and tools are discovered dynamically from the live page via the Chrome DevTools Protocol (CDP) in `<3ms`:

### 3.1 WebMCP Discovery
The agent checks for exposed WebMCP capabilities:
- `window.navigator.modelContext.getTools()`
- `window.document.modelContext`
- Declarative forms with `toolname` attributes (`<form toolname="...">`)

When WebMCP tools are available, the agent invokes them directly via typed JSON RPC calls, bypassing DOM traversal.

### 3.2 Visual DOM Affordances
When WebMCP is not present, the agent extracts visual affordances:
- Interactive elements: `button`, `a`, `input`, `textarea`, `select`, `[role=button]`, `[role=checkbox]`, `[role=tab]`, `[role=menuitem]`
- Semantic labeling: Icon-only buttons (e.g., SVG checkmarks, trash cans) are enriched with parent table row context (e.g., `Cancel order 3491f364`).
- Document height scanning: Scans down to 8,000px to detect table items and pagination controls below the initial viewport fold.

### 3.3 Universal Form Input Decoupling & Multi-Field Binding
To support diverse real-world tasks beyond pure search (contact forms, registrations, profile settings, feedback):
- **Decoupled Input Routing**: Distinguishes keyword search bars from general form fields (`name`, `email`, `phone`, `address`, `message`, `subject`, `password`).
- **Flexible Field Matching**: Automatically bridges hyphen, underscore, and space variations (`full_name` ↔ `full-name` ↔ `Full Name`) and queries `get_by_label()` and `get_by_placeholder()`.
- **Subtask Value Binding**: Honors explicit input values provided in subtasks or bound slots without accidentally overwriting them.

### 3.4 Transient Overlay & Dialog Dismissal
- Passively dismisses blocking cookie consent banners, newsletter prompts, or e-commerce login dialogs (`✕`, `Close`, `Accept all`) during screen settling, preventing UI blockers without corrupting user goals.

### 3.5 Universal Structured Extraction & Non-Table Content Auditing
- **Dynamic Content & Value Extraction**: Anchors target entity/value extraction to card containers (`article`, `[role='listitem']`, `div[class*='item' i]`, `[data-row]`, `[data-component-type='search-result']`) matching query keywords before falling back to generic page text.
- **Universal Content Auditing**: When checking counts or verifying statuses on sites lacking HTML `<table>` elements (e.g. Hacker News, GitHub, Reddit, card grids), gracefully inspects list items, article cards, and container hierarchies.

### 3.6 True Multi-Tab Browser Orchestration & Domain Alignment
- **Parallel Browser Tabs**: `PlaywrightWebMcpDriver` supports concurrent tab management via `new_tab()`, `switch_tab()`, `get_tabs()`, and `close_tab()`. When multi-site comparison or cart addition tasks run, sites are loaded into separate tabs rather than overwriting each other.
- **Cross-Domain Tab Alignment Gate**: Before every subtask actuation, the agent verifies that the focused browser tab's domain matches the subtask target domain (`on Amazon` vs `on Flipkart`). If mismatched, the driver auto-switches focus in `<5ms` to the matching open tab, eliminating cross-site execution drift.
- **Non-Existent Product & Accessory Trap Protection**: Prevents falling back to accessories (cases, covers, cables) when a user requests a core product (phones, laptops, watches). If all discovered items are accessories or 0 listings exist, the agent triggers interactive disambiguation or logs a clean audit finding rather than clicking compare checkboxes or buying phone covers.

---

## 4. Decision Policy Layer (Laya MLX)

The decision loop is powered by **Laya MLX** (`aac6fef/laya-mlx`), a non-autoregressive decision model (~421M parameters) running on Apple Silicon Metal GPU.

### 4.1 Token Budget & Candidate Selection
Laya evaluates options in a single forward pass with a strict 192-token budget for candidate choices (`head_max_len = 192`):
- Candidates are curated to **<= 12 options** (WebMCP tools, matching navigation links, matching action buttons).
- **Negative Sink (`"other"`)**: Every choice set includes `"other"` to absorb probability mass when the target action is not present on the current screen:
  ```python
  "other": "None of the other options fits the active step or the required element is not on this screen"
  ```

### 4.2 State Representation
To prevent attention bleed across future goals, the input state includes only the active subtask and immediate screen summary:
```text
Active Objective: <current_subtask>
Current URL: <url>
Visible Screen Elements: <concise_element_summary>
```

### 4.3 Confidence Gating & Local Screen Arbitration
- **Metal Fast Policy**: When decision confidence exceeds `0.85` on a clear affordance, the action is dispatched immediately in `<15ms`.
- **Local LLM Screen Arbitration**: When confidence is below `0.85`, negative option `"other"` is chosen, or candidates are ambiguous:
  - The agent passes the active subtask, page title, URL, and visible affordances to the local LLM arbitrator (`qwen2.5`).
  - **Universal Action Blocker Detection (`action_blocked`)**: If visible screen elements indicate that the requested action is blocked (e.g. product is unreleased or out of stock with *"Notify Me"* / *"Currently Unavailable"*, or requires login), the arbitrator returns `action_blocked` with the physical reason.
  - The agent records an `unfulfillable` finding in `extracted_data`, logs the blocker in Markdown/HTML reports, and advances to the next subtask without hallucinating button clicks or falling back to navbar links.
- Mutation actions (e.g., `logout`, `delete`, `submit`) cannot execute unless the active subtask explicitly intends that mutation.

---

## 5. Local Caching & Memory Architecture

Caching is backed by Python's standard library `sqlite3` using Write-Ahead Logging (`PRAGMA journal_mode=WAL;`), stored at `~/.laya/prompt_cache.db`.

```
                        User Request / Subtask
                                  |
                                  v
                  +-------------------------------+
                  | SQLite Cache Lookup (<0.2ms)  |
                  +---------------+---------------+
                                  |
                   +--------------+--------------+
                   |                             |
                 [Hit]                         [Miss]
                   |                             |
                   v                             v
+-----------------------------------+  +-----------------------------------+
| Replay Cached Plan or Macro       |  | Query Local Model / MLX Policy    |
| (Verify live affordance first)    |  | Store canonical result in SQLite  |
+-----------------------------------+  +-----------------------------------+
```

### 5.1 Full-Prompt Cache (`prompt_cache`)
- Computes a deterministic SHA-256 fingerprint from canonicalized JSON payloads:
  $$\text{Key} = \text{SHA256}(\text{Namespace} : \text{Model} : \text{CanonicalJSON})$$
- Normalizes whitespace, sorts dictionary keys recursively, and strips volatile runtime values.
- Supported prompt cache namespaces:
  - `task_planner`: initial confident horizon and subtask decomposition.
  - `progressive_expansion`: live screen affordance subtask expansion.
  - `screen_locator`: fallback visual coordinate resolution.
- Retrieves decomposed plans and expanded subtasks in `<0.2ms` (~25,000x speedup over local LLM generation).

### 5.2 Parent-Scoped Subtask Macro Cache (`subtask_cache`)
- Actions are partitioned strictly by `(origin, route, canonical_intent)`:
  - `origin` (`scheme://netloc`): Ensures actions from one host never execute on another.
  - `route` (`path`): Isolates identical labels across different pages (e.g., "Submit" on `/login` vs `/checkout`).
  - `canonical_intent`: Normalized token signature stripping functional stop words.
- **Live Affordance Verification**: Before any cached macro is executed, `verify_macro_affordance` checks that the target element or tool exists and is interactive on the live screen. If missing, the macro is evicted immediately.

### 5.3 Self-Healing Eviction on User Feedback
When a conversational turn indicates an outcome discrepancy or negative feedback (e.g., *"order was not placed"*, *"wrong item selected"*):
1. The turn is classified as `feedback_correction`.
2. All cached macros for that `(origin, route)` are evicted (`invalidate_route_macros`).
3. Recent prompt cache keys across `task_planner`, `screen_locator`, and `progressive_expansion` are purged (`invalidate_recent_prompts`).
4. The planner enters `remediation` mode: it conducts a live state audit and forbids extracting quantity values from complaints.

### 5.4 Guarded Subtask Macro Advancement Gate
- **Advancement Precondition**: To prevent repetitive action doom loops, the engine enforces a strict advancement gate: macros are written to `subtask_cache` **only after the subtask index actually advances** and the step transition passes verification.
- **Immediate Eviction on Failure**: If an executed action fails, stagnates, or produces an assertion error, it is recorded in `failed_actions` (preventing repetition on the current subtask) and any existing macro entry for `(origin, route, subtask)` is immediately invalidated.

### 5.5 Cache Inspection & Reset
- **Inspect**:
  ```bash
  sqlite3 ~/.laya/prompt_cache.db "SELECT origin, route, canonical_intent, hit_count FROM subtask_cache;"
  ```
- **Clear**:
  ```bash
  rm -f ~/.laya/prompt_cache.db*
  # Or within interactive chat:
  laya > clear cache
  ```

---

## 6. Multi-Turn Conversational Memory & Closed-Loop Subtask Verification

To prevent context window inflation on local models while maintaining robust multi-turn continuity:

1. **Active Browser & Tab Coordination Across Turns**:
   - `shared_driver` preserves live Chromium instances, multiple open tabs, and authenticated sessions across conversational turns without closing the browser.
   - When a follow-up turn is initiated, the agent introspects live browser tabs (`get_tabs()`) and active URLs, injecting them into the planner's session context (`open_tabs`).
   - The planner recognizes already loaded domains and schedules high-speed tab switches (`browser_switch_tab`) rather than opening redundant tabs or re-navigating from scratch.
   - If `driver.start()` is invoked on an active shared session with an already loaded URL, re-navigation is bypassed to protect the active workspace.

2. **Closed-Loop Subtask Verification & Action Fulfillment**:
   - Subtasks are classified into text entry demands vs. operational actions (e.g. `add_to_cart`, `buy`, `order`, `cancel`, `approve`, `switch_tab`).
   - The presence of target entity names in subtasks does not falsely restrict operational actions from advancing.
   - When an action semantically fulfills the subtask objective with verified `success: True`, the subtask immediately advances.
   - **Input-Demanding Subtask Guard**: If a subtask specifies text entry/typing, intermediate click actions (e.g. focusing an element, clicking icons, or dismissing interstitials) are strictly blocked from advancing the subtask until the input is filled (`action_did_input`).
   - **Universal Tab Management Primitives**: Tab lifecycle actions (`switch to tab <N>`, `open new tab`, `close tab`) are executed directly via driver-level primitives (`switch_tab`, `new_tab`, `close_tab`) without falling through to on-page DOM clicks.

3. **Subtask Anti-Stagnation & Cache Eviction Guardrails**:
   - Each subtask maintains an internal step budget (`_subtask_step_counts`). If an action stagnates or fails, the agent isolates the fault and invalidates recent `screen_locator` prompts (`invalidate_recent_prompts`) to prevent replay doom loops.
   - At the beginning of each conversational turn, transient exploratory prompts are automatically evicted from `prompt_cache.db`.
   - **Word-Boundary Query Matching**: Screen variant search candidate extraction uses word-boundary token matching (`\b{token}\b`) to eliminate false substring overlaps across unrelated products.
   - **Multi-Site Audit Host Deduplication**: Comparative audits group and deduplicate findings by unique host name, preventing duplicate slot keys for the same website from comparing against itself.

4. **Semantic Session State (Persistent $O(1)$ Footprint)**:
   - Maintains a compact dictionary across turns:
     - Authentication credentials: `{"username": "...", "password": "..."}`
     - Open browser tabs, active route, and target URL
     - `last_goal` summary string
     - Bounded sliding window: `history[-5:]`
   - Total LLM prompt overhead remains compact and constant regardless of turn count.

---

## 7. High-Speed Batch Mutation Loops

For batch operations (*"cancel all open orders"*, *"approve all pending"*):
- The agent initiates an in-step iteration loop locating all matching elements via Playwright.
- Executes batch actions in a single subtask step (<1s total duration) rather than consuming 1 step per row.
- **State Verification Assertion Gates**: Directly verifies completion via DOM assertions (`table tr:has-text('PENDING').count() == 0`) before advancing to subsequent subtasks.

---

## 8. Autonomous Data Inspection & Auditing

For quantitative check goals (*"check if there are any pending orders and include count in the report"*):
1. Queries WebMCP read endpoints (`get_my_orders`, `get_products`) or falls back to live DOM table inspection (`table tr:has-text(...)`).
2. Calculates verified counts.
3. Records findings in `agent.extracted_data`.
4. Outputs verified metrics to the CLI receipt and execution reports under `Extracted Findings & Data Audit`.

---

## 9. Telemetry & Execution Reporting

Every execution session automatically produces dual verification reports:
- **Interactive HTML Dashboard (`reports/execution_report.html`)**: Responsive dark-mode dashboard with executive status badges, data audit tables, step-by-step latency telemetry, masked credentials, and embedded visual screenshots.
- **Structured Markdown Report (`reports/execution_report.md`)**: Audit trail detailing each step, action selected, confidence %, tool return data, and visual screenshot proof.
- **Screenshot Modes**: Configurable via `--screenshot-mode` as `major_steps` (default), `every_step`, or `none`.

---

## 10. Closed-Loop Engineering & Adaptive Failure Recovery

To prevent endless loops, fatal crashes, and stagnant execution when a physical DOM action, runtime exception, or assertion fails:

### 10.1 Step-Level Fault Isolation & Zero-Crash Execution Guarantee
- **Individual Step Boundaries**: Every step in the execution loop is isolated in its own exception boundary. A runtime error, element interception, network timeout, or unexpected DOM exception on a single step **never terminates the overall session**.
- **Transparent Audit Event Logging**: When a step encounters an error, the agent logs an `AgentStepEvent` with the exact error details, captures a visual screenshot, invalidates stale route caches, and invokes Loop Engineering self-healing.
- **Fail-Safe Thresholds**: The agent only halts if unrecoverable fatal conditions persist (e.g. browser process terminated or $\ge 5$ consecutive unrecoverable crashes).

### 10.2 Dynamic Self-Healing via Local Models (LLM & Screen Perception)
- **Local Model Remediation (`remediate_plan_from_screen`)**: When an action fails or assertion gate rejects a state transition, the agent passes the failure reason, active URL, page title, and live DOM affordances to the local planner (`qwen2.5:3b` / `qwen2.5:7b`).
- **Dynamic Re-Routing**:
  - If alternative interactive affordances exist on the live screen (e.g. search bar, alternate button, pagination link), the LLM generates a revised concrete subtask on the fly.
  - If a particular website or subtask is blocked (e.g. Captcha, anti-bot barrier, missing item), the agent records a truthful audit finding and **advances to the next independent phase of the overall goal** (e.g. proceeding to the second website in a comparison task) rather than aborting.

### 10.3 Proactive & Reactive Popup / Dialog Dismissal
- **Proactive Settlement Dismissal**: During `wait_for_settled()`, the browser engine continuously scans for and dismisses blocking dialogs, cookie consent banners (`Accept all`, `Accept cookies`), newsletter promotions (`Not now`, `Maybe later`, `Skip`), and e-commerce login walls (e.g. Flipkart's `✕` login modal).
- **Universal Modal & Escape Key Clearance**: If modal dialogs or backdrop overlays (`[role='dialog']`, `[aria-modal='true']`, `.modal.show`) are active, the driver issues native `Escape` key commands and removes pointer-blocking backdrops.
- **Reactive Click & Fill Retries**: If a native Playwright `click()` or `fill()` is intercepted or blocked by an unexpected popup appearing dynamically, the driver automatically triggers overlay dismissal and re-attempts the action seamlessly.

### 10.4 Downstream Subtask Preservation During Plan Remediation
When a subtask fails and Loop Engineering remediates the plan from the live screen, the planner and orchestrator compute:
```python
preserved_remaining = [
    st for st in remaining
    if not any(st.lower() in alt.lower() or alt.lower() in st.lower() for alt in altered_tasks)
]
self.subtasks = self.subtasks[:self.current_subtask_idx] + altered_tasks + preserved_remaining
```
This guarantees that recovering from a step failure on an initial site (e.g. Amazon) never truncates or drops downstream subtasks (e.g. navigating to Flipkart, inspecting prices, and generating cross-site comparative findings). Furthermore, comparative and audit subtasks are protected against accidental single-domain navigation redirects.

---

## 11. QA Architecture & Web Agent Benchmark Taxonomy

To benchmark Laya Browser Agent against modern autonomous web agent standards (WebArena, VisualWebArena, Mind2Web, BrowserGym), the architecture implements structural defenses against the 5 primary web agent failure modes:

| Failure Mode (Benchmark Taxonomy) | Industry Vulnerability | Laya Architectural Defense |
| :--- | :--- | :--- |
| **1. Premature Termination & Goal Drift** | Agents terminate before multi-step goals complete or wander off into unrelated routes. | **Progressive Horizon Decomposition + Sequential Subtask Tracking**: Anchors high-level intent while only expanding subtasks confident from the current screen. |
| **2. Element Grounding & Form Omissions** | Checkboxes, radio buttons, and native selects missed due to shallow tag scanning. | **Multi-Modal Affordance Extraction**: Discovers `<input type="checkbox/radio">`, custom ARIA comboboxes, and native `<select>` fallback to `value` attributes. |
| **3. Context Poisoning & Error Cascades** | One failed step repeats 25 times in a doom loop, exhausting all tokens and steps. | **Closed-Loop Remediation Gate**: Detects repeat subtask failures ($\ge 2$), introspects the live DOM, replans or logs an unfulfillable finding, and advances. |
| **4. Blind Macro Replay & Stale Cache** | Replaying actions cached before a layout change or ignoring user corrections. | **Live Affordance Verification (`verify_macro_affordance`) + Self-Healing Feedback Eviction**: Invalidation of `(origin, route)` macros on discrepancy reports. |
| **5. Hallucinated Success & Data Dropping** | Claiming a count or check was performed without inspecting the actual DOM table. | **Autonomous Data Inspection Subtasks**: Queries WebMCP read tools or executes table cell scans, recording findings in `agent.extracted_data` and dual reports. |

---

## 12. Codebase Modularization & Anti-Bloat Architecture

To ensure codebase scalability, maintainability, and prevent monolithic fragility, the architecture strictly adheres to a **500–600 line limit per module** and Single Responsibility Principle (SRP):

```
laya_browser_agent/
├── agent.py         # Core Agent Orchestrator: step loop, lifecycle & step-baked verification
├── reporting.py     # Standalone Reporting: truthful status calculation, Markdown & HTML dashboards
├── resolver.py      # Schema & Parameter Resolver: WebMCP tool binding, slot mapping & input aliases
├── laya_engine.py   # Metal GPU Decision Model: MLX policy inference & candidate shortlisting
├── affordance.py    # Affordance Perception: CDP candidate extraction, search input detection
├── expansion.py     # Progressive Horizon: deferred intent screen grounding & failure remediation
├── extractor.py     # Intent & Horizon Planner: progressive discovery & machine-actionable slots
├── browser.py       # Playwright CDP WebMCP driver & deep DOM perception
├── cache.py         # SQLite WAL prompt & parent-scoped subtask macro caches
├── dialogue.py      # Multi-turn conversational dialogue classifier & self-healing cache eviction
└── variants.py      # Universal variant extraction, specification parsing, ranking & disambiguation
```

---

## 13. Truthful Run Status Evaluation & Subtask Integrity

To eliminate false-positive completion reports where dashboards stamped `COMPLETED (SUCCESS)` even when steps failed or entities were missing:

### 13.1 Deterministic Run Status Evaluation (`evaluate_run_status`)
Execution outcomes are evaluated through `evaluate_run_status(agent, execution_error)` across deterministic criteria:
1. **Unhandled Execution Errors**: Network timeouts, Playwright crashes, or unhandled exceptions return `failed`.
2. **Unfulfillable Structured Findings**: If `agent.extracted_data` records an `unfulfillable` status (e.g. target entity not found after multi-level remediation), the run is evaluated as `unfulfillable`.
3. **Subtask Horizon Completion**: If fewer subtasks completed than were planned (`completed_subtask_idx < len(subtasks)`), the run is evaluated as `incomplete` (`⚠️ INCOMPLETE (X of Y subtasks finished)`).
4. **Mandated Goal Data Check & Price Comparison Fulfillment**: If the goal specified quantitative checking or price comparison across multiple sites, a completed comparison finding across target sites is required. If prices cannot be extracted from the target portals, the status is evaluated as `unfulfillable` (`⚠️ UNFULFILLABLE`).
5. **Clean Completion**: Strictly stamped as `success` (`✅ COMPLETED (SUCCESS)`) when all planned subtasks completed without unhandled errors, unfulfillable findings, or omitted comparison audits.

### 13.2 Subtask Marker Fidelity
Subtasks in both Markdown reports and the HTML dashboard display truthful execution state badges:
- `[✓]` — Successfully executed and verified.
- `[✗]` — Failed or unfulfillable with logged reason.
- `[○]` — Pending or omitted due to upstream failure.

---

## 14. Machine-Actionable Automation Planning

Sequential plans must be formed for **browser automation**, not human conversational browsing:

1. **Concrete Browser Operations**:
   - Vague instructions like *"Go to products page to search for iPhone 18 Pro Max"* are decomposed into discrete, actionable steps:
     - `Enter 'iPhone 18 Pro Max' into search input box and submit`
     - `Inspect search results and note the price of 'iPhone 18 Pro Max'`
2. **Search Input Field Resolution**:
   - The affordance extractor detects search fields (`twotabsearchtextbox`, `field-keywords`, `q`, `type="search"`, `role="searchbox"`) and labels them with high-priority search metadata.
   - The input parameter resolver maps `search`, `query`, and `product` slots directly to the target search input, preventing false slot collisions (e.g., matching `"q"` to quantity).
3. **Dynamic Result Data Extraction**:
   - Price and metric inspection subtasks inspect table cells and matching DOM elements (`span.a-price-whole`, `div[class*='price']`) and record structured values into `agent.extracted_data`.

---

## 15. The Laya Decision Model Ecosystem: Non-Autoregressive System 1 on Metal GPU

Unlike generative LLMs or vision-language models that require massive compute and token-by-token generation:

1. **Non-Autoregressive Decision Architecture**:
   - Powered by `aac6fef/laya-mlx` (Metal GPU port of `convaiinnovations/laya` / `cklxx/laya-browser`), built on a bidirectional encoder backbone (ModernBERT / mmBERT, ~322M–421M parameters).
   - Evaluates page context and candidate affordances in a **single forward pass** (<15ms per step).
   - Generates calibrated discrete probabilities for choices without token generation overhead.
2. **DOM-Based Affordances vs. Vision Coordinate Drift**:
   - Vision models (UI-TARS, ShowUI) predict $(x, y)$ pixel coordinates that drift with screen zoom, device pixel ratio, and dynamic modal overlays, and require 2,500ms–6,000ms per step.
   - Laya operates directly on indexed DOM affordances and WebMCP tools, taking <15ms, utilizing ~500MB VRAM, and executing native Playwright element actions.

---

## 16. Universal Machine & Model Portability & Telemetry

Laya Browser Agent is engineered to run seamlessly across any capable local hardware architecture:

1. **Decoupled Platform Acceleration & Graceful Fallback**:
   - Apple Silicon Metal GPU acceleration (`laya_mlx`) is dynamically imported and initialized. On Linux (x86_64, ARM64 with CUDA/CPU) or Windows (WSL2/DirectML/CPU) where `mlx` is not present, the agent gracefully falls back to CPU execution and local model runners (Ollama / Llama.cpp / vLLM) with zero import crashes.
2. **Dynamic Local Model Discovery**:
   - Rather than coupling to a hardcoded local model, the agent queries `/api/tags` on the local runner and auto-selects the best installed instruction model if the default (`qwen2.5:3b`) is missing, respecting the `OLLAMA_MODEL` environment variable.
3. **Cross-Platform User-Agent & OS Independence**:
   - Dynamically constructs realistic desktop User-Agent strings matching the host platform (`platform.system()`) across Darwin, Linux, and Windows NT.
   - All file storage and WAL database paths use standard POSIX and Windows path resolution (`os.path.expanduser`).
4. **Minimalist Session Telemetry**:
   - The agent tracks prompt tokens (`prompt_eval_count`), completion tokens (`eval_count`), total tokens, cache reads/writes, and saved latency.
   - Rendered in a clean, minimalist monospace table without over-beautification or redundant graphical elements.
5. **Content-Hash Screenshot Deduplication**:
   - Uses MD5 content hashing across captured step frames to suppress redundant screenshots across consecutive steps and eliminate duplicate final execution proofs.

---

## 17. Universal Variant Perception, Cross-Site Anchoring & Interactive Disambiguation

When executing repeatable, comparative, or purchasing tasks (e.g., multi-site product comparisons, flight bookings, apparel add-to-cart), web search listings frequently display diverse variants (different storage tiers, sizes, colors, travel classes) alongside sponsored ads. To guarantee consistent, truthful, and repeatable automation:

### 17.1 Universal Specification Parsing (`variants.py`)
- **Capacity & Hardware Storage**: Detects `256 GB`, `512 GB`, `1 TB`, `2 TB`, `16 GB RAM`.
- **Apparel & Shoe Sizing**: Detects `Size 9`, `UK 8`, `US 10`, `XS`, `S`, `M`, `L`, `XL`, `XXL`.
- **Finish & Colors**: Extracts `Desert Titanium`, `Natural Titanium`, `Space Black`, `Midnight`, `Starlight`.
- **Travel Classes**: Detects `Economy`, `Premium Economy`, `Business`, `First Class`, `Non-stop`.
- **Packaging & Units**: Identifies `Pack of 2`, `500g`, `1L`.

### 17.2 Accessory & Sponsored Outlier Filtering
- **Accessory Penalization**: When searching for core products (e.g., smartphones, laptops, shoes), peripheral items (cases, covers, screen protectors, chargers, cables) are detected via `is_accessory_text` and heavily penalized (-120.0 score) unless the user explicitly requested accessories.
- **Sponsored Ad De-prioritization**: Sponsored and promoted cards at the top of search result listings often push higher-margin or maxed-out variants (e.g. 2 TB editions). Organic base retail results receive an automatic +50.0 boost while sponsored cards are deprioritized.

### 17.3 Cross-Site Apples-to-Apples Consistency Anchoring
- When comparing across multiple domains (e.g., Amazon.in vs Flipkart.com):
  1. The agent selects or clarifies the target variant on Site 1.
  2. The resolved specification is anchored in `agent.extracted_slots["selected_variant"]` (e.g., `"256GB"`).
  3. When navigating to Site 2, `extract_screen_variants(..., target_variant="256GB")` awards a +200.0 score bonus to matching hardware variants, ensuring both platforms compare identical models and eliminating false price discrepancies.
  4. If both platforms price the anchored variant identically, the audit truthfully summarizes: `"Identical price on www.amazon.in and www.flipkart.com (₹1,79,900) for variant '256GB'"`.

### 17.4 Interactive Disambiguation & Pre-Cart Swatch Selection
- **CLI Disambiguation Prompt (`cli_disambiguation_callback`)**: If multiple distinct variants exist on screen and no variant was specified by the user, the agent prompts the user in interactive mode with numbered options (title, spec, price) before proceeding. In headless/CI mode, it defaults cleanly to the highest-scoring organic base variant.
- **Product Page Swatch Selection**: When about to click "Add to Cart" or "Buy Now", `find_page_variant_buttons` discovers interactive size/variant buttons (e.g. shoes sizes `8`, `9`, `10` or storage swatches) and clicks the selected variant button before executing checkout.

### 17.5 Screen Byte Hash Deduplication
- `PlaywrightWebMcpDriver.take_screenshot` computes `hashlib.md5(raw_bytes).hexdigest()`.
- If consecutive steps or the final execution state produce byte-for-byte identical frames (e.g. step 4 inspects data and step 5 finishes on the same page), the driver reuses the existing screenshot path and suppresses writing duplicate 1.8MB files to disk.
- Markdown and HTML execution dashboards verify image hashes against `seen_hashes` to suppress redundant gallery cards and repeated final visual verification proofs.

---

## 18. Local LLM Client Interface, Trajectory Logging & Benchmark Harness

To stabilize testing, eliminate flakiness, and make results reproducible:

### 18.1 Unified LLM Client Interface (`laya_browser_agent.llm`)
- **Protocol Abstraction (`LLMClient`)**: All System 2 planning, progressive expansion, remediation, and visual locator calls route through a single runtime-checkable `LLMClient` protocol (`chat_json`).
- **Thinking-Tag Cleaning (`strip_thinking_tags`)**: Seamlessly scrubs `<think>...</think>` internal reasoning blocks produced by modern reasoning models (e.g. Qwen 3, Qwen 3.5), preventing JSON parser corruption.
- **Dynamic Local Model Discovery (`resolve_local_model`)**: Auto-discovers installed models via local runner endpoints (`/api/tags`), prioritizing installed high-capacity models (`qwen3.5:latest`, `qwen2.5:7b`, `qwen3:14b`) and respecting the `OLLAMA_MODEL` environment variable.
- **Deterministic Replay Client (`RecordedLLMClient`)**: Enables 100% offline, zero-cost, deterministic self-check execution (T1 suite) without network or model inference latency. `strict=True` raises on any prompt with no recording, to expose test gaps.
- **Thinking off by default**: `OllamaClient` sends `think=false`. qwen3.5 with thinking took ~22 s per structured-JSON call versus ~1 s without, which dominated every fallback step.
- **Call Counting**: Both clients expose `llm_call_count`; `LayaAgent` derives per-step `llm_calls` / `llm_fallback` and `decision_ms` from it, so `laya_share` and LLM calls per task are measured rather than guessed from log text.
- **Model resolution**: `resolve_local_model(preferred, base_url)` honours `OLLAMA_MODEL`, then `preferred` only if installed, then the best installed model; passing a URL as the first argument raises `ValueError`.

### 18.2 Trajectory Logger (`laya_browser_agent.trajectory`)
- **Structured Step Logging**: Emits granular JSON lines into `$LAYA_HOME/trajectories/<run_id>.jsonl` (default `~/.laya`) capturing state, URL, candidates, chosen action, confidence, latency, engine info, LLM fallback flag and the raw tool result. `verified` is `null` (unknown) until Phase 1 adds post-condition verification.
- **Sensitive Credential Redaction (`mask_sensitive_values`)**: Recursively masks values of keys whose word tokens include a secret word (`password`, `token`, `secret`, `api_key`, ...; `author` is not matched) and inline `user/Password` pairs in strings, including the logged goal and subtask.
- **Flywheel Training Substrate**: Trajectories form the foundational dataset for off-line calibration, hard-negative mining, and DAgger fine-tuning.

### 18.3 Practice Sites & Optional Task Runner (`bench/`)
- **Practice sites**: `shop` (WebMCP declarative tool forms that reply via `respondWith`), `admin` (plain DOM table) and `forms` (multi-step form with an overlay), served by `python -m bench serve` with `/__state` and `/__reset` APIs.
- **Optional runner**: `python -m bench run` executes `bench/tasks/*.yaml` and checks the final app state (`state`), an independent WebMCP query (`webmcp`, row-scoped with `where` / `id_prefix`), a visited URL (`url`) or extracted findings (`answer`). It is a convenience for spotting regressions, not a release gate.
- **Run safety**: `LayaAgent(max_seconds=...)` stops a run at the next step boundary (`--task-timeout`). WebMCP tool calls are time-bounded (5 s native, then the declarative form fallback), so one unresponsive tool cannot freeze a step.
- **Quality gate (`make check`)**: syntax compilation, zero-warning flake8 and the offline test suite.
