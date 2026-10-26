# Laya Browser Agent — Roadmap

> Goal: a local web agent that completes most everyday web tasks correctly, with the Laya decision model
> making the bulk of per-step decisions in milliseconds and a local LLM used only where text generation or
> deeper reasoning is genuinely needed. Everything runs on your own machine.

This is an experimental project. Progress is judged by trying real tasks (on the bundled practice sites,
[PostQode Nexus](https://github.com/Sreenuraj/postqode-nexus), and public sites) and checking whether the
agent did the right thing. The optional `bench/` runner can automate that check, but it is a convenience,
not a gate.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done.

---

## 1. Principles

| Principle | Meaning in practice |
| :--- | :--- |
| Local-first | All models run locally (Apple Silicon Metal first, CPU/CUDA fallback). |
| Laya does the heavy lifting | Each per-step decision (which element, which operation, is the step done, is something blocking) is a typed Laya question answered in one forward pass. |
| LLM is a helper, not the loop | The LLM plans the goal once, writes free-text values, and handles the cases Laya is unsure about. |
| Application-agnostic | No site-specific routes, entities, tool names, selectors or verbs in code (see `AGENTS.md`). |
| Truthful | Claiming success when the goal was not achieved is worse than failing. |
| Thin harness | Code perceives the page, asks the models typed questions, executes exactly what they chose, verifies the outcome and records it. It does not branch on task text. |
| Human in the loop when needed | Missing information, irreversible actions, CAPTCHAs/2FA → the agent asks the user. |

---

## 2. Known problems (why the roadmap exists)

Observed on a typical multi-step goal (*log in, order 2 of a product, confirm the order is pending*):

| Problem | Root cause |
| :--- | :--- |
| A multi-field form advanced after filling only one field | Progress advances on "action succeeded", not on the step actually being done |
| Laya picked a wrong element at high confidence | The base checkpoint is used zero-shot and its confidence is uncalibrated (one bucket ships temperature 0.10) |
| Planner turned "2 qty" into "add 2 products" and leaked a search template into unrelated steps | Free-text plans with no validation against the goal |
| A click or tool call counted as success with no proof the order existed | No post-condition check |
| `finish` emitted with unrelated reasoning | No completion gate |
| Several seconds per step | The LLM fallback is the main loop rather than the exception |

Structural issues:

- **Keyword heuristics.** `agent.py`, `extractor.py`, `expansion.py` and others contain long English keyword tuples and a few site-specific selectors/tool names. They are brittle and conflict with `AGENTS.md`.
- **File size.** `agent.py` (~2,000 lines) and `browser.py` (~1,100 lines) exceed the 600-line module limit.
- **Caches** can replay an action that "succeeded" but did not achieve the step.

---

## 3. Background: models and techniques

### Laya decision model

- The base `convaiinnovations/laya` is a fast encoder to specialise. It is weak zero-shot on typed decisions and much stronger after fine-tuning.
- Shipped temperatures are over-confident. Questions with 11+ options fall into the worst bucket, so keep questions to ≤10 options and recalibrate on our own data.
- All options share one head token budget. Use short option strings, and use `predict_shortlist` / `predict_tournament` for large candidate sets.
- **`cklxx/laya-browser`** is a browser-agent head trained on Mind2Web, NNetNav, WebChain and others. It answers operation + target in one pass and loads directly in `laya_mlx`. It is the most promising starting checkpoint, but it tends to stop too early and is not a planner. In quick local probes it clicked Login with empty fields and rarely said "done"; latency was ~75–130 ms per decision.
- `laya-mlx` already ships `predict_shortlist`, `predict_tournament`, `fit_temperature_map`, `fit_abstention_thresholds`, `apply_confidence_gate`, `noul` (yes/no/unknown) questions and `convert`. The agent currently uses only one flat `choice` question.

### Local LLM (planner / value writer)

Any Ollama model works. The agent discovers installed models via `/api/tags` or uses `OLLAMA_MODEL`.
Good candidates are the Qwen 2.5 / 3 / 3.5 families in the 4B–14B range; pick by trying your own goals,
not by leaderboards. Thinking mode is disabled by default because it makes each call much slower.

### Techniques worth adopting

- **Operation + target factorisation**: ask "which operation?" and "which element?" as separate questions.
- **Coarse-to-fine ranking**: shortlist ~40 DOM candidates down to ≤10 before Laya picks.
- **Harness fixes matter as much as the model**: wait for the navigation an Enter/click triggers, add an undo guard for toggles, give the value writer field metadata (placeholder, type, pattern).
- **DAgger-style data collection**: let the agent drive, label the states it visits, retrain.

---

## 4. Target architecture

```
Goal ──► [LLM planner] ──► typed plan: [{op_hint, target, value?, success_criterion}, ...]
                                   │  (validated against the goal)
                                   ▼
             ┌──────────────── step loop (per observation) ─────────────────┐
             │ Observe: DOM/AX candidates + WebMCP tools + page text          │
             │ Laya Q1 (noul)   step done?             ── yes ► advance       │
             │ Laya Q2 (choice) blocker? {none, overlay, login, captcha, …}   │
             │ Laya Q3 (choice) operation {CLICK, TYPE, SELECT, ENTER,        │
             │                   SCROLL, TOOL, NAVIGATE, READ, ASK_USER, DONE}│
             │ Laya Q4 (choice) target among ≤10 shortlisted candidates       │
             │ Calibrated gate per question ─ unsure ► LLM arbiter            │
             │ TYPE/SELECT value ► LLM value writer (or plan slot)            │
             │ Act ► wait for triggered navigation ► verify the criterion     │
             └───────────────────────────────────────────────────────────────┘
                                   ▼
                     Goal check ► truthful report + trajectory log
```

| Harness MAY | Harness MUST NOT |
| :--- | :--- |
| Extract every actionable element and WebMCP tool generically | Choose an element or tool by keyword matching |
| Present them as typed options to Laya; ask the LLM for free-text values | Contain domain nouns, routes, tool names or site selectors |
| Execute the chosen operation exactly | Rewrite the model's choice with overlap heuristics |
| Verify the post-condition the plan declared | Advance or skip steps by keyword |
| Enforce generic safety (ask before irreversible actions, step/time budgets, loop guards) | Hard-code which actions are dangerous by name |

---

## 5. Phases

### Phase 0 — Foundations ✅

- [x] `requirements-dev.txt` + `make check` (py_compile, flake8 gate, offline tests).
- [x] One `LLMClient` interface used everywhere, plus `RecordedLLMClient` for deterministic offline tests.
- [x] Offline test suite is green and deterministic (~15 s, no Ollama needed).
- [x] Trajectory logger: `$LAYA_HOME/trajectories/<run_id>.jsonl` with credentials masked. These logs are the future training data.
- [x] Practice sites in `bench/sites/` (shop with WebMCP, admin table, multi-step form) served by `python -m bench serve`, plus an optional task runner (`python -m bench run`).
- [x] Harness fixes found while testing: WebMCP tool calls are time-bounded, local LLM thinking mode is off, email addresses are not treated as navigation targets.

### Phase 1 — Correct loop (current)

Fix the failure classes in §2 at the root:

- [ ] **Remove hard-coding**: site-specific tool names/routes, entity slots (e.g. `orderId`), site selectors, domain keyword tuples and the `variants.py` domain heuristics. Add `tests/test_no_domain_hardcoding.py` (deny-list grep over `laya_browser_agent/`).
- [ ] **Typed plan**: the planner returns `{op_hint, target, value, success_criterion}` per step via Ollama JSON-schema output. No free-text template rewriting.
- [ ] **Plan validation**: reject steps not grounded in the goal ("repeat previous step"); quantities bind to slots, not to repeated steps.
- [ ] **Progress = verified criterion**: a step completes only when its criterion holds (URL change, field values set, form gone, tool returned data), never just because an action ran.
- [ ] **Reads complete on data**: a WebMCP read or DOM read that returns what the criterion asks for completes the step and records the findings.
- [ ] **WebMCP tools as options**: tools are offered to Laya next to DOM elements; the model chooses, the harness only binds parameters from the JSON schema.
- [ ] **Finish gate**: report success only when every step is verified; otherwise report incomplete.
- [ ] **Harness fixes**: wait for triggered navigation, undo guard for toggles/filters, pass field metadata to the value writer.
- [ ] **Cache safety**: store macros only after verification, keyed on the success criterion too.
- [ ] **Split large files**: `agent.py` → `loop.py`, `progress.py`, `navigation.py`, `verify.py`; `browser.py` → `driver.py`, `perception.py`, `overlays.py` (each ≤500 lines).

**Done when:** the example goal in §2 works reliably, form/wizard tasks on the practice sites fill every field before submitting, and the agent no longer reports success on tasks it did not finish.

### Phase 2 — Let Laya carry the loop

- [ ] Replace the single flat question with the four questions in §4, using short option strings (`[e12] button "Buy" · row: "Wireless Mouse $19"`).
- [ ] Richer state: goal, current step + criterion, URL, title, visible text excerpt, form-field summary, last 3 actions and outcomes.
- [ ] Candidate shortlister (lexical + Laya embeddings) to keep ≤10 options without losing the right one.
- [ ] Try `cklxx/laya-browser` (convert with `laya_mlx.convert`) against the current checkpoint and keep whichever picks the right element more often.
- [ ] Recalibrate with `fit_temperature_map` / `fit_abstention_thresholds` on logged decisions; replace the fixed 0.75/0.85 thresholds with per-question gates.
- [ ] Replace remaining keyword classifiers (subtask kind, mutation intent, dialogue turn type, overlay detection) with Laya questions.
- [ ] LLM fallback only when Laya abstains; every fallback is logged as a training example.

**Done when:** most steps on the practice sites are decided by Laya alone (visible as `llm_fallback: false` in the trajectory log) and the agent feels noticeably faster without getting less accurate.

### Phase 3 — Fine-tune Laya (optional, when there is enough data)

- [ ] `export_training.py`: trajectory JSONL → `laya-train` JSONL (one case per step, all four questions, labels from verified outcomes).
- [ ] Labeling: scripted answers on the practice sites; a larger local LLM as teacher for real-site steps, with manual spot checks.
- [ ] Vary the practice sites (layouts, labels, synonyms, popups) so the model does not memorise one DOM.
- [ ] A few DAgger rounds: agent drives, states are labelled, retrain.
- [ ] Optionally mix in public data (WebChain, Mind2Web, NNetNav) converted to our question format.
- [ ] Train head-only locally for quick iterations; full fine-tune on a free cloud GPU if needed. See `doc/finetune.md`.
- [ ] The exporter excludes credentials and any origins you mark as evaluation-only; a unit test enforces this.
- [ ] Keep a new checkpoint only if it is better on tasks it was not trained on. Expose it via a `--model` option.

### Phase 4 — Planner upgrade (can run alongside Phases 2–3)

- [ ] Compare a few local models on a set of your own goals (plan correctness, valid JSON, speed) and choose a default plus a fast tier.
- [ ] Ollama structured outputs everywhere; remove regex JSON repair.
- [ ] Replan on verified failure after a live page audit (max 2 replans per step).
- [ ] Goal-level check: the LLM judges the collected evidence against the goal before the final status.

### Phase 5 — Real-web robustness

- [ ] Perception: accessibility tree merged with DOM, shadow DOM, same-origin iframes, virtualised lists, below-the-fold elements.
- [ ] Generic overlay/blocker handling via Laya Q2 (cookie banners, newsletter popups, login walls; CAPTCHA → report blocked).
- [ ] Better waits: network idle + DOM quiescence + triggered navigation.
- [ ] Multi-tab and multi-turn flows, including feedback-driven cache eviction.
- [ ] Try read-only tasks on public sites (GitHub, Hacker News, Wikipedia, docs) and note which sites block headless browsers.
- [ ] Linux CPU path (PyTorch Laya fallback) works alongside macOS/MLX.

### Phase 6 — Polish

- [ ] `ASK_USER` as a model-chosen operation: the CLI pauses and resumes the same step with the answer; headless mode reports `needs_user`.
- [ ] Safety: a Laya yes/no question "is this irreversible, does it spend money or send data externally?" forces `ASK_USER` unless the goal explicitly authorises it. Credentials never reach logs, caches or training data.
- [ ] HTML report shows Laya probabilities per question and why the LLM was used.
- [ ] Packaging: `pip install`, a `laya` CLI entry point, pinned model revisions.
- [ ] Docs describe the v2 loop; remove claims not backed by working code.

---

## 6. When does fine-tuning matter?

| Stage | Laya model | Who decides most steps | What to expect |
| :--- | :--- | :--- | :--- |
| Phases 0–1 | Off-the-shelf checkpoint | Local LLM fallback (seconds per step) | More **correct** as the loop fixes land |
| Phase 2 | Best off-the-shelf checkpoint + recalibration | Laya for easy steps, LLM for the rest | Faster; Laya stops acting on wrong-but-confident answers |
| Phase 3+ | Fine-tuned Laya | Laya for most steps | Fast **and** accurate |

Fine-tuning cannot come first: the training data is the trajectory log produced by running the agent, and
the question format has to be stable before a model is trained on it.

---

## 7. Risks

| Risk | Mitigation |
| :--- | :--- |
| A single-pass encoder cannot do goal decomposition ("stops too early") | Keep planning in the LLM; Laya answers local questions about one step with an explicit criterion |
| Overfitting to the practice sites | Vary the sites, keep some sites out of training, try real sites |
| Memory pressure (Laya + LLM + Chromium) | Laya is <1 GB; use a smaller LLM tier when memory is tight; run large teacher models offline only |
| Real sites block headless Chromium | Headed mode, realistic user agent, note blocked sites separately |