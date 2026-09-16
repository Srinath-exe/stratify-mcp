# Stratify for Claude Desktop (.mcpb)

`build.sh` produces `dist/stratify-<version>.mcpb`: the npm stdio bridge, an icon, and
a manifest with one user field -- the API key. A user double-clicks the file (or installs
it from Claude Desktop's extension directory), pastes their key once, and the tools
appear.

Submit it through the desktop-extension form at https://clau.de/desktop-extention-submission.
That form does not require a Team or Enterprise organisation; the Connectors Directory
(remote servers, claude.ai) does.

Rebuild after every npm release so the bundle and the package carry the same version.
