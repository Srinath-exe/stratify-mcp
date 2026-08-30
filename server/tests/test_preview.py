"""The report preview loop.

What is worth testing here is not the HTML -- test_server.py already asserts the report's
structure -- but the two properties that make the loop trustworthy:

  1. rendering is a pure function of a frozen fixture, so it needs no database. If this
     breaks, iterating on the layout silently starts costing four seconds and a live
     ClickHouse again.
  2. a fixture built at one tier is not quietly a fixture at another. The DB enforces the
     date window as a row policy on a per-tier user, so asking for 2019 with the free user
     returns 2025 onward and no error -- the exact bug that made the first capture a
     one-year "seven-year" report.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import preview  # noqa: E402


def _fixture(tmp_path, n_trades=4):
    """A hand-built fixture. Small on purpose: this must not need the engine either."""
    trades = [{"entry": f"2025-0{i + 1}-06 09:30", "exit": f"2025-0{i + 1}-08 15:29",
               "pnl_rupees": 900.0 if i % 2 else -400.0, "margin_points": 120.0,
               "lot_size": 75, "spot_at_entry": 23000 + i * 50,
               "exit_reason": "expiry"} for i in range(n_trades)]
    doc = {
        "name": "t", "spec": {"structure": "iron_condor"}, "captured_at": "2026-01-01",
        "backtest_id": "preview_t",
        "payload": {"spec": {"structure": "iron_condor",
                             "params": {"pct_offset": 1.0, "pct_width": 1.0}},
                    "summary": {"period": {"from": "2025-01-01", "to": "2025-05-01"},
                                "win_rate": 0.5, "ratios": {"sharpe": 0.4}},
                    "honesty": {"verdict": "weak", "health_score": 30,
                                "explanation": "thin evidence"},
                    "interpretation": {"reading": ["a reading"], "do_not_conclude": ["no"]}},
        "chart_rows": trades,
        "candles": [{"time": f"2025-01-0{i + 1}", "open": 23000, "high": 23100,
                     "low": 22900, "close": 23050} for i in range(9)],
    }
    preview.FIXTURES = tmp_path
    (tmp_path / "t.json").write_text(json.dumps(doc))
    return doc


def test_build_renders_from_a_fixture_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "FIXTURES", tmp_path)
    _fixture(tmp_path)
    out = preview.build("t", out=tmp_path / "out")
    doc = out.read_text()
    assert doc.startswith("<!doctype html>")
    assert "Iron Condor" in doc and "Not investment advice" in doc


def test_build_never_touches_the_database(tmp_path, monkeypatch):
    """The whole point of the loop. A fixture render that opens a connection is a bug."""
    monkeypatch.setattr(preview, "FIXTURES", tmp_path)
    _fixture(tmp_path)
    from engine import db
    monkeypatch.setattr(db, "rows", lambda *a, **k: pytest.fail("preview queried the DB"))
    preview.build("t", out=tmp_path / "out")


def test_dev_build_writes_a_stamp_and_injects_the_reloader(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "FIXTURES", tmp_path)
    _fixture(tmp_path)
    out = preview.build("t", out=tmp_path / "out", dev=True)
    assert (tmp_path / "out" / "t.stamp").exists()
    assert "data-preview-reload" in out.read_text()


def test_a_plain_build_carries_no_preview_furniture(tmp_path, monkeypatch):
    """`build` without --dev must produce exactly what the product serves."""
    monkeypatch.setattr(preview, "FIXTURES", tmp_path)
    _fixture(tmp_path)
    doc = preview.build("t", out=tmp_path / "out").read_text()
    assert "data-preview-reload" not in doc and "PREVIEW &middot;" not in doc


def test_capital_and_deploy_reach_the_render(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "FIXTURES", tmp_path)
    _fixture(tmp_path)
    doc = preview.build("t", out=tmp_path / "out", capital=5_000_000, deploy=25).read_text()
    assert "₹50.00 L" in doc and "25% of running capital" in doc


def test_missing_fixture_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "FIXTURES", tmp_path)
    with pytest.raises(SystemExit) as e:
        preview.build("nope", out=tmp_path / "out")
    assert "preview capture" in str(e.value)


def test_every_demo_spec_is_a_spec_the_engine_accepts():
    """The 20 mock sessions were all written against an API that did not exist. Same trap:
    a demo spec that no longer parses turns into a capture failure at the worst moment."""
    import datetime as dt
    from engine import spec as spec_mod, strategy as strategy_mod
    for name, raw in preview.DEMO_SPECS.items():
        if "legs" in raw:
            # The open protocol. There is no declared structure to check against -- the
            # shape is DERIVED from the legs -- so the assertion is that it parses and
            # names itself.
            parsed = strategy_mod.parse(
                raw, window=(dt.date(2019, 1, 1), dt.date(2026, 6, 30)))
            assert parsed.structure and parsed.n_legs >= 1, name
        else:
            parsed = spec_mod.parse(raw, tier="pro")
            assert parsed.structure == raw["structure"], name


def test_demo_specs_ask_for_the_full_history():
    """A fixture is only useful if it covers the period the report claims to cover."""
    for name, raw in preview.DEMO_SPECS.items():
        assert (raw.get("period") or {}).get("from") == "2019-01-01", name


def test_capture_binds_the_clickhouse_tier_user():
    """spec.parse(tier=...) widens the DATE WINDOW; the row policy is enforced separately
    on a per-tier DB user. Capturing without binding it returns one year, silently."""
    src = (Path(preview.__file__)).read_text()
    assert "db.use_tier(tier)" in src
