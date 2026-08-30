"""JSON-RPC client for the Stratify MCP server.

There is no separate REST endpoint for running a backtest (server/app.py:9-16) -- every
tool, including run_backtest, is only reachable through POST /mcp as MCP JSON-RPC 2.0
tools/call. This client speaks that protocol directly rather than waiting on a future
OpenAPI layer, using nothing beyond the standard library for the HTTP leg so installing
this package pulls in exactly one real dependency: pandas, for the DataFrames quants
actually asked for (PRODUCT_REQUIREMENTS.md, distribution surfaces table).
"""
import json
import urllib.error
import urllib.request
from urllib.parse import urljoin

from .exceptions import (AuthenticationError, ProtocolError, QuotaExceededError,
                         ToolRefusalError, TransportError)
from .models import BacktestResult

DEFAULT_BASE_URL = "https://stratify-mcp.aeon-labs.site"
DEFAULT_TIMEOUT = 120  # seconds -- server/app.py's own MCP endpoint allows a 7-year
                       # backtest up to 120s at p95 under load (sites-available/stratify-mcp)

_UNAUTHENTICATED = -32001
_QUOTA_EXCEEDED = -32002


class StratifyClient:
    """One client per API key. Not thread-safe across concurrent calls sharing an
    account's quota is fine (the server enforces that), but this object holds no
    connection state, so making one per thread if you fan out is cheap and correct.

        client = StratifyClient(api_key="sk_live_...")
        result = client.run_backtest({
            "structure": "short_strangle", "symbol": "NIFTY",
            "params": {"pct_offset": 1.5, "sl_mult": 2.0},
            "entry_time": "09:30",
        })
        result.trades          # pandas.DataFrame
        result.equity_curve    # pandas.DataFrame
        result.summary["cagr"]
    """

    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT):
        if not api_key:
            raise ValueError("api_key is required -- get one from StratifyClient.signup()")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_id = 0

    # ---------------------------------------------------------------- public API

    def run_backtest(self, spec, lots=1, detail="standard"):
        """spec: a dict matching the public StrategySpec shape (see describe_coverage() or
        the server's knowledge base for the exact fields -- structure, params, entry_time,
        etc.). detail: 'summary' (aggregates only), 'standard' (default -- equity curve,
        breakdowns, first 25 trades), or 'full' (every trade up to the stored cap).
        Returns a BacktestResult."""
        payload = self._call("run_backtest",
                             {"spec": spec, "lots": lots, "detail": detail})
        return BacktestResult(payload)

    def get_backtest(self, backtest_id, detail=None):
        """Retrieve a previously run backtest by id, exactly as first computed."""
        args = {"backtest_id": backtest_id}
        if detail is not None:
            args["detail"] = detail
        return BacktestResult(self._call("get_backtest", args))

    def describe_coverage(self):
        """Symbols, date range, resolution, structures, gates, biases, cost model, known
        gaps. Call this before building a spec -- it is unauthenticated on the server side
        but routed the same way here for one consistent client surface."""
        return self._call("describe_coverage", {})

    def explain_methodology(self, topic=None):
        """How a result is produced and how to judge it. `topic` narrows to one section;
        omit it for the full document."""
        args = {"topic": topic} if topic else {}
        return self._call("explain_methodology", args)

    def list_strategies(self, order="consistency", limit=None):
        """This account's strategies that held up out-of-sample, ranked by worst
        walk-forward fold by default -- not by P&L. Returns the full response dict, not a
        bare list: {'strategies': [...], 'bar': {what qualifies, why not just P&L, ...},
        'n_entries': int, ...} -- the qualification bar travels with the list because a
        caller reading the empty-list case needs to know what it would take, not just that
        nothing qualified yet. Verified against a real (empty) account rather than
        assumed. Use client.list_strategies()['strategies'] for just the entries, or
        pandas.DataFrame(client.list_strategies()['strategies']) for a table."""
        args = {"order": order}
        if limit is not None:
            args["limit"] = limit
        return self._call("list_strategies", args)

    def search(self, query):
        """Search what this service covers. Returns ids usable with fetch()."""
        return self._call("search", {"query": query})

    def fetch(self, id):  # noqa: A002 -- matches the tool's own parameter name
        """Fetch a document or backtest result by id, as returned by search()."""
        return self._call("fetch", {"id": id})

    @staticmethod
    def signup(email, base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT):
        """Create an account and issue the first API key. No approval step (decision §0)
        -- returns immediately with a usable key. THE KEY IS SHOWN ONCE: store
        response['api_key'] yourself, the server does not let you recover it later.
        Returns the raw signup response dict (account_id, key_id, api_key, tier, limits,
        mcp_endpoint, ...) rather than a StratifyClient, since the two most common next
        steps -- print the key for a human, or hand it straight to StratifyClient(api_key=)
        -- both want the plain dict."""
        url = urljoin(base_url.rstrip("/") + "/", "v1/signup")
        body = json.dumps({"email": email}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail_body = exc.read()
            try:
                detail = json.loads(detail_body).get("error", detail_body.decode(errors="replace"))
            except Exception:
                detail = detail_body.decode(errors="replace")
            raise TransportError(f"signup failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"could not reach {url}: {exc.reason}") from exc

    # ---------------------------------------------------------------- transport

    def _call(self, tool_name, arguments):
        self._request_id += 1
        body = json.dumps({
            "jsonrpc": "2.0", "id": self._request_id, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }).encode()
        url = urljoin(self.base_url + "/", "mcp")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "stratify-py/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                message = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # The server returns non-2xx only for origin/size checks ahead of JSON-RPC
            # (app.py:_serve_mcp) -- a real tool refusal or quota hit still comes back 200
            # with a JSON-RPC body, handled below.
            raise TransportError(f"HTTP {exc.code} from {url}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"could not reach {url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise TransportError("server response was not valid JSON") from exc

        if "error" in message:
            err = message["error"]
            code, msg, data = err.get("code"), err.get("message", ""), err.get("data") or {}
            if code == _UNAUTHENTICATED:
                raise AuthenticationError(msg, data)
            if code == _QUOTA_EXCEEDED:
                raise QuotaExceededError(msg, limit=data.get("limit"),
                                         retry_after_seconds=data.get("retry_after_seconds"))
            raise ProtocolError(msg, code=code)

        result = message.get("result") or {}
        if result.get("isError"):
            text = "; ".join(b.get("text", "") for b in result.get("content", []))
            raise ToolRefusalError(text or "the server refused this call")

        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        # Fallback for a client/server pairing that only carries the text block -- parse
        # the same JSON the structuredContent would have held (server/app.py:_tool_text
        # always sends both today, but this keeps the client correct if that ever changes).
        for block in result.get("content", []):
            if block.get("type") == "text":
                return json.loads(block["text"])
        return {}
