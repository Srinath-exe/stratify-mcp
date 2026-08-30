# `docs` — the internal design record

These are working documents, frozen at the date in each header, not maintained
documentation. They are here because the reasoning behind a decision is usually more
useful than the decision, and because several of them are the only written account of why
a constant in `engine/config/` is the number it is.

Read them as history. Where a document disagrees with the code, **the code is right** —
and the disagreement is worth reporting.

| | |
|---|---|
| `DECISIONS.md` | 30 questions, resolved and frozen 2026-08-21. The closest thing to a spec. |
| `HIGH_LEVEL_DESIGN.md` | Shape of the system: tiers, layers, request path. |
| `LOW_LEVEL_DESIGN.md` | Table definitions, query plans, the response envelope. |
| `MCP_COMPATIBILITY.md` | What 1,538 registry servers actually do, and what we matched. |
| `TESTING_STRATEGY.md` | What is tested, and the failures each test exists to catch. |

Three things in them are deliberately stale, and are left rather than quietly rewritten:

- **Hostnames.** Planning used `mcp.stratify.io`. The live endpoint is
  `https://stratify-mcp.aeon-labs.site/mcp`.
- **Prices.** Current tiers are on the website; the figures frozen in `DECISIONS.md` have
  been superseded.
- **References to documents not published here** — a data audit, a performance analysis, a
  product requirements doc, an open-questions list. They are internal and are marked as
  such where cited.
