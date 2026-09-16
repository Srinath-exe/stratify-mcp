# Changelog

Notable changes to the server and both clients. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**This is not the methodology changelog.** A release can change how a number is computed,
and that is tracked separately and machine-readably in `engine/methodology.py`, surfaced
through `explain_methodology("changelog")`. Entries here that move numbers say so and
point at the methodology version.

## [Unreleased]

### Added
- **OAuth 2.1 authorization server** (`server/mcpauth.py`): PKCE S256 required, dynamic
  client registration, client-ID metadata documents, exact redirect matching with the
  RFC 8252 loopback exception, single-use codes with replay detection, refresh rotation
  with reuse detection. Unauthenticated `tools/call` now answers 401 + `WWW-Authenticate`.
- Admin-issued password sign-in at `/login`, for directory reviewers. Not a signup.
- Index indicators as entry gates in the open protocol: `rsi_N`, `close_vs_sma_N_pct`,
  `close_vs_ema_N_pct`, `ema_F_vs_S_pct`, `sma_F_vs_S_pct`, N 2..250, all computed on the
  previous close so a gate can never see its own day.
- The site on the report's design system; `/explore`, `/pricing`, a waitlist, the mark.
- One report renderer (`server/reportui.py`) for `/r/{token}` and `format: "full"`, with
  a browser-side capital view and touch-friendly info overlays.
- `/readyz` (ClickHouse window + service-DB write probe) beside the dependency-free
  `/healthz`; `server/backup.sh` (restore-tested) and `server/watchdog.sh`.
- Backtests and metadata calls metered separately (`metadata_requests_per_hour`), so
  reading coverage and methodology never spends the backtest allowance.
- Tool annotations (`title`, `readOnlyHint`, `destructiveHint`) on every tool.

### Fixed
- Every page handed out the MCP URL on the *web* host, which worked and silently broke
  OAuth discovery. `STRATIFY_PUBLIC_MCP_URL` is authoritative.
- Reports older than 30 days are purged; a purged link answers 410 with a page.

## [0.1.2] — 2026-09-15

### Added
- `mcpName` in the npm package and an `mcp-name` marker in the Python README, so the
  official MCP Registry can verify the packages belong to `site.aeon-labs/stratify`.
- `packages/server.json` now lists both the hosted endpoint and the npm stdio bridge.
- `packages/mcpb`: a Claude Desktop extension bundle built from the npm bridge.
- `packages/gemini-extension`: version 0.1.2, mirrored to its own repository for the
  Gemini CLI gallery.
- The Stratify mark: `/static/icon.svg`, `/static/icon-{64,192,512}.png`, `/favicon.ico`.

### Fixed
- `bin` paths in `package.json` no longer start with `./`, which npm 11 strips on publish.

## [0.1.1] — 2026-09-15

First tagged release. `0.1.0` of the npm client was published by hand to bootstrap
trusted publishing; from here both clients ship from a tag.

### Added
- Methodology versioning. Every result now carries a stamp — version, slippage basis and
  margin calibration date — so a stored figure can be attributed to the model that
  produced it. The version is inside `spec_hash`, so re-running a spec after a
  methodology change creates a new strategy-book entry instead of silently overwriting
  the old one's statistics. New topic: `explain_methodology("changelog")`.
- `conftest.py` makes the suite runnable without the serving database: 317 pass, 219 skip
  with a reason naming the database, 0 fail.
- CI on every push and PR; releases publish from a tag. Both registries use trusted
  publishing -- no stored token anywhere.

### Fixed
- **Open redirect** in the Google sign-in flow. `_safe_next` rejected `//evil` but not
  `/\evil`; browsers normalise the backslash and followed it off-site. The existing test
  had covered only the obvious form.
- The npm tarball was shipping the test suite — `tsconfig` compiled `src` and `test` into
  one `dist/` and `files: ["dist"]` swept up both. 14 files to 10, 12.5 kB to 8.9 kB.
- Tests resolved the package through a *sibling* path, so any other checkout layout
  silently tested a different copy of the code.
- A leg `label` was unbounded free text of any JSON type. Now text, 60 characters.
- `server/preview.py` no longer defaults its output into a webroot.

## [0.1.0] — 2026-09-15

First release: MCP server, npm client and stdio bridge, Python client. npm only.
