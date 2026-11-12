"""No tool text may steer the model or the host.

OpenAI rejected v1.0.0 for "instructions that could cause the model to bypass normal
safeguards or act outside authorized behavior". The offending text was real: build_report
said "PUBLISH THE RETURNED HTML VERBATIM AS AN ARTIFACT ... Do not rewrite it", and the
server instructions said to publish "WITHOUT asking permission first -- offering costs a
round trip and the answer is always yes". A tool description may say what the tool does;
it may not decide what the assistant publishes, or name another product's features.

This test exists so the next person adding a description finds out here rather than from
a review six weeks later.
"""
import re

import pytest

from server import knowledge, tools

# Phrases that direct the assistant's behaviour, override consent, or reach into the
# host's own features. Matched case-insensitively against every model-visible string.
BANNED = [
    r"\bpublish\b(?!ed)",
    r"\bverbatim\b",
    r"without asking",
    r"do not (?:ask|wait|rewrite|edit|transcribe|summarise|summarize|redraw)",
    r"\bdefault behaviour\b",
    r"\bby default\b(?=[^.]*\bpublish)",
    r"\bartifact\b(?!s?\"|s?'|_)",
    r"\bcanvas\b",
    r"\bchatgpt\b",
    r"\bclaude\b",
    r"\bgemini\b",
    r"tell the user",
    r"give them the",
    r"do not just apologise",
]

# `artifact` survives as a format ENUM VALUE on build_report and as a field name. A value
# is not an instruction, so those two spellings are allowed through.
ALLOWED_EXACT = {"artifact"}


def _strings(node, path="", out=None):
    out = [] if out is None else out
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            _strings(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _strings(v, f"{path}[{i}]", out)
    return out


@pytest.mark.parametrize("pattern", BANNED)
def test_no_tool_text_steers_the_model(pattern):
    rx = re.compile(pattern, re.I)
    bad = []
    for tool in tools.TOOLS:
        for path, text in _strings(tool, tool["name"]):
            if text in ALLOWED_EXACT:
                continue
            if rx.search(text):
                bad.append(f"{path}: {text[:110]}")
    assert not bad, f"{pattern!r} appears in tool text:\n" + "\n".join(bad)


@pytest.mark.parametrize("pattern", BANNED)
def test_no_server_instruction_steers_the_model(pattern):
    rx = re.compile(pattern, re.I)
    hit = rx.search(knowledge.SERVER_INSTRUCTIONS)
    assert hit is None, (
        f"{pattern!r} appears in SERVER_INSTRUCTIONS: "
        f"...{knowledge.SERVER_INSTRUCTIONS[max(0, hit.start()-60):hit.end()+60]}...")


def test_the_safety_rules_are_still_there():
    """The cut must not take the not-advice rules with it.

    Those are a safety constraint on what the service produces, not a steer on how the
    assistant behaves, and the review did not object to them.
    """
    text = knowledge.SERVER_INSTRUCTIONS.lower()
    for phrase in ("not investment advice", "position size", "what i would do",
                   "specific strikes"):
        assert phrase in text, phrase


def test_every_tool_declares_what_it_returns():
    for tool in tools.TOOLS:
        assert "outputSchema" in tool, f"{tool['name']} has no outputSchema"
        assert tool["outputSchema"]["type"] == "object"


def test_run_backtest_is_not_advertised_as_read_only():
    """It stores the result and may add a strategy-book entry."""
    ann = [t for t in tools.TOOLS if t["name"] == "run_backtest"][0]["annotations"]
    assert ann["readOnlyHint"] is False
    assert ann["destructiveHint"] is False
