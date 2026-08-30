# Security

## Reporting a vulnerability

Email **sahej@bluerickshawusa.com** with steps to reproduce. Please do not open a public
issue for anything that would let a caller read data, escalate a tier, or reach another
account's records.

There is also an in-product path: the `submit_feedback` MCP tool attaches the failing
call's context automatically, which is usually faster than describing it.

## What this repository does and does not contain

Publishing the server is deliberate — the methodology is the product, and a backtest you
cannot audit is a claim, not a measurement. But some things are absent on purpose, and
their absence is not an oversight:

- **No market data.** No prices, no chains, no fixtures containing either.
- **No ingestion pipeline.** How the data is sourced, normalised and gap-repaired is not
  here, and the loaders under `data/` that read the private upstream tables are not here.
- **No credentials.** Every secret is read from the environment. The only `sk_live_` strings
  anywhere are test fixtures like `sk_live_not_a_real_key`.
- **No production topology.** `docker-compose.example.yml` is a self-hosting template, not
  a description of the deployed system.

## The design this depends on

Reading the code should make these verifiable rather than requiring trust:

**API keys** are `scrypt`-hashed with a pepper from `STRATIFY_KEY_PEPPER`, compared with
`hmac.compare_digest`. Only the hash is stored, and `server/run.sh` refuses to start if the
pepper is unset — the in-code default is a development value published in this repository
and is unsafe by construction.

**Tier isolation is enforced in ClickHouse, not in Python.** Each plan binds a distinct
database user (`stratify_free`, `stratify_paid`) whose visible date range is a row policy
(`data/schema/70_tiers.sql`). A bug in the application layer cannot widen it. The
signal cache is keyed on the ClickHouse user for the same reason — see the comment in
`engine/signals.py`, which exists because keying on entry minute alone once let a free-tier
gate evaluate against seven years of paid history.

**No `eval`, no string-built SQL from user input.** The strategy protocol is a closed
vocabulary; unrecognised fields are refused rather than ignored. Queries are parameterised.

**No route returns market data.** A test asserts no such route exists and another asserts a
result carries no per-contract prices. Per-trade prices are released only through a
three-control boundary: a minimum trade count, a per-response cap, and an hourly meter.

**The admin console cannot bootstrap itself.** The first admin is granted by
`python -m server.grant_admin`, which requires shell access to the host, because any
in-app path to creating the first admin is a privilege-escalation route by definition.
`is_admin` is re-read from the database on every admin request, so revoking it takes effect
on the next click rather than at the next sign-in.

**`X-Forwarded-For` is trusted only from the proxy.** `STRATIFY_BEHIND_PROXY` gates it, and
`server/app.py` takes the last untrusted hop, not the first — sending
`X-Forwarded-For: 1.2.3.4` with an incrementing address previously granted a fresh identity
per request. There are tests for exactly that spoof.

**Browser origins are allow-listed.** A request carrying an `Origin` this service does not
recognise is rejected, which is the check the MCP spec requires: a locally-bound server is
otherwise reachable from any web page the user happens to be visiting. With
`STRATIFY_ALLOWED_ORIGINS` unset the list is not empty — it still contains the three
`claude.ai` hosts in `DEFAULT_BROWSER_ORIGINS`. If you self-host and do not want those,
edit that constant; setting the environment variable only adds to it.

## If you self-host

The three settings most likely to be got wrong:

1. **Do not publish the service port.** Docker publishes to `0.0.0.0` by default and
   installs its iptables rules ahead of `ufw`. Bind to `127.0.0.1` and terminate TLS in
   front of it. ClickHouse should have no host port mapping at all.
2. **If you enable URL-embedded keys, make sure your proxy does not log them.**
   `POST /mcp/k/{key}` exists because some MCP clients cannot send a custom header, and it
   puts an API key somewhere keys should not be: the request line, which almost every
   reverse proxy logs by default. The hosted service suppresses logging for that path. A
   fresh deployment does not. Either configure the same suppression or leave the route off
   — it is gated by `ALLOW_URL_KEY` and returns 404 when disabled.
3. **Do not give the service the ClickHouse admin user.** The `default` user holds
   `access_management` plus `FILE`, `URL`, `S3` and `REMOTE`. The service never needs it —
   every request binds a per-tier user. `docker-compose.example.yml` blanks
   `CLICKHOUSE_PASSWORD` for this reason.
