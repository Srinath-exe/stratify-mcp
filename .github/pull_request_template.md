## What this changes

<!-- The behaviour change, not the diff. A reviewer can read the diff. -->

## Why

<!-- If it fixes a bug: what did the bug DO? The symptom is what someone will search for
     later. "Margin ignored quantity, so 100 short calls blocked the same capital as one." -->

## Does this change any number?

- [ ] No — refactor, new tool, wording, or performance with identical output
- [ ] **Yes** — an unchanged spec can now return different results

If yes, all of these must be true before merge:

- [ ] `VERSION` bumped in `engine/methodology.py`
- [ ] `CHANGELOG` entry added there with `affects_results: true`
- [ ] The entry says what moved and, where measurable, by how much

<!-- This is not bookkeeping. The strategy book is keyed on a hash containing that
     version. Without a bump, a user's recorded statistics are silently overwritten with
     numbers from a different model. -->

## Testing

- [ ] `python -m pytest engine/tests server/tests -q -rs` passes
- [ ] Ran against a real database (state the result), or say you could not
- [ ] A bug fix includes the test that would have caught it

<!-- Without a database expect ~319 passed, ~208 skipped, 0 failed. Failures are real. -->

## Boundaries

- [ ] No route returns market data
- [ ] No new user-supplied value reaches SQL or HTML without passing the closed-vocabulary
      validation in `engine/strategy.py`, or being escaped
