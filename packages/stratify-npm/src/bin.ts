#!/usr/bin/env node
/**
 * `npx stratify-mcp` -- entrypoint for a local MCP client that only speaks stdio.
 * (`stratify-mcp-proxy` stays as an alias so an existing config keeps working.)
 * See proxy.ts for why this exists and what it does and does not do.
 */
import { DEFAULT_BASE_URL } from "./client.js";
import { runStdioProxy } from "./proxy.js";

function parseArgs(argv: string[]): { apiKey?: string; baseUrl?: string } {
  const opts: { apiKey?: string; baseUrl?: string } = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--api-key" && argv[i + 1] !== undefined) {
      opts.apiKey = argv[++i];
    } else if (argv[i] === "--base-url" && argv[i + 1] !== undefined) {
      opts.baseUrl = argv[++i];
    } else if (argv[i] === "--help" || argv[i] === "-h") {
      process.stdout.write(
        "stratify-mcp -- local stdio bridge to the Stratify MCP server\n\n" +
        "Usage:\n" +
        "  STRATIFY_API_KEY=sk_live_... npx stratify-mcp\n" +
        "  npx stratify-mcp --api-key sk_live_... [--base-url https://stratify-mcp.aeon-labs.site]\n\n" +
        "Only needed for a client that cannot speak Streamable HTTP directly -- most\n" +
        "modern MCP clients (claude.ai, Claude Desktop, Claude Code) should be pointed at\n" +
        `${DEFAULT_BASE_URL}/mcp directly instead.\n`);
      process.exit(0);
    }
  }
  return opts;
}

const cli = parseArgs(process.argv.slice(2));
const apiKey = cli.apiKey ?? process.env.STRATIFY_API_KEY;
const baseUrl = cli.baseUrl ?? process.env.STRATIFY_BASE_URL ?? DEFAULT_BASE_URL;

if (!apiKey) {
  process.stderr.write(
    "stratify-mcp: no API key given.\n" +
    "Set STRATIFY_API_KEY, or pass --api-key sk_live_....\n" +
    "Get one at https://stratify.aeon-labs.site, or programmatically via StratifyClient.signup(email).\n");
  process.exit(1);
}

runStdioProxy({ apiKey, baseUrl }).catch((err: Error) => {
  process.stderr.write(`stratify-mcp: fatal: ${err.message}\n`);
  process.exit(1);
});
