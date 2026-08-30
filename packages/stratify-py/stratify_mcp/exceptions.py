"""Exceptions the client raises.

Mirrors the three distinct failure shapes server/app.py actually produces (see its
_handle() dispatcher) rather than collapsing them into one generic error -- a caller needs
to tell "your key is wrong" from "you're over quota, retry in N seconds" from "the server
looked at your spec and said no" apart, and each needs different handling code.
"""


class StratifyError(Exception):
    """Base class for every error this client raises."""


class TransportError(StratifyError):
    """The request never got a JSON-RPC response at all -- a network failure, a timeout,
    or a non-2xx HTTP status from something other than this API (a proxy, a CDN page)."""


class AuthenticationError(StratifyError):
    """JSON-RPC error -32001. The key is missing, malformed, or revoked. `how_to_fix` and
    `alternative_headers`, when the server sent them, are on `.data`."""

    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data or {}


class QuotaExceededError(StratifyError):
    """JSON-RPC error -32002. `limit` names which of the three dimensions (requests, CPU
    seconds, price points) was hit; `retry_after_seconds` is how long to back off."""

    def __init__(self, message, limit=None, retry_after_seconds=None):
        super().__init__(message)
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds


class ToolRefusalError(StratifyError):
    """Not a protocol error -- a successful JSON-RPC response with isError=true. This is
    the server having read the spec and refused it on its own terms: too narrow a window,
    an anti-oracle floor, a malformed structure parameter. The message is written for a
    human (or a model) to read and correct, so it is passed through unmodified."""


class ProtocolError(StratifyError):
    """A JSON-RPC error this client did not anticipate (unknown method, malformed request,
    internal server error). Carries the raw code and message rather than pretending to a
    category the server didn't declare."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code
