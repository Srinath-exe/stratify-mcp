"""Unit tests against a mocked transport -- no live server required. Each test patches
urllib.request.urlopen to hand back exactly the JSON-RPC envelope server/app.py would
produce for that scenario (copied from reading app.py's _handle()/tools.py directly, not
guessed), so these pin the wire contract this client actually depends on.
"""
import json
import urllib.error
from unittest.mock import patch

import pytest

from stratify_mcp import (AuthenticationError, BacktestResult, ProtocolError,
                      QuotaExceededError, StratifyClient, ToolRefusalError)


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _rpc_result(structured):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(structured)}],
                      "structuredContent": structured, "isError": False}}


def _rpc_error(code, message, data=None):
    err = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": 1, "error": err}


def _rpc_refusal(message):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": message}], "isError": True}}


def _client():
    return StratifyClient(api_key="sk_live_test", base_url="https://example.invalid")


def test_run_backtest_returns_backtest_result_with_dataframes():
    payload = {
        "backtest_id": "bt_123", "report_url": "https://example.invalid/r/tok",
        "summary": {"cagr": 0.42, "n_trades": 40},
        "honesty": {"oos_split": "70/30"},
        "trades": [{"n": 1, "entry": "2025-07-01 09:30", "pnl_pts": 12.5},
                   {"n": 2, "entry": "2025-07-08 09:30", "pnl_pts": -4.0}],
        "equity_curve": {"columns": ["date", "equity_rupees"],
                         "rows": [["2025-07-01", 100000], ["2025-07-08", 101250]]},
        "strategy_book": {"qualified": True},
    }
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_rpc_result(payload))) as m:
        result = _client().run_backtest(
            {"structure": "short_strangle", "params": {"pct_offset": 1.5}})

    assert isinstance(result, BacktestResult)
    assert result.backtest_id == "bt_123"
    assert result.qualified is True
    assert result.summary["cagr"] == 0.42
    assert len(result.trades) == 2
    assert list(result.trades["pnl_pts"]) == [12.5, -4.0]
    assert list(result.equity_curve.columns) == ["date", "equity_rupees"]
    assert len(result.equity_curve) == 2

    # Confirms the actual wire request shape, not just the response handling.
    sent_body = json.loads(m.call_args[0][0].data)
    assert sent_body["method"] == "tools/call"
    assert sent_body["params"]["name"] == "run_backtest"
    assert sent_body["params"]["arguments"]["spec"]["structure"] == "short_strangle"
    assert sent_body["params"]["arguments"]["detail"] == "standard"
    assert m.call_args[0][0].get_header("Authorization") == "Bearer sk_live_test"


def test_summary_detail_has_no_trades_but_does_not_raise():
    payload = {"backtest_id": "bt_999", "summary": {"cagr": 0.1},
              "strategy_book": {"qualified": False, "note": "n_trades below 30"}}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_rpc_result(payload))):
        result = _client().run_backtest({"structure": "long_option", "params": {}},
                                        detail="summary")
    assert result.qualified is False
    assert result.why_not_qualified == "n_trades below 30"
    assert result.trades.empty
    assert result.equity_curve.empty


def test_authentication_error():
    body = _rpc_error(-32001, "missing or invalid API key",
                      {"how_to_fix": "POST /v1/signup ..."})
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(AuthenticationError) as exc_info:
            _client().describe_coverage()
    assert "how_to_fix" in exc_info.value.data


def test_quota_exceeded_error_carries_limit_and_retry_after():
    body = _rpc_error(-32002, "cpu_seconds_per_hour exceeded",
                      {"limit": "cpu_seconds_per_hour", "retry_after_seconds": 120})
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(QuotaExceededError) as exc_info:
            _client().run_backtest({"structure": "credit_spread", "params": {}})
    assert exc_info.value.limit == "cpu_seconds_per_hour"
    assert exc_info.value.retry_after_seconds == 120


def test_tool_refusal_is_not_a_protocol_error():
    body = _rpc_refusal("this strategy's queries touched only 4 distinct contracts; at "
                        "least 20 are required. Widen the period or the strike range")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ToolRefusalError, match="distinct contracts"):
            _client().run_backtest({"structure": "iron_condor", "params": {}})


def test_unrecognised_error_code_becomes_protocol_error():
    body = _rpc_error(-32603, "internal error")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        with pytest.raises(ProtocolError) as exc_info:
            _client().describe_coverage()
    assert exc_info.value.code == -32603


def test_describe_coverage_search_fetch_list_strategies_send_correct_tool_names():
    for method_call, tool_name, expected_args in [
        (lambda c: c.describe_coverage(), "describe_coverage", {}),
        (lambda c: c.explain_methodology("margin"), "explain_methodology", {"topic": "margin"}),
        (lambda c: c.search("SENSEX weekly"), "search", {"query": "SENSEX weekly"}),
        (lambda c: c.fetch("bt_1"), "fetch", {"id": "bt_1"}),
        (lambda c: c.list_strategies(order="pnl", limit=5), "list_strategies",
         {"order": "pnl", "limit": 5}),
    ]:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(_rpc_result({"ok": True}))) as m:
            method_call(_client())
        sent = json.loads(m.call_args[0][0].data)
        assert sent["params"]["name"] == tool_name
        assert sent["params"]["arguments"] == expected_args


def test_get_backtest_returns_backtest_result():
    payload = {"backtest_id": "bt_777", "summary": {"cagr": 0.2},
              "trades": [], "equity_curve": {"columns": [], "rows": []},
              "strategy_book": {"qualified": False, "note": "x"}}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_rpc_result(payload))) as m:
        result = _client().get_backtest("bt_777", detail="full")
    assert isinstance(result, BacktestResult)
    assert result.backtest_id == "bt_777"
    sent = json.loads(m.call_args[0][0].data)
    assert sent["params"]["arguments"] == {"backtest_id": "bt_777", "detail": "full"}


def test_signup_returns_raw_dict():
    body = {"account_id": "acc_1", "key_id": "key_1", "api_key": "sk_live_new",
           "tier": "free", "mcp_endpoint": "https://example.invalid/mcp"}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)) as m:
        result = StratifyClient.signup("me@example.com", base_url="https://example.invalid")
    assert result["api_key"] == "sk_live_new"
    sent = json.loads(m.call_args[0][0].data)
    assert sent == {"email": "me@example.com"}


def test_signup_failure_raises_transport_error():
    from stratify_mcp import TransportError

    def raise_http_error(*a, **kw):
        raise urllib.error.HTTPError(
            "https://example.invalid/v1/signup", 400, "bad request", {},
            fp=__import__("io").BytesIO(json.dumps({"error": "a valid email is required"}).encode()))

    with patch("urllib.request.urlopen", side_effect=raise_http_error):
        with pytest.raises(TransportError, match="a valid email is required"):
            StratifyClient.signup("not-an-email", base_url="https://example.invalid")


def test_missing_api_key_raises_immediately():
    with pytest.raises(ValueError):
        StratifyClient(api_key="")


def test_repr_uses_real_field_names_not_a_nonexistent_cagr():
    """Field names verified against a real local server run, not assumed: there is no
    'cagr' in the actual summary payload -- the real return metric is total_pnl_rupees."""
    result = BacktestResult({"backtest_id": "bt_1",
                             "summary": {"total_pnl_rupees": 6584.19, "n_trades": 50}})
    text = repr(result)
    assert "bt_1" in text
    assert "6,584" in text or "6584" in text
    assert "n_trades=50" in text


def test_repr_handles_insufficient_sample_without_crashing():
    result = BacktestResult({"backtest_id": "bt_2", "summary": {}})
    assert "insufficient_sample" in repr(result)
