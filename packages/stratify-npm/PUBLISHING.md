# Publishing the clients

Both clients ship from one tag on this repository:
`.github/workflows/release.yml` runs the tests, checks the tag against both
package versions, then publishes `stratify-mcp` to npm and to PyPI using
**trusted publishing** -- the workflow proves its identity with a
short-lived OIDC token, so no registry token is stored in GitHub or on any
machine.

## One-time setup

### npm (needs one manual publish first)

npm only lets you register a trusted publisher on a package that already
exists, so the very first version goes out by hand:

1. `npm login` (2FA on the account, always).
2. From `packages/stratify-npm`: `npm publish --access public`
   (`prepublishOnly` builds and runs the tests first).
3. On npmjs.com open the package -> **Settings** -> **Trusted Publisher**
   -> GitHub Actions:
   - Organization or user: `Srinath-exe`
   - Repository: `stratify-mcp`
   - Workflow filename: `release.yml`
   - Environment name: `npm`
   - Allowed actions: allow direct `npm publish` (the workflow does not
     use `npm stage publish`)
4. Same page, **Publishing access**: *Require two-factor authentication
   and disallow tokens*. Trusted publishing is not a token, so the
   workflow keeps working; a leaked classic token would not.
5. `npm logout` on the box you published from.

### PyPI (no manual publish needed)

PyPI accepts a *pending* publisher for a project that does not exist yet:

1. pypi.org -> account -> **Publishing** -> *Add a new pending publisher*:
   - PyPI project name: `stratify-mcp`
   - Owner: `Srinath-exe`
   - Repository name: `stratify-mcp`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
2. The first tag creates the project and the pending publisher becomes
   its trusted publisher.

## Every release

```bash
# versions must agree with each other and with the tag
#   packages/stratify-npm/package.json      "version"
#   packages/stratify-py/pyproject.toml     version = "..."
git tag v0.1.1
git push origin v0.1.1
```

Watch it under **Actions**. Both publish jobs depend on the test job, so a
red build never ships. A version already on the registry is refused before
the build starts.

`workflow_dispatch` with *dry_run* (the default) runs everything except
the two publish steps.

## Notes

- The two GitHub environments (`npm`, `pypi`) are created automatically the
  first time a job references them. They carry no protection rules; if
  the repository ever gains other maintainers, add yourself as required
  reviewer on both so a publish needs a human click.
- Provenance is not attested while this repository is private. It turns on
  by itself when the repository is made public; nothing else changes.
