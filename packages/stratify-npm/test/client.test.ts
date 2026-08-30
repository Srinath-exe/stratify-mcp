/**
 * Unit tests against a mocked global fetch -- no live server required. Response bodies
 * are copied from reading server/app.py's _handle() and _rpc_error()/_tool_text()
 * directly, not guessed, so these pin the wire contract this client actually depends on.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { AuthenticationError, DEFAULT_BASE_URL, ProtocolError, QuotaExceededError, StratifyClient, ToolRefusalError, StrategySpec, PresetSpec } from "../src/client.js";

let originalFetch: typeof fetch;
beforeEach(() => { originalFetch = globalThis.fetch; });
afterEach(() => { globalThis.fetch = originalFetch; });

interface Captured { url: string; init: RequestInit }

function mockFetchOnce(body: unknown, opts: { ok?: boolean; status?: number } = {}): () => Captured | null {
  const ok = opts.ok ?? true;
  const status = opts.status ?? 200;
  let captured: Captured | null = null;
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    captured = { url: url as string, init: init ?? {} };
    return {
      ok, status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  }) as typeof fetch;
  return () => captured;
}

function client(): StratifyClient {
  return new StratifyClient({ apiKey: "sk_live_test", baseUrl: "https://example.invalid" });
}

test("runBacktest returns structuredContent and sends the right request", async () => {
  const payload = {
    backtest_id: "bt_1", summary: { cagr: 0.3 }, honesty: {},
    trades: [{ n: 1, pnl_pts: 5 }],
    equity_curve: { columns: ["date"], rows: [["2025-07-01"]] },
    strategy_book: { qualified: true },
  };
  const getReq = mockFetchOnce({
    jsonrpc: "2.0", id: 1,
    result: { content: [{ type: "text", text: JSON.stringify(payload) }],
             structuredContent: payload, isError: false },
  });

  const result = await client().runBacktest(
    { structure: "short_strangle", params: { pct_offset: 1.5 } });

  assert.equal(result.backtest_id, "bt_1");
  assert.equal((result.summary as { cagr: number }).cagr, 0.3);
  assert.equal(result.trades?.length, 1);

  const req = getReq();
  assert.ok(req);
  const sentBody = JSON.parse(req!.init.body as string);
  assert.equal(sentBody.method, "tools/call");
  assert.equal(sentBody.params.name, "run_backtest");
  assert.equal(sentBody.params.arguments.detail, "standard");
  assert.equal(sentBody.params.arguments.lots, 1);
  const headers = req!.init.headers as Record<string, string>;
  assert.equal(headers.Authorization, "Bearer sk_live_test");
  assert.equal(req!.url, "https://example.invalid/mcp");
});

test("throws AuthenticationError on -32001 and carries data", async () => {
  mockFetchOnce({ jsonrpc: "2.0", id: 1,
                 error: { code: -32001, message: "missing or invalid API key",
                         data: { how_to_fix: "POST /v1/signup" } } });
  await assert.rejects(() => client().describeCoverage(), (err: unknown) => {
    if (!(err instanceof AuthenticationError)) return false;
    assert.equal(err.data.how_to_fix, "POST /v1/signup");
    return true;
  });
});

test("throws QuotaExceededError with limit and retryAfterSeconds", async () => {
  mockFetchOnce({ jsonrpc: "2.0", id: 1,
                 error: { code: -32002, message: "cpu_seconds_per_hour exceeded",
                         data: { limit: "cpu_seconds_per_hour", retry_after_seconds: 90 } } });
  await assert.rejects(
    () => client().runBacktest({ structure: "iron_condor", params: {} }),
    (err: unknown) => {
      if (!(err instanceof QuotaExceededError)) return false;
      assert.equal(err.limit, "cpu_seconds_per_hour");
      assert.equal(err.retryAfterSeconds, 90);
      return true;
    });
});

test("throws ToolRefusalError on a successful isError result, not a rejection code", async () => {
  mockFetchOnce({ jsonrpc: "2.0", id: 1,
                 result: { content: [{ type: "text", text: "touched only 4 distinct contracts" }],
                          isError: true } });
  await assert.rejects(
    () => client().runBacktest({ structure: "credit_spread", params: {} }),
    (err: unknown) => {
      if (!(err instanceof ToolRefusalError)) return false;
      assert.match(err.message, /distinct contracts/);
      return true;
    });
});

test("throws ProtocolError on an unrecognised JSON-RPC error code", async () => {
  mockFetchOnce({ jsonrpc: "2.0", id: 1, error: { code: -32603, message: "internal error" } });
  await assert.rejects(() => client().describeCoverage(), (err: unknown) => {
    if (!(err instanceof ProtocolError)) return false;
    assert.equal(err.code, -32603);
    return true;
  });
});

test("constructor requires an apiKey", () => {
  assert.throws(() => new StratifyClient({ apiKey: "" }));
});

test("search/fetchDocument/listStrategies/explainMethodology send the right tool names", async () => {
  const cases: Array<[() => Promise<unknown>, string, Record<string, unknown>]> = [
    [() => client().search("SENSEX weekly"), "search", { query: "SENSEX weekly" }],
    [() => client().fetchDocument("bt_1"), "fetch", { id: "bt_1" }],
    [() => client().explainMethodology("margin"), "explain_methodology", { topic: "margin" }],
    [() => client().listStrategies({ order: "pnl", limit: 5 }), "list_strategies",
     { order: "pnl", limit: 5 }],
  ];
  for (const [call, toolName, expectedArgs] of cases) {
    const getReq = mockFetchOnce({
      jsonrpc: "2.0", id: 1,
      result: { content: [{ type: "text", text: "{}" }], structuredContent: {}, isError: false },
    });
    await call();
    const sent = JSON.parse(getReq()!.init.body as string);
    assert.equal(sent.params.name, toolName);
    assert.deepEqual(sent.params.arguments, expectedArgs);
  }
});

test("signup posts to /v1/signup and returns the raw response", async () => {
  const getReq = mockFetchOnce({
    account_id: "acc_1", key_id: "key_1", api_key: "sk_live_new", tier: "free",
    limits: {}, mcp_endpoint: "https://example.invalid/mcp", warning: "shown once",
  });
  const result = await StratifyClient.signup("me@example.com", "https://example.invalid");
  assert.equal(result.api_key, "sk_live_new");
  const req = getReq();
  assert.equal(req!.url, "https://example.invalid/v1/signup");
  assert.deepEqual(JSON.parse(req!.init.body as string), { email: "me@example.com" });
});

test("DEFAULT_BASE_URL points at the production endpoint", () => {
  assert.equal(DEFAULT_BASE_URL, "https://stratify-mcp.aeon-labs.site");
});

test("the spec type accepts the open protocol, not only the presets", () => {
  // THIS IS A COMPILE-TIME TEST. StrategySpec once REQUIRED `structure` and `params`,
  // which typed the product's own flagship shape out of existence: a leg list with rules
  // over it would not compile, so every TypeScript user was silently confined to the five
  // retired presets. If that regresses, `npm run build` fails before this ever runs.
  const open: StrategySpec = {
    legs: [
      { side: "sell", type: "CE", strike: { delta_near: 0.2 } },
      { side: "sell", type: "PE", strike: { delta_near: 0.2 }, qty: 2 },
      { side: "buy", type: "CE", strike: "atm", expiry: "next" },
    ],
    entry: { cadence: "monthly", dte: 21, time: "09:47", when: { vix: { gte: 15 } } },
    rules: [
      { when: { pnl_pct_of_credit: { gte: 0.6 } }, then: "close" },
      { when: { leg_mark_mult: { gte: 2, leg: 0 } },
        then: { roll: { legs: [0], to: { delta_near: 0.2 } } }, max_times: 2 },
    ],
    exit: { time: "15:10" },
    portfolio: { stop_after_losses: 3, resume_after_days: 30 },
    resolution: 5,
  };
  assert.equal(open.legs.length, 3);
  // ...and the retired shape still compiles, because the server still accepts it.
  const preset: PresetSpec = { structure: "short_strangle", params: { pct_offset: 1.5 } };
  assert.equal(preset.structure, "short_strangle");
});
