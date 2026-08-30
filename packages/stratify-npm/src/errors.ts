/**
 * Mirrors packages/stratify-py/stratify/exceptions.py's three-way split (auth / quota /
 * refusal) plus a transport/protocol fallback -- same wire contract, same shape, so
 * someone who has used the Python client already knows this one.
 */

export class StratifyError extends Error {}

/** The request never got a JSON-RPC response at all: network failure, timeout, or a
 * non-2xx HTTP status from something other than this API. */
export class TransportError extends StratifyError {}

/** JSON-RPC error -32001. The key is missing, malformed, or revoked. */
export class AuthenticationError extends StratifyError {
  data: Record<string, unknown>;
  constructor(message: string, data: Record<string, unknown> = {}) {
    super(message);
    this.data = data;
  }
}

/** JSON-RPC error -32002. `limit` names which of the three dimensions (requests, CPU
 * seconds, price points) was hit; `retryAfterSeconds` is how long to back off. */
export class QuotaExceededError extends StratifyError {
  limit?: string;
  retryAfterSeconds?: number;
  constructor(message: string, limit?: string, retryAfterSeconds?: number) {
    super(message);
    this.limit = limit;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** Not a protocol error -- a successful JSON-RPC response with isError=true. The server
 * read the spec and refused it on its own terms (too narrow a window, an anti-oracle
 * floor, a malformed parameter). The message is written for a human or a model to read
 * and correct, so it is passed through unmodified. */
export class ToolRefusalError extends StratifyError {}

/** A JSON-RPC error this client did not anticipate. Carries the raw code rather than
 * pretending to a category the server didn't declare. */
export class ProtocolError extends StratifyError {
  code?: number;
  constructor(message: string, code?: number) {
    super(message);
    this.code = code;
  }
}
