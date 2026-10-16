# 🌐 Laya Browser Agent: Product Overview & Capabilities

> **The First 100% Local, Zero-Cloud-Cost Autonomous Web Agent for Private Enterprise Workflows**

---

## 1. Product Overview

**Laya Browser Agent** is a next-generation autonomous web automation agent designed for developers, QA engineers, and enterprise teams who need fast, reliable web automation without the cost, latency, or data privacy risks of cloud AI APIs (OpenAI, Anthropic).

Powered by **Laya MLX** (`aac6fef/laya-mlx`) running directly on Apple Silicon Metal GPUs and local **Ollama** (`llama3.1:8b`), Laya Browser Agent interacts with web applications through two unified interfaces:
1. **WebMCP (Web Model Context Protocol)**: Direct programmatic tool RPCs exposed via `navigator.modelContext`, `document.modelContext`, or `<form toolname="...">`.
2. **Universal Visual & DOM Actuation**: Coordinate clicks, auto-scrolling down table rows up to 8,000px, and keyboard input typing for web applications that do not yet have WebMCP.

---

## 2. Why Laya Browser Agent?

| Dimension | Traditional Cloud Agents (Claude/GPT-4o) | Laya Browser Agent |
| :--- | :--- | :--- |
| **Cloud API Cost** | $0.50 – $2.50 per workflow | **$0.00 (Zero cloud tokens forever)** |
| **Decision Latency** | 4,000 – 12,000 ms per step | **10 – 20 ms per step (Apple Silicon GPU)** |
| **Data Privacy** | Credentials & PII sent over the wire | **100% On-Premise / Local Machine** |
| **Application Speed** | 30 – 90 seconds per task | **3 – 8 seconds per task** |
| **Reliability** | Brittle HTML DOM scraping | **WebMCP Typed Tools + Deep Table Perception** |

---

## 3. Supported Web Applications & Protocols

Laya Browser Agent works seamlessly across modern enterprise web applications:

### 1. WebMCP Native Applications
Exposes web capabilities directly to browser agents as typed, high-level JSON tools:
```javascript
// PostQode Nexus / WebMCP Native
await window.navigator.modelContext.getTools();
// Returns: place_order, cancel_order, approve_order, search_products, etc.
```

### 2. Declarative WebMCP Forms
Standard HTML forms enhanced with lightweight agent attributes:
```html
<form toolname="submit_feedback" tooldescription="Submit user feedback">
    <input name="email" toolparamdescription="User email" />
    <input name="rating" toolparamdescription="Rating 1 to 5" />
    <button type="submit">Submit</button>
</form>
```

### 3. Classic & Single-Page Web Applications (Without WebMCP)
Applications with standard HTML5 elements, client-side routing, and complex tables:
- Interactive navigation bars, tabs, and links.
- Deep table and list scanning (e.g. orders, inventory, customers) with automatic icon button recognition (`Cancel`, `Approve`, `Delete`, `Edit`).
- Form inputs, textareas, and dropdown selection.

---

## 4. Reference Test Application: PostQode Nexus

Laya Browser Agent is thoroughly tested and verified against **PostQode Nexus** ([github.com/Sreenuraj/postqode-nexus](https://github.com/Sreenuraj/postqode-nexus)):
- **Tech Stack**: Next.js 15 App Router, TypeScript, TailwindCSS, Spring Boot 3 Java backend, PostgreSQL.
- **Roles & Workflows Tested**:
  - **Shopper / User**: Login, product catalog search, purchasing, order tracking, and order cancellation.
  - **Admin / Operations**: Order management, multi-order approvals and rejections, Command Center system inspection.

If you would like to run experiments or test custom agent scenarios locally, clone and start PostQode Nexus at `http://localhost:3000`.

---

## 5. Key Product Features

### 🧠 Generic Zero-Shot Intent Handling
- **Zero Predefined Intent Tables**: Handles open-ended goals like *"compare 2 products"*, *"see if available"*, or *"export invoice"* without maintaining hardcoded intent dictionaries or regex catalogs.
- **Multi-Step Dynamic Decomposition**: Decomposes complex user instructions into sequential, manageable subtasks on the fly.

### ⚡️ Live In-Memory Affordance Perception
- Discovers interactive buttons, navigation links, and WebMCP tools in **< 3ms** directly via Chrome DevTools Protocol.
- Eliminates stale disk file caches, reacting immediately to live page updates, modal dialogs, and dynamic order state changes.

### 📊 Transparent Execution Reports & Visual Verification
- Automatically produces structured Markdown reports (`execution_report.md` by default).
- Includes step-by-step telemetry, latency breakdowns, masked credential audit trails, and embedded visual screenshot proof (`final_execution_state.png`).

### 🖥 Flexible Headed & Headless Execution
- **Headed Mode (`--headed`)**: Watch Chromium actuate the browser live on screen. Perfect for development and demonstrations.
- **Headless Mode (`--headless`)**: Runs quietly in the background for CI/CD pipelines, automated testing, and background workers.
