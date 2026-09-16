# MCP Client Compatibility — Constraints That Must Shape the Build

**Status:** Research v1 · **Date:** 2026-08-21 · Read before writing any server code.

Goal: maximum accessibility. Every constraint below was verified against current
documentation, and several of them would be expensive to discover after launch.

---

## 1. Transport — settled

| Client | stdio | SSE | Streamable HTTP |
|---|---|---|---|
| claude.ai web / mobile | **No** | Yes | **Yes** |
| Claude Desktop (connectors) | local only | Yes | **Yes** |
| Claude Code | Yes | Yes | **Yes** |
| ChatGPT (web, Developer mode) | **No** | Yes | **Yes** |
| Gemini CLI | Yes | Yes | **Yes** |
| Cursor / VS Code / OpenCode | Yes | Yes | Yes |

**Decision: Streamable HTTP is the build target.** The 2026-07-28 spec update moved to a
stateless HTTP request/response model; SSE is legacy. A stdio-only server is invisible to
claude.ai and ChatGPT — the two largest surfaces.

Ship the `npx` stdio→HTTP proxy anyway for local-only clients, but it is a convenience,
never the primary path.

---

## 2. Authentication — the biggest surprise, and it costs us scope

| Client | API key in header | OAuth 2.1 | No auth |
|---|---|---|---|
| claude.ai web | **Yes** — "Request headers" field, stored securely | Yes (DCR / CIMD) | Yes |
| ChatGPT | **NO — not offered in the UI** | Yes | Yes |
| Gemini CLI | via config | Yes | Yes |

**A bearer-token-only server reaches Claude but NOT ChatGPT.** ChatGPT connectors
authenticate with *none* or *OAuth* only; there is no API-key-header option in its UI.

*Resolved 2026-09-06:* the server now speaks OAuth 2.1 itself (`server/mcpauth.py`); both
columns are served. Unauthenticated `tools/call` answers HTTP 401 with a
`WWW-Authenticate` challenge that points at the protected-resource metadata.

### Consequence for us

Our "one API key from the dashboard, paste it in" model works beautifully on Claude and is
useless on ChatGPT. To reach ChatGPT and Gemini we must implement **OAuth 2.1**. This is
real scope that the LLD does not currently budget for.

Recommended: ship bearer-token first (Claude + Code + Cursor covers launch), add OAuth in
the phase that opens ChatGPT/Gemini. Do not promise those surfaces until OAuth exists.

### OAuth implementation details that bite

Claude uses **Dynamic Client Registration**:

- Serve `.well-known/oauth-authorization-server` exposing `/register`, `/authorize`, `/token`
- `/token` **must** accept `Content-Type: application/x-www-form-urlencoded` (RFC 6749 §4.1.3)
- `/register` uses `application/json` (RFC 7591 §3.1)
- **These are different parsers — do not assume one handler serves both.** This is a
  common, silent failure.
- OAuth 2.1 requires rotating or sender-constraining refresh tokens for public clients
- The 2025-11-25 spec added **Client ID Metadata Documents (CIMD)**, so DCR is no longer
  the only option — but DCR remains supported and is still the right choice for us

---

## 3. Timeouts — this forces an architectural change

| Client | Tool-call timeout |
|---|---|
| MCP TypeScript SDK default | 60 s (`DEFAULT_REQUEST_TIMEOUT_MSEC`) |
| Claude | ~45 s |
| **ChatGPT Desktop** | **30 s** |
| Claude Code | configurable (`timeout` in `.mcp.json`, `MCP_TOOL_TIMEOUT`) |

**The web clients are not configurable by us or by the user.** Therefore:

> **Every tool must return in under 30 seconds, or it is broken on ChatGPT.**

Free tier is safe — measured backtests run 8 ms (precomputed) to 300 ms (raw). But a
paid-tier 2010–2026 multi-symbol backtest could exceed 30 s.

### Required design change

The paid tier needs an **async submit/poll pattern**:

- `run_backtest` returns immediately with `{ backtest_id, status: "running", poll_after_ms }`
  whenever the router estimates the job will exceed ~15 s
- `get_backtest(backtest_id)` returns `running` or the completed result
- Below the threshold, return the result inline as normal — do not make the common case
  pay for the rare one

The router already knows each layer's `typical_cpu_sec`, so the estimate is essentially free.

---

## 4. Response size

- Claude Code **warns above 10,000 tokens** and **caps at 25,000 tokens** by default
  (`MAX_MCP_OUTPUT_TOKENS` to raise; a tool may set `anthropic/maxResultSizeChars`)
- Our measured backtest response is **904 bytes** — roughly 0.9% of the cap. Comfortable.

Rules:
- `get_trades` **must** paginate with a cursor. A year of per-trade detail will breach the cap.
- `get_option_chain` must stay row-capped (already specified).
- Never inline the HTML report — it would be ~20 k tokens. `report_url` costs 40 bytes.
- Set `anthropic/maxResultSizeChars` explicitly on any tool that could grow.

This is independent confirmation of the compute-to-data design: the protocol itself
punishes returning rows.

---

## 5. Tool count — keep the surface small

- Claude Desktop showed only **100 of 206 tools** on one server — client-side truncation
- Each tool definition costs **300–600 tokens** of system prompt before the conversation starts
- **Tool-selection accuracy degrades sharply past 30–50 tools**

Our planned 6–8 tools is correct. QuantConnect's 64 and TradingView's 37 are both past the
comfortable range. Resist the urge to add a tool per query shape — parameterise instead.

---

## 6. ChatGPT's deep-research contract

To appear in ChatGPT's **deep research / company-knowledge** paths, a server must expose two
read-only tools named exactly `search` and `fetch`, each taking a **single string parameter**.

Full Developer mode allows arbitrary tools, so this constraint applies only to the
deep-research surface — but that surface is valuable distribution.

**Recommendation:** ship `search` and `fetch` as thin aliases over `describe_coverage` and
`get_backtest`, conforming to the required schema. Cheap to add, opens a channel we would
otherwise be excluded from.

---

## 7. Discovery

- The **official MCP Registry** is live at `registry.modelcontextprotocol.io`; README-based
  listings are deprecated. Publish there.
- OpenAI's app directory merged into a plugin directory (2026-07-09) shared between ChatGPT
  and Codex.
- Ship `llms.txt` (Massive does this) so any web-enabled agent can read our docs and write
  correct calls with no integration at all.

---

## 8. Build checklist

- [ ] Streamable HTTP transport (not SSE, not stdio-primary)
- [ ] Public HTTPS endpoint with valid TLS
- [ ] Bearer-token auth via request headers — Claude launch path
- [ ] OAuth 2.1 + DCR — required before claiming ChatGPT or Gemini support
- [ ] `.well-known/oauth-authorization-server`, with **separate** form-encoded `/token`
      and JSON `/register` handlers
- [ ] Every tool returns in **< 30 s**; async submit/poll above ~15 s estimated
- [ ] All responses well under 25,000 tokens; `anthropic/maxResultSizeChars` set
- [ ] Cursor pagination on `get_trades`
- [ ] Tool count ≤ 8
- [ ] `search` + `fetch` aliases with single-string params for ChatGPT deep research
- [ ] `npx` stdio→HTTP proxy for local-only clients
- [ ] Published to the official MCP Registry
- [ ] `llms.txt` served
- [ ] Strict JSON — no `Infinity` / `NaN` (defect observed in TradingView MCP)

---

## 9. Ecosystem conventions — measured from the official registry

Pulled and analysed **1,538 unique published servers** from
`registry.modelcontextprotocol.io` on 2026-08-21. These are observed conventions, not
opinions — match them so we look native.

### Transport: the argument is over

| Remote transport | Servers |
|---|---|
| **streamable-http** | **1,359** |
| sse | 45 |

97% of remote endpoints are Streamable HTTP. SSE is legacy. Confirms §1.

**89% of all published servers offer a remote endpoint** (1,360 of 1,533). Local-only is
now the minority position:

| Shape | Servers |
|---|---|
| remote only | 1,295 (84%) |
| local only | 173 (11%) |
| both | 65 (4%) |

### Auth: `Authorization` header dominates

Of 436 remotes declaring headers, **402 use `Authorization`**. `X-API-Key` appears 27 times
combined. Our bearer-token choice is the ecosystem norm.

### Naming and hosting conventions

| Convention | Observed | Our choice |
|---|---|---|
| Namespace | reverse-DNS (`ai.*` 1109, `app.*` 393) | `ai.stratify/mcp` |
| Hostname first label | `mcp.*` (334) > `server.*` (214) > `api.*` (131) | `mcp.stratify.io` |
| Endpoint path | `/mcp` (770) > `/api/mcp` (160) | `/mcp` |
| Description length | **median 92 chars, max 100** — a hard cap | ≤ 100 chars |
| Repo or website declared | 1,375 / 1,538 | declare both |
| Multiple remote URLs | 37 servers (redundancy) | consider later |

**Description discipline matters.** One line, ≤100 characters, leading with a verb.
Most common openers: `Search`, `Create`, `Manage`, `Generate`, `Read-only`, `Live`.

Draft: `Backtest Indian index options from any AI agent. Results in milliseconds, data stays put.`
(89 chars.)

### Local packaging

npm 201 · pypi 28 · oci 11 · mcpb 7. **npm outnumbers pypi 7:1** for local distribution —
so ship the stdio proxy on npm first, pip second.

### Publishing checklist

- [ ] Reverse-DNS name `ai.stratify/mcp`
- [ ] Endpoint `https://mcp.stratify.io/mcp`, Streamable HTTP
- [ ] `Authorization: Bearer` header auth
- [ ] Description ≤ 100 chars, verb-first
- [ ] Declare both `repository` and `websiteUrl`
- [ ] Publish to the official registry; keep the entry **live and accurate**
