/**
 * JSON-RPC client for the Stratify MCP server.
 *
 * There is no separate REST endpoint for running a backtest (server/app.py:9-16) -- every
 * tool, including run_backtest, is only reachable through POST /mcp as MCP JSON-RPC 2.0
 * tools/call. This client speaks that protocol directly, using the platform `fetch`
 * (Node >=18, or a browser) rather than adding an HTTP dependency.
 *
 * No pandas-equivalent DataFrame wrapper here on purpose: JS has no standard tabular type
 * the way Python has pandas, and `result.trades`/`result.equityCurve.rows` are already
 * plain arrays of records -- the idiomatic thing to hand a JS caller, not a bundled
 * dependency on a specific dataframe library they may not want.
 */
import { AuthenticationError, ProtocolError, QuotaExceededError, StratifyError,
        ToolRefusalError, TransportError } from "./errors.js";

export const DEFAULT_BASE_URL = "https://stratify-mcp.aeon-labs.site";
const DEFAULT_TIMEOUT_MS = 120_000; // server/app.py's own MCP endpoint allows a 7-year
                                    // backtest up to 120s at p95 (sites-available/stratify-mcp)

const UNAUTHENTICATED = -32001;
const QUOTA_EXCEEDED = -32002;

/** A leg of an open-protocol strategy. Any number, any side, any quantity, any expiry. */
export interface Leg {
  side: "sell" | "buy";
  type: "CE" | "PE";
  /** "atm", an absolute strike, or a selector: {pct_offset}, {points_offset},
   *  {premium_near}, {delta_near}, {from_leg}. */
  strike: "atm" | number | Record<string, unknown>;
  qty?: number;
  expiry?: "near" | "next" | "far";
  label?: string;
}

/** A management rule: a condition tree, and what to do when it fires. */
export interface Rule {
  when: Record<string, unknown>;
  then: "close" | Record<string, unknown>;
  max_times?: number;
  label?: string;
}

/**
 * The open protocol: describe the position and how it is managed.
 *
 * `params`/`structure` are NOT here. This interface used to require them, which typed out
 * of existence the thing the product is actually for -- a leg list with rules over it
 * would not compile. The server accepts either shape (see EITHER_SPEC in server/tools.py),
 * so both are offered and neither is privileged.
 */
export interface StrategySpec {
  name?: string;
  symbol?: "NIFTY";
  legs: Leg[];
  entry?: {
    cadence?: "weekly" | "daily" | "monthly";
    /** ANY minute of the session, e.g. "09:20". Not a fixed grid. */
    time?: string;
    dte?: number;
    max_dte?: number;
    /** The reason for the trade: vix, prev_day_move_pct, gap_pct, realised_vol_20d,
     *  day_of_week, combined_premium ... */
    when?: Record<string, unknown>;
  };
  rules?: Rule[];
  exit?: { time?: string; when?: Record<string, unknown> };
  max_adjustments?: number;
  /** Rules over the SEQUENCE of trades: stop_after_losses, stop_after_drawdown_pct,
   *  skip_after_loss, max_trades, stop_after_profit_pct, resume_after_days. */
  portfolio?: Record<string, number | boolean>;
  resolution?: 1 | 5 | 15;
  period?: { from?: string; to?: string };
}

/** The original five-preset shape. Still accepted by the server. */
export interface PresetSpec {
  structure: string;
  symbol?: string;
  params: Record<string, number | string>;
  entry_time?: string;
  exit_time?: string;
  cadence?: string;
  max_dte?: number;
  gate?: string;
  bias?: string;
  period?: { from?: string; to?: string };
}

export type AnySpec = StrategySpec | PresetSpec;

export type DetailLevel = "summary" | "standard" | "full";

export interface EquityCurve {
  columns: string[];
  rows: unknown[][];
  note?: string;
}

export interface BacktestResult {
  backtest_id?: string;
  report_url?: string;
  report_note?: string;
  summary: Record<string, unknown>;
  honesty: Record<string, unknown>;
  interpretation?: string;
  trades?: Array<Record<string, unknown>>;
  equity_curve?: EquityCurve;
  strategy_book?: { qualified: boolean; note?: string };
  warnings?: string[];
  [key: string]: unknown;
}

export interface SignupResponse {
  account_id: string;
  key_id: string;
  api_key: string;
  tier: string;
  limits: Record<string, number>;
  mcp_endpoint: string;
  warning: string;
  [key: string]: unknown;
}

export interface StratifyClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeoutMs?: number;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number | null;
  result?: { content: Array<{ type: string; text: string }>;
            structuredContent?: unknown; isError?: boolean };
  error?: { code: number; message: string; data?: Record<string, unknown> };
}

export class StratifyClient {
  private apiKey: string;
  private baseUrl: string;
  private timeoutMs: number;
  private requestId = 0;

  constructor(opts: StratifyClientOptions) {
    if (!opts.apiKey) {
      throw new Error("apiKey is required -- get one from StratifyClient.signup()");
    }
    this.apiKey = opts.apiKey;
    this.baseUrl = (opts.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  /** spec: a StrategySpec (see describeCoverage() or the server's knowledge base for the
   * exact fields). detail: "summary" (aggregates only), "standard" (default -- equity
   * curve, breakdowns, first 25 trades), or "full" (every stored trade). */
  async runBacktest(spec: AnySpec,
                    opts: { lots?: number; detail?: DetailLevel } = {}): Promise<BacktestResult> {
    return this.call("run_backtest", {
      spec, lots: opts.lots ?? 1, detail: opts.detail ?? "standard",
    }) as Promise<BacktestResult>;
  }

  /** Retrieve a previously run backtest by id, exactly as first computed. */
  async getBacktest(backtestId: string, detail?: DetailLevel): Promise<BacktestResult> {
    const args: Record<string, unknown> = { backtest_id: backtestId };
    if (detail) args.detail = detail;
    return this.call("get_backtest", args) as Promise<BacktestResult>;
  }

  /** Symbols, date range, resolution, structures, gates, biases, cost model, known gaps.
   * Call this before building a spec. */
  async describeCoverage(): Promise<Record<string, unknown>> {
    return this.call("describe_coverage", {}) as Promise<Record<string, unknown>>;
  }

  /** How a result is produced and how to judge it. `topic` narrows to one section; omit
   * for the full document. */
  async explainMethodology(topic?: string): Promise<Record<string, unknown>> {
    return this.call("explain_methodology", topic ? { topic } : {}) as Promise<Record<string, unknown>>;
  }

  /** This account's strategies that held up out-of-sample, ranked by worst walk-forward
   * fold by default -- not by P&L. Returns the full response, not a bare array:
   * `{strategies: [...], bar: {...}, n_entries: number, ...}` -- verified against a real
   * account rather than assumed. Use `(await client.listStrategies()).strategies` for
   * just the entries. */
  async listStrategies(opts: { order?: "consistency" | "health" | "pnl"; limit?: number } = {}):
      Promise<Record<string, unknown>> {
    const args: Record<string, unknown> = { order: opts.order ?? "consistency" };
    if (opts.limit !== undefined) args.limit = opts.limit;
    return this.call("list_strategies", args) as Promise<Record<string, unknown>>;
  }

  /** Search what this service covers. Returns ids usable with fetchDocument(). */
  async search(query: string): Promise<Record<string, unknown>> {
    return this.call("search", { query }) as Promise<Record<string, unknown>>;
  }

  /** Fetch a document or backtest result by id, as returned by search(). Named
   * fetchDocument rather than `fetch` so it does not collide with the global fetch this
   * class already uses internally. */
  async fetchDocument(id: string): Promise<Record<string, unknown>> {
    return this.call("fetch", { id }) as Promise<Record<string, unknown>>;
  }

  /** Create an account and issue the first API key. No approval step (decision Sec.0) --
   * resolves immediately with a usable key. THE KEY IS SHOWN ONCE: store
   * `result.api_key` yourself, the server cannot recover it later. A static method
   * rather than an instance method, since there is no key yet to construct a client
   * with. */
  static async signup(email: string, baseUrl: string = DEFAULT_BASE_URL,
                      timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<SignupResponse> {
    const url = `${baseUrl.replace(/\/+$/, "")}/v1/signup`;
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      throw new TransportError(`could not reach ${url}: ${(err as Error).message}`);
    }
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = (body && typeof body === "object" && "error" in body)
        ? (body as { error: string }).error : `HTTP ${response.status}`;
      throw new TransportError(`signup failed (${response.status}): ${detail}`);
    }
    return body as SignupResponse;
  }

  // ---------------------------------------------------------------- transport

  private async call(toolName: string, args: Record<string, unknown>): Promise<unknown> {
    this.requestId += 1;
    const url = `${this.baseUrl}/mcp`;
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${this.apiKey}`,
          "User-Agent": "stratify-js/1.0",
        },
        body: JSON.stringify({
          jsonrpc: "2.0", id: this.requestId, method: "tools/call",
          params: { name: toolName, arguments: args },
        }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err) {
      throw new TransportError(`could not reach ${url}: ${(err as Error).message}`);
    }

    if (!response.ok) {
      // app.py returns non-2xx only for origin/size checks ahead of JSON-RPC -- a real
      // tool refusal or quota hit still comes back 200 with a JSON-RPC body, below.
      throw new TransportError(`HTTP ${response.status} from ${url}`);
    }

    let message: JsonRpcResponse;
    try {
      message = (await response.json()) as JsonRpcResponse;
    } catch {
      throw new TransportError("server response was not valid JSON");
    }

    if (message.error) {
      const { code, message: msg, data } = message.error;
      if (code === UNAUTHENTICATED) throw new AuthenticationError(msg, data);
      if (code === QUOTA_EXCEEDED) {
        throw new QuotaExceededError(msg, data?.limit as string | undefined,
                                     data?.retry_after_seconds as number | undefined);
      }
      throw new ProtocolError(msg, code);
    }

    const result = message.result;
    if (result?.isError) {
      const text = (result.content ?? []).map((b) => b.text).join("; ");
      throw new ToolRefusalError(text || "the server refused this call");
    }
    if (result && "structuredContent" in result && result.structuredContent !== undefined) {
      return result.structuredContent;
    }
    // Fallback for a pairing that only carries the text block (server/app.py's
    // _tool_text always sends both today; this keeps the client correct if that changes).
    const textBlock = (result?.content ?? []).find((b) => b.type === "text");
    return textBlock ? JSON.parse(textBlock.text) : {};
  }
}

export { AuthenticationError, ProtocolError, QuotaExceededError, StratifyError,
        ToolRefusalError, TransportError };
