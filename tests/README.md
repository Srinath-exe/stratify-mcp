# Automated testing

Four layers, in increasing cost and decreasing frequency. The point of the split is that
the cheap layers must stay cheap: a suite that takes minutes stops being run.

| Layer | What it proves | Cost | When |
|---|---|---|---|
| `pytest engine/tests server/tests` | behaviour that was reasoned about | free, ~30 s | every push |
| `tests/sweep.py --quick` (also in pytest) | invariants hold across a spec grid | free, ~7 s | every push |
| `tests/sweep.py` / `--full` / `--random` | the same, at breadth | free, minutes | nightly |
| `tests/persona_eval.py` | a real model reaches correct conclusions | ~$0.15–0.65 each | before a release |

## Why the sweep exists

On 2026-08-25 two bugs shipped past 231 passing tests. An "iron fly" whose short and long
PE legs landed on the same strike — a position that did not exist — reported **100.6 %
return on margin in one trade**. An iron condor produced credit, max loss and margin of
exactly zero, and that zero was then silently dropped from the mean-return-on-margin
denominator without the reader being told the average had changed shape.

Both were reachable from *default* parameters. They survived because every test picked its
specs by hand, and these bugs only appear where a particular spec meets a particular thin
minute in the data. No amount of care choosing examples finds that. Breadth does.

So `tests/invariants.py` states what must be true of any trade of any strategy, and
`sweep.py` applies it across a grid. **Every invariant names what it would have caught** —
one nobody can motivate gets deleted the first time it fails inconveniently.

On its first run the sweep found four things. Two were bugs in the invariants themselves
(a flat 25-strike-step bound flagged a legitimate 5 % offset; a `credit > 0` rule flagged a
genuine zero-credit fill). Two were real and are now fixed or disclosed: credit structures
entering at a **net debit** where the further-out leg printed a tick above the nearer one,
and entries collecting **under 2 % of the spread width** while blocking the full width of
margin. That ratio is 4 % of trades on a normal condor and 51 % on a 0.25 %-wide one — the
threshold was measured, not guessed.

## Why the persona evals exist

Everything above tests what the server *does*. None of it tests what a model *does with
it*, and that is where this product lives: the tool descriptions, the ambient instructions,
the refusals and the honesty panel exist to make a model reach a correct conclusion.

Each persona is a role and a goal handed to a real model through the `claude` CLI, with
this MCP server attached **and nothing else** — no filesystem, no shell, no web. It gets
what a real user's agent would get.

Scored in two independent layers, both of which must pass:

- **Trace checks** are deterministic and read the actual tool calls. `it never called
  describe_coverage`, `it passed cadence weekly for a question about every day`. Facts.
- **A judge** reads the transcript against the persona's rubric and answers what the trace
  cannot: did this person get what they came for, and were they misled. It is told to be
  adversarial, because an LLM judge's characteristic failure is calling a plausible answer
  a good one.

A run that satisfies every trace check and still leaves the user believing something false
is a failure. So is one that reaches a good answer without reading the coverage — that would
not survive a different week's data.

**Three of six personas are adversarial.** They want a recommendation, the option chain, and
a best-of-twenty headline with no mention of the search. For those, `satisfied=False` is the
target: the user should *not* get what they asked for, and the eval checks they were told
clearly why.

```bash
export STRATIFY_EVAL_KEY=sk_live_...
python3 tests/persona_eval.py                      # all six
python3 tests/persona_eval.py --only chain_scraper # one
python3 tests/persona_eval.py --no-judge           # trace checks only, free
```

## Not yet built

- **Protocol conformance replay.** The claude.ai connector broke on 2026-08-25 because
  `Authorization` is reserved by its OAuth flow and the server accepted no other header.
  Nothing in this suite would have caught it — that needs the real handshake sequences of
  real clients replayed against the server. `docs/TESTING_STRATEGY.md` L5 names the
  tooling (`mcp-compliance`, MCPJam for OAuth).
- **Load and soak.** `data/loadtest.py` exists and finds the knee; it is not scheduled.
- **An open question the sweep raised, deliberately not decided:** far out of the money on
  expiry day the chain is sparse enough that the nearest listed strike can sit 9 steps past
  the requested offset — measured at 2,337 points against a 1,900-point request. Unlike a
  drifted ATM this does not corrupt margin, and it is documented "nearest listed" behaviour,
  so it is carried as slack in the invariant rather than guarded. Whether an offset should
  be bounded the way ATM is remains a product question.
