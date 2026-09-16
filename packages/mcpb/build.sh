#!/usr/bin/env bash
# Builds stratify.mcpb -- the Claude Desktop extension. It is the npm stdio bridge
# (zero runtime dependencies, so no node_modules) plus a manifest whose one user field
# is the API key, injected as STRATIFY_API_KEY. Output: packages/mcpb/dist/stratify.mcpb
#
# Submit at https://clau.de/desktop-extention-submission -- the desktop-extension form,
# which unlike the Connectors Directory does not need a Team/Enterprise organisation.
set -euo pipefail
cd "$(dirname "$0")"
NPM=../stratify-npm
VERSION="$(node -p "require('$NPM/package.json').version")"
rm -rf build dist && mkdir -p build/server dist
( cd "$NPM" && npm ci --silent && npm run build --silent )
cp "$NPM"/dist/*.js build/server/
printf '{ "type": "module" }\n' > build/server/package.json   # dist is ESM
cp ../../server/assets/icon-512.png build/icon.png
python3 - "$VERSION" > build/manifest.json <<'PY'
import json, sys, urllib.request
version = sys.argv[1]
tools = json.loads(urllib.request.urlopen(urllib.request.Request(
    "https://stratify-mcp.aeon-labs.site/mcp", method="POST",
    data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
    headers={"Content-Type": "application/json", "Accept": "application/json"}), timeout=20)
    .read())["result"]["tools"]
print(json.dumps({
  "manifest_version": "0.3",
  "name": "stratify",
  "display_name": "Stratify",
  "version": version,
  "description": "Backtest NIFTY option strategies on real 1-minute data, with an honest out-of-sample panel.",
  "long_description": (
    "Describe an Indian index option strategy in plain words -- the legs, when to enter, "
    "how to manage it -- and get it backtested on a year of real 1-minute NIFTY options "
    "data. Results carry P&L after real charges and slippage, return on margin, and an "
    "honesty panel: out-of-sample split, walk-forward folds, bootstrap interval and a "
    "deflated Sharpe that accounts for how many variants you already tried. Every "
    "backtest gets a shareable report page. Free tier: one year of NIFTY, 100 backtests "
    "an hour. Needs a key from https://stratify.aeon-labs.site (Google sign-in)."),
  "author": {"name": "Srinath H", "url": "https://github.com/Srinath-exe"},  # the directory wants the GitHub profile here
  "repository": {"type": "git", "url": "https://github.com/Srinath-exe/stratify-mcp"},
  "homepage": "https://stratify.aeon-labs.site",
  "documentation": "https://stratify.aeon-labs.site/docs",
  "support": "https://stratify.aeon-labs.site/contact",
  "icon": "icon.png",
  "server": {
    "type": "node",
    "entry_point": "server/bin.js",
    "mcp_config": {
      "command": "node",
      "args": ["${__dirname}/server/bin.js"],
      "env": {"STRATIFY_API_KEY": "${user_config.api_key}"}
    }
  },
  "user_config": {
    "api_key": {
      "type": "string",
      "title": "Stratify API key",
      "description": "sk_live_... from https://stratify.aeon-labs.site -> API keys. Shown once when made.",
      "sensitive": True,
      "required": True
    }
  },
  "privacy_policies": ["https://stratify.aeon-labs.site/privacy"],
  "tools": [{"name": t["name"], "description": t["annotations"].get("title") or t["description"][:80]} for t in tools],
  "keywords": ["options", "backtesting", "nifty", "trading", "india", "finance"],
  "license": "MIT",
  "compatibility": {
    "claude_desktop": ">=1.0.0",
    "platforms": ["darwin", "win32", "linux"],
    "runtimes": {"node": ">=18.0.0"}
  }
}, indent=2))
PY
( cd build && npx -y @anthropic-ai/mcpb@latest pack . "../dist/stratify-$VERSION.mcpb" )
ls -la dist
