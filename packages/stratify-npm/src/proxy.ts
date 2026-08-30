/**
 * stdio -> HTTP proxy, for MCP clients that only support a local stdio server
 * (MCP_COMPATIBILITY.md: "Ship the npx stdio->HTTP proxy anyway for local-only clients,
 * but it is a convenience, never the primary path" -- Streamable HTTP direct to
 * stratify-mcp.aeon-labs.site is what claude.ai and every modern client actually use).
 *
 * One JSON-RPC message per line on stdin (the MCP stdio transport's own framing -- no
 * Content-Length headers, unlike LSP), forwarded verbatim as the POST body to /mcp, with
 * the response written back as one line on stdout. This proxy does not parse or
 * understand the JSON-RPC payload beyond pulling out `id` for the one case that needs it
 * (synthesizing an error if the network call itself fails) -- staying a dumb pipe is what
 * keeps this file correct as the protocol surface grows on the server side without a
 * matching release here.
 */
import * as readline from "node:readline";
import { DEFAULT_BASE_URL } from "./client.js";

export interface ProxyOptions {
  apiKey: string;
  baseUrl?: string;
  timeoutMs?: number;
  /** Injectable for tests -- defaults to process.stdin/stdout. */
  input?: NodeJS.ReadableStream;
  output?: NodeJS.WritableStream;
}

const DEFAULT_TIMEOUT_MS = 120_000;

export async function runStdioProxy(opts: ProxyOptions): Promise<void> {
  const baseUrl = (opts.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
  const url = `${baseUrl}/mcp`;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const input = opts.input ?? process.stdin;
  const output = opts.output ?? process.stdout;

  const rl = readline.createInterface({ input, terminal: false });

  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    let requestId: unknown = null;
    try {
      const parsed = JSON.parse(trimmed);
      if (!Array.isArray(parsed)) requestId = parsed?.id ?? null;
    } catch {
      // Malformed JSON from the local client -- forwarded anyway; the server's own
      // PARSE_ERROR handling reports it properly, and this proxy does not need to
      // duplicate JSON-RPC validation to do its one job.
    }

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${opts.apiKey}`,
          "User-Agent": "stratify-mcp-proxy/1.0",
        },
        body: trimmed,
        signal: AbortSignal.timeout(timeoutMs),
      });
      const text = (await response.text()).trim();
      output.write(text + "\n");
    } catch (err) {
      // The local client is waiting on a reply for this id -- a silently dropped line
      // means it hangs forever rather than seeing a clear, actionable error.
      const errorResponse = {
        jsonrpc: "2.0", id: requestId,
        error: { code: -32000,
                message: `stratify-mcp-proxy: could not reach ${url}: ${(err as Error).message}` },
      };
      output.write(JSON.stringify(errorResponse) + "\n");
    }
  }
}
