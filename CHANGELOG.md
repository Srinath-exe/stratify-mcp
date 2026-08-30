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
- Methodology versioning. Every result now carries a stamp — version, slippage basis and
  margin calibration date — so a stored figure can be attributed to the model that
  produced it. The version is inside `spec_hash`, so re-running a spec after a
  methodology change creates a new strategy-book entry instead of silently overwriting
  the old one's statistics. New topic: `explain_methodology("changelog")`.
- `conftest.py` makes the suite runnable without the serving database: 317 pass, 219 skip
  with a reason naming the database, 0 fail.
- CI on every push and PR; releases publish from a tag.

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

## [0.1.0] — unreleased

First release: MCP server, npm client and stdio bridge, Python client.
