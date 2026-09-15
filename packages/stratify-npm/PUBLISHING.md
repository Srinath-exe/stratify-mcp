# Publishing the clients

Both clients publish with **trusted publishing** -- the workflow proves its
identity to the registry with a short-lived OIDC token, so no registry
token is stored in GitHub or on any machine.

They currently ship from two repositories (see *Why two repositories*):

| Package | Registry | Repository | Workflow | Tag |
|---|---|---|---|---|
| `stratify-mcp` (Python) | PyPI | `Srinath-exe/stratify-mcp` (this repo) | `release.yml`, environment `pypi` | `v<version>` |
| `stratify-mcp` (npm) | npm | `Srinath-exe/Stratify` (private monorepo) | `publish-npm.yml`, no environment | `npm-v<version>` |

The package sources are byte-identical in both repositories; keep them so.

## Every release

```bash
# 1. Bump BOTH versions to the same number and add a CHANGELOG entry:
#      packages/stratify-npm/package.json      "version"
#      packages/stratify-npm/package-lock.json "version"   (top two occurrences)
#      packages/stratify-py/pyproject.toml     version = "..."
#      packages/stratify-py/stratify_mcp/__init__.py
#    Copy the four package files into the monorepo's stratify_mcp/packages/ too.

# 2. PyPI -- from this repo:
git tag v0.1.2 && git push origin v0.1.2

# 3. npm -- from the monorepo:
cd /root/Stratify && git tag npm-v0.1.2 && git push origin npm-v0.1.2
```

Each workflow runs the tests first, checks the tag equals the package
version, refuses a version already on the registry, publishes, and (npm)
waits until the registry serves it. Watch under each repo's **Actions**.

## Why two repositories

GitHub repositories created after 2026-07-15 get OIDC tokens with an
*immutable* subject claim (`repo:owner@id/repo@id`). npm's token exchange
does not accept that format yet and answers "package not found"
([npm/cli#9969](https://github.com/npm/cli/issues/9969)); GitHub's API to
switch a repository back to the legacy subject returns success and changes
nothing. This repository was created 2026-08-30. The monorepo dates from
2025 and still gets the legacy subject, so npm's exchange works there.
PyPI accepts both formats.

**When npm fixes #9969:** in `.github/workflows/release.yml` remove the
`false &&` from the npm job's `if:`; on npmjs.com edit the trusted
publisher to repository `stratify-mcp`, workflow `release.yml`,
environment `npm`; delete `.github/workflows/publish-npm.yml` from the
monorepo. One tag then ships both.

## One-time registry setup (done 2026-09-15)

**npm.** The first version (0.1.0) was published by hand because npm only
registers a trusted publisher on a package that already exists. Then on
npmjs.com -> package -> Settings -> Trusted Publisher -> GitHub Actions:
owner `Srinath-exe`, repository `Stratify`, workflow `publish-npm.yml`,
environment blank, direct `npm publish` allowed. Publishing access is
*Require 2FA and disallow tokens*: trusted publishing is not a token, so
the workflow keeps working; a leaked classic token would not.

**PyPI.** A *pending publisher* was registered before the project existed
(owner `Srinath-exe`, repository `stratify-mcp`, workflow `release.yml`,
environment `pypi`); the first tagged release created the project. PyPI
attaches attestations automatically.

## Notes

- The `pypi` GitHub environment was created automatically the first time
  the job referenced it. It carries no protection rules; if the repository
  ever gains other maintainers, add yourself as required reviewer so a
  publish needs a human click.
- npm provenance is not attested while the publishing repository is
  private; it turns on by itself once npm publishes from a public repo.
