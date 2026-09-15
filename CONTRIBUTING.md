# Contributing

Thanks for looking. The most useful thing you can do here is **check our arithmetic** — this is a backtesting engine, and a wrong number that looks plausible is the failure mode that matters.

## The one thing to understand first

**The data is not in this repository and never will be.** Market data stays on the server; you send a strategy and get results back. That means:

- Roughly **219 of the 536 tests cannot run** on a clone. They need a populated ClickHouse.
- `conftest.py` turns those into skips *with a reason*, and only when the input is confirmed absent. A failure on your machine is a real failure.
- The same applies to the three optional runtime inputs in [vendor/README.md](vendor/README.md) — the signals module, the SPAN calibration table and the paper-trading book.
- Run `pytest -rs` to see exactly what was skipped and why.

```bash
pip install -r requirements-dev.txt
python -m pytest engine/tests server/tests -q -rs
# expect 317 passed, 219 skipped, 0 failed
```

If you see failures rather than skips, something is genuinely broken — please open an issue.

## Setup

```bash
# Python service. requirements.txt is what the SERVICE needs; -dev adds the test
# dependencies, which are deliberately kept out of the runtime image.
pip install -r requirements-dev.txt
cp .env.example .env          # STRATIFY_KEY_PEPPER is required; run.sh refuses without it

# npm client
cd packages/stratify-npm && npm ci && npm test

# Python client
cd packages/stratify-py && pip install -e ".[dev]" && python -m pytest tests -q
```

## House style

The code here is heavily commented, and deliberately so: a constant in `engine/config/` is either **measured** and says where from, or an **assumption** and says so. Please keep that up.

- **Explain why, not what.** `# 30, because that is the honesty panel's floor` earns its line. `# set x to 30` does not.
- **When you fix a bug, say what it did.** The comments that have saved the most time here are the ones naming the symptom — "this guard failed to one character until 2026-08-30", "two chart resolutions had never once executed". Someone reading later needs the failure, not just the fix.
- Match the surrounding code. Four spaces, ~90 columns, no linter is enforced.

## What needs care

**Anything that changes a number.** If your change means an unchanged spec returns different results, bump `VERSION` in `engine/methodology.py` and add a changelog entry with `affects_results: true`. This is not bookkeeping — the strategy book is keyed on a hash that includes that version, and without a bump a user's recorded statistics get silently overwritten with numbers from a different model. `engine/tests/test_methodology.py` enforces what it can.

Bump for: cost model, margin, slippage, fills, exit resolution, strike selection.
Do **not** bump for: a new tool, a faster query, better wording, a refactor with identical output.

**Anything that touches the data boundary.** No route may return market data. There are tests asserting this and they are not negotiable.

**Anything user-supplied that reaches SQL or HTML.** `engine/exits.py` builds SQL by interpolation and is safe *only* because every value is closed-vocabulary or integer-cast at `engine/strategy.py:parse_leg`. If you widen what a caller can put in a spec, check that path.

## Pull requests

1. Branch from `main`.
2. Make CI green — three jobs: Python, npm client, Python client.
3. Explain the behaviour change in the PR body, not just the diff.
4. If you fixed a bug, add the test that would have caught it. This matters more than the fix.

## Security

Do not open a public issue for anything that lets a caller read data, escalate a tier, or reach another account's records. See [SECURITY.md](SECURITY.md).

## Releasing (maintainers)

Publishing happens in CI, from a tag, and never from a laptop — `npx stratify-mcp` runs on other people's machines, so publish credentials are the most dangerous thing this project has.

```bash
# 1. Run the FULL suite against a real database first. CI cannot do this.
python -m pytest engine/tests server/tests -q        # expect 536 passed, 0 skipped

# 2. Bump both package versions to match. Release CI fails if the tag disagrees.
#    packages/stratify-npm/package.json  ·  packages/stratify-py/pyproject.toml

# 3. Update CHANGELOG.md, then tag.
git tag v0.1.1 && git push origin v0.1.1
```

`release.yml` re-runs everything, then publishes to PyPI. npm is published from the monorepo for now (a tag `npm-v<version>` there) -- `packages/stratify-npm/PUBLISHING.md` explains why and how to fold it back. Both use trusted publishing: no token is stored anywhere, the workflow's OIDC identity is exchanged for a one-shot credential.
