# chrome-devtools-mcp vs agent-browser — Efficiency & Token Comparison

> Focus: which is more efficient and token-lean for AI agent browser automation.

## Architecture

| Aspect | chrome-devtools-mcp | agent-browser |
|--------|-------------------|---------------|
| Type | MCP server (Node.js) | Native Rust CLI |
| Transport | stdio (JSON-RPC) | Direct CLI output (plain text) |
| Browser | Launches/attaches to Chrome | Launches/attaches to Chrome |
| Tool count | ~40+ individual MCP tools | ~80 CLI subcommands |
| Batch execution | No — one tool call per action | Yes — `batch` command |
| AI-friendly text output | No (raw HTML/JSON) | Yes — `read` (markdown), `snapshot` (a11y tree) |

## Token Efficiency

### Per-Action Overhead

| Action | chrome-devtools-mcp | agent-browser | Token Winner |
|--------|-------------------|---------------|-------------|
| Navigate to URL | 1 MCP call: `mcp_chrome_devtools_navigate` + JSON args + JSON result | 1 CLI: `agent-browser open <url>` → text output | agent-browser |
| Get page content | `get_dom` / `get_html` → full HTML string (can be 50KB+) | `read` → markdown text (typically 5-10KB) | agent-browser (5-10x less) |
| Get page structure | `get_dom` → full HTML | `snapshot` → a11y tree with refs (~2-5KB) | agent-browser (10x less) |
| Click element | 1 MCP call + JSON | 1 CLI: `click @e2` | agent-browser |
| Fill form field | 1 MCP call + JSON | 1 CLI: `fill @e3 "text"` | agent-browser |
| Screenshot | Returns base64 image (100KB-1MB → ~25K-250K tokens) | `screenshot --if-changed` → skips if unchanged; JPEG option (3-5x smaller) | agent-browser |
| Multi-step (navigate + snapshot + click) | 3 separate MCP calls (3x overhead) | 1 `batch` call (1x overhead) | agent-browser (3x less) |
| Get console errors | `get_console_messages` → JSON array | `console` → plain text list | agent-browser |
| Get network requests | `get_network_requests` → JSON array | `network requests --filter api` → filtered text | agent-browser |
| Evaluate JS | `evaluate_js` → JSON | `eval <js>` → plain text | agent-browser |
| Get text of element | `get_text` or query_selector + read | `get text @e1` | agent-browser |

### Token Cost Estimation (typical page)

| Scenario | chrome-devtools-mcp | agent-browser | Savings |
|----------|-------------------|---------------|---------|
| Load page + read content | ~15K-50K tokens (HTML) | ~2K-5K tokens (markdown) | 70-90% |
| Page structure for AI | ~20K-60K tokens (DOM) | ~2K-5K tokens (a11y tree) | 80-90% |
| 5-step interaction flow | ~15K tokens (5 calls) | ~3K tokens (1 batch) | 80% |
| Screenshot (changed) | ~25K-250K tokens (PNG base64) | ~10K-100K tokens (JPEG, if-changed) | 30-60% |
| Screenshot (unchanged) | ~25K-250K tokens (always sends) | ~0 tokens (skipped) | 100% |
| Full page inspection | ~50K-100K+ tokens | ~5K-10K tokens | 85-95% |

### Token Overhead Per Tool Call

```
chrome-devtools-mcp (per call):
  Tool name:        "mcp_chrome_devtools_navigate"    ~6 tokens
  JSON args:        {"url": "https://..."}            ~10-50 tokens
  JSON result:      {"result": "navigated to ..."}    ~20-200 tokens
  LLM wrapper:      tool_call overhead                ~20-50 tokens
  ─────────────────────────────────────────────────────
  Total per call:   ~60-350 tokens (before content)

agent-browser (per command):
  CLI command:      "agent-browser open https://..."   ~8 tokens
  Text output:      "Navigated to https://..."         ~5-20 tokens
  ─────────────────────────────────────────────────────
  Total per command: ~15-50 tokens (before content)
```

## Feature Comparison

| Feature | chrome-devtools-mcp | agent-browser |
|---------|-------------------|---------------|
| Basic navigation | ✅ | ✅ |
| DOM inspection | ✅ (raw HTML) | ✅ (a11y tree, markdown) |
| Form filling | ✅ | ✅ |
| Clicking/hovering | ✅ | ✅ (+ `--human` for curved movement) |
| Screenshots | ✅ (PNG base64) | ✅ (JPEG, `--if-changed`, `--annotate`) |
| Console access | ✅ | ✅ |
| Network monitoring | ✅ | ✅ (+ HAR recording, filtering) |
| JavaScript eval | ✅ | ✅ |
| Performance traces | ✅ (Chrome Tracing API) | ❌ |
| Memory profiling | ✅ (`--memoryDebugging`) | ❌ |
| CPU profiling | ✅ (via traces) | ❌ |
| Cookie/storage | ✅ | ✅ (+ `--curl` import) |
| Multiple tabs | ✅ | ✅ |
| Wait conditions | ✅ | ✅ (text, URL, JS fn, selector, load states) |
| Semantic locators | ❌ (CSS selectors only) | ✅ (role, text, label, placeholder, testid) |
| Batch execution | ❌ | ✅ |
| Accessibility tree | ❌ | ✅ (`snapshot`) |
| Agent-readable text | ❌ | ✅ (`read` — markdown, `--filter`, `--outline`) |
| Domain allowlist | ✅ (`--blockedUrlPattern`) | ✅ (`--allowed-domains` + WebSocket guard) |
| Auth vault | ❌ | ✅ (encrypted, LLM never sees passwords) |
| Content boundaries | ❌ | ✅ (`--content-boundaries`) |
| Action policy | ❌ | ✅ (gate destructive actions) |
| WebMCP (experimental) | ❌ | ✅ |
| State encryption | ❌ | ✅ (AES-256-GCM) |
| CLI mode (no MCP) | ❌ | ✅ |
| AI chat mode | ❌ | ✅ (`agent-browser chat`) |
| Screenshot diff | ❌ | ✅ (`--if-changed`, `--threshold`) |
| Annotated screenshots | ❌ | ✅ (`--annotate`) |
| PDF export | ❌ | ✅ |
| Clipboard | ❌ | ✅ |
| Mouse control (raw) | ✅ (via DevTools) | ✅ (+ human-like movement) |
| Device emulation | ✅ | ✅ |
| Geolocation | ✅ | ✅ |
| Offline mode | ❌ | ✅ |
| Plugin system | ❌ | ✅ |

## Use-Case Fit

| Use Case | Winner | Why |
|----------|--------|-----|
| AI agent browsing (fill forms, navigate) | **agent-browser** | 70-90% less tokens, semantic locators, batch |
| AI agent page inspection | **agent-browser** | `read` (markdown) + `snapshot` (a11y tree) vs raw HTML |
| Multi-step workflow automation | **agent-browser** | `batch` = 1 call vs N MCP calls |
| Performance analysis (LCP, CLS, traces) | **chrome-devtools-mcp** | Only one with Chrome Tracing API |
| Memory/CPU profiling | **chrome-devtools-mcp** | Only one with DevTools profiling |
| Network debugging (HAR, mocking) | **agent-browser** | HAR recording + filtering vs JSON dump |
| Secure auth flows | **agent-browser** | Auth vault (encrypted) vs no vault |
| Sandboxed/locked-down environments | **agent-browser** | Domain allowlist + action policy + content boundaries |
| Deep DevTools inspection | **chrome-devtools-mcp** | Full DevTools access (breakpoints, memory, CPU) |
| Token budget constrained | **agent-browser** | 70-95% less tokens on most operations |
| Screenshot-heavy workflows | **agent-browser** | `--if-changed` + JPEG = 30-100% savings |
| One-off debugging session | **chrome-devtools-mcp** | Richer inspection tools |

## Token Budget Impact (Realistic Session)

**Scenario: Test a 5-page e-commerce checkout flow**

| Step | chrome-devtools-mcp | agent-browser |
|------|-------------------|---------------|
| 1. Navigate to product page | ~5K tokens (HTML) | ~1K tokens (read) |
| 2. Get page structure | ~15K tokens (DOM) | ~2K tokens (snapshot) |
| 3. Click "Add to Cart" | ~1K tokens | ~0.3K tokens |
| 4. Navigate to cart | ~5K tokens (HTML) | ~1K tokens (read) |
| 5. Fill shipping form (3 fields) | ~3K tokens (3 calls) | ~0.5K tokens (1 batch) |
| 6. Click "Proceed to Payment" | ~1K tokens | ~0.3K tokens |
| 7. Screenshot (for verification) | ~50K tokens (PNG) | ~5K tokens (JPEG, if-changed) |
| 8. Check console for errors | ~2K tokens | ~0.5K tokens |
| **Total** | **~82K tokens** | **~10.6K tokens** |

**Savings: ~87% fewer tokens with agent-browser**

## Verdict

| Criterion | Winner |
|-----------|--------|
| Token efficiency | **agent-browser** 🏆 (70-95% less) |
| AI agent suitability | **agent-browser** 🏆 (a11y tree, markdown, semantic locators) |
| Multi-step workflows | **agent-browser** 🏆 (batch execution) |
| Performance profiling | **chrome-devtools-mcp** 🏆 (only one with traces) |
| Security/sandboxing | **agent-browser** 🏆 (auth vault, allowlist, policy) |
| Deep debugging | **chrome-devtools-mcp** 🏆 (breakpoints, memory, CPU) |
| Screenshot token cost | **agent-browser** 🏆 (--if-changed, JPEG) |
| Network debugging | **agent-browser** 🏆 (HAR, filtering) |
| General browsing automation | **agent-browser** 🏆 |
| DevTools-level inspection | **chrome-devtools-mcp** 🏆 |

### Recommendation

- **For AI agent browser automation (your use case):** Use **agent-browser** — 70-95% token savings, built for AI consumption
- **For performance profiling / deep debugging:** Keep **chrome-devtools-mcp** as a complementary tool
- **Best setup:** Both — agent-browser for daily automation, chrome-devtools-mcp for when you need traces/profiles

## Installation for agent-browser (Windows)

```bash
npm install -g agent-browser
agent-browser install  # Downloads Chrome for Testing (or detects Brave)
```

Config in Hermes (if using as MCP):
```yaml
mcp_servers:
  agent-browser:
    command: "agent-browser"
    args: ["mcp"]
```

Or just use via terminal (no MCP overhead):
```bash
agent-browser open https://your-site.com
agent-browser read
agent-browser snapshot
agent-browser click @e2
```
