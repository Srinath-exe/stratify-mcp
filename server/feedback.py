"""User feedback: one intake, tagged, with enough context to act on without a reply.

WHY ONE ENDPOINT AND NOT FOUR. "Report a bug", "request a feature", "file a complaint" are
the same act from the reporter's side -- something is not as they want it -- and asking
them to pick the right form first is how you lose the report. One intake, and the
CATEGORY is a tag we assign (with the reporter's hint if they gave one), not a routing
decision we push onto them.

WHAT MAKES A REPORT ACTIONABLE. Not the prose. A report is actionable when it can be
reproduced, and reproduction needs the spec that was run and what the service said back.
`capture_context` attaches the reporter's recent calls automatically, so a triage agent
starts from "here is the failing call" rather than "here is a sentence about a failing
call". This is the whole difference between a feedback table and a feedback LOOP.

PRIVACY. The context snapshot holds the reporter's OWN calls and nothing else: tool names,
arguments they constructed, outcomes, and refusal text this service wrote. No other
account's data is reachable from a feedback row, and no market data is copied into one --
arguments are specs, which are the caller's input, not our output.
"""
import hashlib
import json
import re

from . import store

# The categories are the ones that lead to different actions. `praise` earns its place:
# knowing which parts people say are good is what stops a rewrite of the part that works.
CATEGORIES = {
    "bug":             "Something behaves incorrectly or crashes",
    "data_gap":        "Data is missing, wrong, or thinner than described",
    "feature_request": "Something that does not exist yet",
    "confusing":       "The output or documentation misled or did not explain enough",
    "performance":     "Too slow, timed out, or hit a limit that felt wrong",
    "pricing":         "Tier boundary, quota, or billing",
    "praise":          "Something worked well and should not be changed",
    "other":           "Anything that does not fit the above",
}

SEVERITIES = {
    "blocker": "Cannot use the service at all",
    "major":   "A real workflow is broken or a result cannot be trusted",
    "minor":   "Annoying, worked around",
    "idea":    "No impact today; a suggestion",
}

STATUSES = ("new", "reopened", "triaged", "in_progress", "fixed", "wontfix", "duplicate")

MAX_TITLE, MAX_BODY = 160, 4000
CONTEXT_CALLS = 5


class FeedbackError(ValueError):
    """A malformed report. Carries a message the caller can act on."""


# Words that reliably indicate a category when the reporter did not name one. This is a
# HINT layer, never an override: an explicit category from the caller always wins, and the
# guess is recorded as a tag so triage can see it was a guess.
_HINTS = (
    ("bug", re.compile(r"\b(crash|error|traceback|broke|broken|wrong|incorrect|"
                       r"mismatch|nan|exception|failed|fails|bug)\b", re.I)),
    ("data_gap", re.compile(r"\b(missing|gap|no data|not covered|empty|absent|"
                            r"unavailable|holes?)\b", re.I)),
    ("performance", re.compile(r"\b(slow|timeout|timed out|hang|hung|took \d+|"
                               r"rate.?limit|429|quota)\b", re.I)),
    ("pricing", re.compile(r"\b(price|pricing|billing|subscription|refund|upgrade|"
                           r"paid tier|free tier|charge[ds]?)\b", re.I)),
    ("confusing", re.compile(r"\b(confus\w+|unclear|misleading|don'?t understand|"
                             r"documentation|docs|explain)\b", re.I)),
    ("feature_request", re.compile(r"\b(would (be (nice|great)|like|love)|please add|"
                                   r"can (you|we) add|support for|wish|feature request|"
                                   r"ability to|allow me|it should also|add \w+ support|"
                                   r"any plans? (to|for)|hoping for)\b", re.I)),
    ("praise", re.compile(r"\b(love|great|excellent|works well|impressive|thank you|"
                          r"exactly what)\b", re.I)),
)

_SEVERITY_HINTS = (
    ("blocker", re.compile(r"\b(cannot use|unusable|completely broken|blocked|"
                           r"every (call|request) fails)\b", re.I)),
    ("major", re.compile(r"\b(wrong (number|result|p&?l)|cannot trust|"
                         r"lost (money|data)|silently)\b", re.I)),
)


def classify(title, body, given=None):
    """Return (category, tags). Explicit beats inferred, always."""
    text = f"{title}\n{body}"
    tags = []
    guessed = next((name for name, pattern in _HINTS if pattern.search(text)), None)
    if given in CATEGORIES:
        category = given
        if guessed and guessed != given:
            # Worth recording: a systematic gap between what people call a thing and what
            # it turns out to be tells you the intake wording is wrong.
            tags.append(f"looks-like:{guessed}")
    else:
        category = guessed or "other"
        tags.append("category:inferred")
    for name, pattern in _HINTS:
        if name != category and pattern.search(text):
            tags.append(f"also:{name}")
    return category, tags


def infer_severity(title, body, category, given=None):
    if given in SEVERITIES:
        return given
    text = f"{title}\n{body}"
    for name, pattern in _SEVERITY_HINTS:
        if pattern.search(text):
            return name
    if category == "feature_request":
        return "idea"
    if category == "praise":
        return "idea"
    if category in ("bug", "data_gap"):
        return "major"
    return "minor"


# Normalisation for the dedup key. Numbers, ids and dates are stripped because "no data
# for 2019-03-14" and "no data for 2021-07-02" are the same report; keeping the digits
# would make every instance of a systemic gap look like a fresh, unique issue.
_NOISE = re.compile(r"\b(?:\d[\d:_/.\-]*|bt_[0-9a-f]+|sk_live_\S+|fb_[0-9a-f]+)\b", re.I)
_PUNCT = re.compile(r"[^a-z ]+")
_STOP = {"the", "a", "an", "is", "it", "to", "of", "for", "on", "in", "and", "or", "but",
         "i", "we", "my", "this", "that", "with", "was", "were", "be", "been", "when",
         "does", "did", "not", "no", "you", "your", "at", "as", "by", "from", "if", "so"}


def dedup_key(title, category=None):
    """Identity of a report, from its TITLE ALONE.

    The category is deliberately NOT part of the key. It used to be, and the category is
    inferred from the body -- so the same complaint filed twice with differently-worded
    bodies was classified two ways, hashed two ways, and stored twice. The title is what
    the reporter thinks the problem is, it is stable across re-filings, and it is the only
    part of a report that identifies it. `category` stays in the signature because callers
    pass it positionally; it is ignored.
    """
    words = _PUNCT.sub(" ", _NOISE.sub(" ", title.lower())).split()
    core = sorted({w for w in words if w not in _STOP and len(w) > 2})
    if not core:
        core = words[:4]
    return hashlib.sha256(" ".join(core).encode()).hexdigest()[:16]


def capture_context(account_id, backtest_id=None):
    """The reporter's recent calls, so triage can reproduce without asking them.

    Arguments are already redacted by the audit layer that wrote them, and are the
    caller's own specs. Refusal text is ours. Nothing here crosses an account boundary.
    """
    try:
        calls = store.recent_calls(account_id, limit=CONTEXT_CALLS)
    except Exception:                       # noqa: BLE001
        return None
    trail = [{"ts": c["ts"], "tool": c["tool"], "outcome": c["outcome"],
              "refusal": c["refusal"], "backtest_id": c["backtest_id"],
              "arguments": _load(c["arguments_json"]), "client": c.get("client")}
             for c in calls]
    context = {"recent_calls": trail}
    if backtest_id:
        row = store.get_result(backtest_id)
        # Only if the reporter owns it. A feedback report must not become a way to read
        # someone else's backtest by guessing an id.
        if row is not None and store.key_owner(row["key_id"]) == account_id:
            context["spec"] = _load(row["spec_json"])
        else:
            context["spec_note"] = "backtest_id not found on this account"
    return json.dumps(context, separators=(",", ":"))[:20000]


def _load(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def submit(account_id, tier, title, body, category=None, severity=None,
           backtest_id=None, source="mcp"):
    """Validate, classify, capture context, store. Returns the stored row plus is_new."""
    title = (title or "").strip()
    body = (body or "").strip()
    if len(title) < 4:
        raise FeedbackError("A title of at least 4 characters is required — one line "
                            "naming the problem, e.g. 'iron condor margin looks too high'.")
    if not body:
        # Without a body there is nothing to reproduce from, and a title-only queue is a
        # queue nobody can work. Better to refuse at intake than to store an unactionable
        # row and discover it at triage.
        raise FeedbackError("A body is required: what you expected, what happened, and "
                            "the spec or backtest_id if a result was involved.")
    if category is not None and category not in CATEGORIES:
        raise FeedbackError(f"category must be one of {sorted(CATEGORIES)}")
    if severity is not None and severity not in SEVERITIES:
        raise FeedbackError(f"severity must be one of {sorted(SEVERITIES)}")
    title, body = title[:MAX_TITLE], body[:MAX_BODY]

    category, tags = classify(title, body, category)
    severity = infer_severity(title, body, category, severity)
    entry = {
        "account_id": account_id, "tier": tier, "source": source,
        "category": category, "tags_json": json.dumps(tags), "severity": severity,
        "title": title, "body": body, "backtest_id": backtest_id or None,
        "context_json": capture_context(account_id, backtest_id),
        "dedup_key": dedup_key(title, category),
    }
    feedback_id, is_new = store.record_feedback(entry)
    return {"feedback_id": feedback_id, "is_new": is_new, "category": category,
            "severity": severity, "tags": tags, "status": "new" if is_new else "updated"}
