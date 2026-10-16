# 🏛️ Laya Browser Agent: Technical Architecture Specification

> **A 100% Local, Sub-Second Autonomous Web Agent Powered by Apple Silicon Metal GPU Decision Models, Dynamic Intent Decomposition, and WebMCP**

---

## 1. Architectural Philosophy: Beyond the Token & Cost Trap

Traditional autonomous browser agents (built on Claude 3.5 Sonnet, GPT-4o, or cloud vision models) suffer from three critical bottlenecks:
1. **Extreme Financial Cost**: Every step consumes 15,000–45,000 tokens of raw HTML, DOM trees, and high-resolution screenshots, costing **$0.50 to $2.50 per workflow**.
2. **High Latency**: Cloud roundtrips and token-by-token generation take **4 to 12 seconds per step**, resulting in sluggish 30-to-60-second browser workflows.
3. **Context Collapse & Hallucination**: Flooding 50,000 tokens of noisy HTML into an LLM causes lost-in-the-middle phenomena, missed buttons, and hallucinated coordinates.

**The Laya Browser Agent Paradigm:**
Instead of brute-forcing massive HTML dumps into giant cloud LLMs, Laya Browser Agent decomposes web automation into a **3-tier, zero-cost, local-first pipeline**:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        USER / TERMINAL CLI                             │
│       laya-agent "http://localhost:3000" --task "<Human Request>"      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ TIER 1: Natural Language Slot & Intent Planner (Ollama llama3.1:8b)    │
│    • Decomposes arbitrary goals into sequential subtasks               │
│    • Extracts parameter slots (credentials, queries, quantities, IDs) │
│    • 100% generic: zero pre-programmed intent tables or verb lists     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ TIER 2: Live Affordance Perception (In-Memory Chrome DevTools Protocol)│
│    • Introspects WebMCP tools (navigator.modelContext, <form toolname>)│
│    • Discovers interactive DOM elements (buttons, inputs, links, rows) │
│    • In-memory CDP evaluation in < 3ms (zero stale disk file caches)   │
└───────────────────▲───────────────┬────────────────────────────────────┘
                    │               │ Candidates Map
                    │ State Update  │ (Tools + Elements)
                    │               ▼
┌───────────────────┴────────────────────────────────────────────────────┐
│ TIER 3: System 1 Decision Policy (Laya MLX on Metal GPU in ~15ms)      │
│    • Evaluates active subtask against on-screen affordances            │
│    • Curated criteria <= 12 options fitting 192-token head budget      │
│    • Mandatory "other" negative sink prevents probability spillage     │
│    • Calibrated RLCD confidence gating with local LLM vision fallback  │
└───────────────────▲───────────────┬────────────────────────────────────┘
                    │               │ Winning Action + Coords / Params
                    │ Execution     │
                    │ Feedback      ▼
┌───────────────────┴────────────────────────────────────────────────────┐
│ Playwright Universal Actuator (WebMCP RPC & Visual DOM Coordinate)     │
│    • WebMCP RPC tool dispatch (native JSON callTool)                  │
│    • Precise coordinate clicks with auto-scroll down to 8000px         │
│    • DOM form input typing and change event dispatch                   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Dynamic Intent Handling: Why We Do NOT Maintain a Hardcoded List

A frequent question in web automation is:
> *"Will we maintain a huge list of intents (e.g. buy, cancel, compare, check stock, export invoice)? What happens when a brand-new intent comes in?"*

**We maintain ZERO hardcoded intents or synonym dictionaries.** In real-world enterprise applications, user intents and web capabilities are infinite:
- *"Compare iPhone 16 and Samsung S24 specs and see which is in stock"*
- *"Export all Q3 invoice PDFs for orders above $500"*
- *"Cancel all my open orders that are in pending state and logout"*
- *"Toggle dark mode and update notification email"*

A hardcoded enum or dictionary of intents (e.g. `INTENTS = {"buy": ..., "cancel": ...}`) or synonym mapping (`synonyms = {"buy": ["order", "purchase"]}`) is an anti-pattern that fails as soon as an application introduces new wording or unforeseen workflows.

### How Arbitrary Goals Flow Dynamically:

1. **Zero-Shot Subtask Decomposition (Tier 1)**:
   - When the user provides an instruction, local Ollama (`llama3.1:8b`) receives the natural language instruction.
   - Using open-ended semantic reasoning, it decomposes the prompt into sequential subtasks:
     1. Search for Product A
     2. Check availability and price for Product A
     3. Search for Product B
     4. Check availability and price for Product B
     5. Compare the two and summarize results
   - No hardcoded intent catalog is consulted.

2. **Universal Affordance Extraction (Tier 2)**:
   - On the target web page, `extract_page_candidates` does not look for predefined verbs. It discovers whatever interactive affordances *that specific page* provides:
     - Registered WebMCP tools: e.g. `compare_products`, `check_inventory`, `get_product_details`
     - Visual DOM elements: buttons like `<button>Compare</button>`, `<button>Check Stock</button>`, search inputs, dropdowns.
   - It builds an ad-hoc menu of options available *on this screen right now*.

3. **System 1 Zero-Shot Decision Policy (Tier 3)**:
   - Laya MLX (`aac6fef/laya-mlx`) evaluates the active subtask against the candidate options on screen in a single forward pass (~15ms on Apple Silicon Metal GPU).
   - Candidate descriptions are phrased as natural action semantics (e.g. `"Cancel order 3491f364"`, `"Navigate to Products page"`).
   - If confidence is below the safety threshold (0.85) or `"other"` is selected, the local LLM Router acts as a fallback safety valve.

---

## 3. Real-Time Affordance Perception (Why Disk Site Maps Were Eliminated)

In early designs, route interactive elements were serialized to disk JSON files (e.g. `.site_maps/<app_key>.json`). In production, this proved to be an anti-pattern:
1. **Stale Dynamic State**: Modern web applications frequently change DOM elements (e.g. order cancel buttons disappear once clicked, rows update from `PENDING` to `CANCELLED`, accordions expand/collapse). Caching button coordinates and dynamic entity IDs on disk served stale elements.
2. **In-Memory CDP is Instantaneous**: Extracting live interactive elements via Playwright / Chrome DevTools Protocol (`querySelectorAll` + `window.modelContext`) takes **< 3ms in memory**. Disk caching added serialization overhead and cache-invalidation bugs without performance benefit.
3. **Session-Scoped Route Telemetry**: Discovered route affordances are retained in memory solely for the execution report (`reports/execution_report.md`), with zero disk serialization.

---

## 4. Laya MLX Decision Architecture & Prompt Formatting

Laya is an open-source, non-autoregressive **System 1 decision model** (built on a ModernBERT-large backbone, ~421M parameters) developed by Convai Innovations. It evaluates typed questions over an input `state` in a single forward pass without token-by-token generation.

### Internal Token Budget & Layout
Under the hood, Laya packs the question, options, and state into a rigid sequence:
```text
[CLS] choice question: <instructions> [SEP] [MASK] <label_1>: <crit_1> [MASK] <label_2>: <crit_2> ... [SEP] <state> [SEP]
```

Laya has a fixed **192-token budget** for the question prefix and all option descriptions combined (`head_max_len = 192`):
- If an agent dumps 30–50 raw DOM elements into `criteria`, Laya's internal tokenizer truncates each option down to **4 tokens**, destroying semantic meaning.
- Laya Browser Agent curates candidates to **<= 12 focused choices** (tools, matched navigation links, matched action buttons, and general affordances).

### Mandatory Negative Sink (`"other"`)
Because Laya computes softmax across choices, the probabilities must mathematically sum to 1.0. If all visible options are irrelevant to the active step, the absence of a negative option forces probability mass onto unrelated buttons (such as `tool_logout_user`).

Every choice question in Laya Browser Agent strictly includes:
```python
"other": "None of the other options fits the active step or the required element is not on this screen"
```

### Unpolluted State Representation
Bidirectional encoder models attend to all tokens in the `state`. If `state` includes the entire user prompt (e.g. *"login, cancel orders, and logout"*), the model attends to the future verb *"logout"* prematurely. Laya Browser Agent passes clean context focusing on the **active subtask** and current screen context:
```python
state = (
    f"Active Objective: {subtask}\n"
    f"Current URL: {current_url}\n"
    f"Visible Screen Elements: {visible_summary}"
)
```

### Calibrated Confidence Gating & Guardrails
- **Threshold Gating**: Laya is trained with Reinforcement Learning for Calibrated Decisions (RLCD). When confidence is < 0.85 or `"other"` is selected, the agent falls back to the local LLM vision locator rather than guessing.
- **Session Protection Invariant**: `tool_logout_user` is strictly rejected unless the active subtask explicitly intends to log out.

---

## 5. Deep Table & Row Perception

Modern enterprise web applications (e.g., PostQode Nexus, Jira, ServiceNow) present dynamic data in scrollable tables with hundreds of rows.

1. **Below-the-Fold Row Inspection**:
   - `get_screen_elements()` scans table rows (`tr, [data-row], .card, li`) down to 8000px.
   - Buttons without explicit text (e.g., Lucide SVG icon buttons: checkmarks, cross marks, trash cans) are semantically labeled with row context (e.g., `Cancel 246c2c2e...`, `Approve 7e618872...`).
2. **Auto-Scroll Actuation**:
   - `click_coordinate(x, y)` automatically scrolls the browser viewport to the element's position before executing the click, ensuring Playwright interacts with genuine rendered DOM coordinates.
3. **Dynamic Target Entity Filtering**:
   - When an active subtask targets a specific ID token (e.g. `246c2c2e`), non-matching row buttons are filtered out so distractor rows do not crowd the decision model's choice space.

---

## 6. Execution Reporting & Visual Proof

Every run automatically generates a comprehensive Markdown report (`reports/execution_report.md` by default) containing:
- **Executive Summary**: Status, total time, average decision latency per step, cloud API cost ($0.00).
- **Structured Plan & Entity Resolution**: Extracted parameters with masked passwords (`••••••••`).
- **Step-by-Step Action Audit Trail**: Exact URL, subtask, action chosen, confidence %, latency, and tool return data.
- **Discovered Web Application Affordances**: Interactive capabilities discovered during the session.
- **Visual Verification Proof**: An embedded high-resolution screenshot (`final_execution_state.png`) proving the final state of the application.

---

## 7. Zero Cloud Cost Guarantee

| Component | Technology | Cost |
| :--- | :--- | :--- |
| **Decision Policy** | Laya MLX (`aac6fef/laya-mlx`) on Apple Silicon GPU | **$0.00** |
| **Intent Planner** | Local Ollama (`llama3.1:8b`) on localhost:11434 | **$0.00** |
| **Browser Engine** | Playwright Chromium (Headed / Headless) | **$0.00** |
| **Total Cloud API Spend** | **0 Cloud API Calls** | **$0.00** |
