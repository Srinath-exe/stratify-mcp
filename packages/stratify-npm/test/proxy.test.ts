import assert from "node:assert/strict";
import { Readable, Writable } from "node:stream";
import { afterEach, beforeEach, test } from "node:test";

import { runStdioProxy } from "../src/proxy.js";

let originalFetch: typeof fetch;
beforeEach(() => { originalFetch = globalThis.fetch; });
afterEach(() => { globalThis.fetch = originalFetch; });

function linesToStream(lines: string[]): Readable {
  return Readable.from(lines.map((l) => l + "\n"));
}

function captureOutput(): { stream: Writable; text: () => string } {
  const chunks: string[] = [];
  const stream = new Writable({
    write(chunk, _enc, cb) { chunks.push(chunk.toString()); cb(); },
  });
  return { stream, text: () => chunks.join("") };
}

test("forwards one stdin line as the POST body and writes the response back", async () => {
  const requestMsg = { jsonrpc: "2.0", id: 1, method: "tools/list" };
  const responseMsg = { jsonrpc: "2.0", id: 1, result: { tools: [] } };
  let sentAuth = "";
  globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
    sentAuth = (init?.headers as Record<string, string>).Authorization;
    assert.deepEqual(JSON.parse(init!.body as string), requestMsg);
    return { ok: true, status: 200, text: async () => JSON.stringify(responseMsg) } as Response;
  }) as typeof fetch;

  const { stream: output, text } = captureOutput();
  await runStdioProxy({
    apiKey: "sk_live_test", baseUrl: "https://example.invalid",
    input: linesToStream([JSON.stringify(requestMsg)]), output,
  });

  assert.equal(text().trim(), JSON.stringify(responseMsg));
  assert.equal(sentAuth, "Bearer sk_live_test");
});

test("handles multiple lines in order", async () => {
  let call = 0;
  globalThis.fetch = (async () => {
    call += 1;
    return { ok: true, status: 200,
            text: async () => JSON.stringify({ jsonrpc: "2.0", id: call, result: {} }) } as Response;
  }) as typeof fetch;

  const { stream: output, text } = captureOutput();
  await runStdioProxy({
    apiKey: "sk_live_test",
    input: linesToStream([
      JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping" }),
      JSON.stringify({ jsonrpc: "2.0", id: 2, method: "ping" }),
    ]),
    output,
  });

  const lines = text().trim().split("\n");
  assert.equal(lines.length, 2);
  assert.equal(JSON.parse(lines[0]).id, 1);
  assert.equal(JSON.parse(lines[1]).id, 2);
});

test("synthesizes a JSON-RPC error carrying the original id when the network call fails", async () => {
  globalThis.fetch = (async () => { throw new Error("connection refused"); }) as typeof fetch;

  const { stream: output, text } = captureOutput();
  await runStdioProxy({
    apiKey: "sk_live_test",
    input: linesToStream([JSON.stringify({ jsonrpc: "2.0", id: 42, method: "ping" })]),
    output,
  });

  const parsed = JSON.parse(text().trim());
  assert.equal(parsed.id, 42);
  assert.equal(parsed.error.code, -32000);
  assert.match(parsed.error.message, /connection refused/);
});

test("skips blank lines without calling fetch", async () => {
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    return { ok: true, status: 200, text: async () => "{}" } as Response;
  }) as typeof fetch;

  const { stream: output } = captureOutput();
  await runStdioProxy({
    apiKey: "sk_live_test",
    input: linesToStream(["", "  ", JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping" })]),
    output,
  });

  assert.equal(calls, 1);
});
