# Releasing

How to publish a new version of `pru-math-engine`. Two paths:

1. **Automated (recommended)**: configure GitHub Actions Trusted
   Publishing once; every subsequent release is `git tag` + `git push`.
2. **Manual**: build and upload from your laptop with `twine`.

Both leave the source tree in the same state — pick whichever fits your
workflow.

The repo ships a CI workflow (`.github/workflows/ci.yml`) that runs
the full test suite on every push, and a publish workflow
(`.github/workflows/publish.yml`) that fires on `v*` tags. The publish
workflow re-runs the test suite as a gate, builds sdist + wheel,
runs `twine check`, and uploads — only if everything is green.

---

## Path A — Automated PyPI publishing (one-time setup)

This is the modern way. PyPI calls it
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/);
it uses GitHub OIDC tokens so you never store a PyPI API token on
GitHub. About 5 minutes.

### One-time setup

1. **Create your PyPI account** at <https://pypi.org/account/register/>.

2. **Reserve the project name** by going to
   <https://pypi.org/manage/account/publishing/> and clicking
   *"Add a new pending publisher"*.

   Fill in:
   | Field | Value |
   | --- | --- |
   | PyPI Project Name | `pru-math-engine` |
   | Owner | `CandanUmut` |
   | Repository name | `math_engine` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   This authorises the GitHub Actions workflow to publish under that
   name without an API token.

3. **Create the matching GitHub environment.** In your repo:
   *Settings → Environments → New environment* → name it `pypi`.

   No secrets to add. Optionally add a *"required reviewer"* protection
   rule so a real human approves every release (recommended).

4. **Optional: TestPyPI.** Repeat the two steps above with
   <https://test.pypi.org/> and a separate `testpypi` GitHub
   environment. The publish workflow has a `workflow_dispatch` input
   that lets you target TestPyPI by hand:
   *Actions → publish → Run workflow → target: testpypi*.

### Per-release (every time)

```bash
# 1. Bump the version in pyproject.toml and CHANGELOG.md
$EDITOR pyproject.toml CHANGELOG.md
# (set project.version = "0.12.1" or "0.13.0" or whatever)

# 2. Commit, then tag, then push
git add pyproject.toml CHANGELOG.md
git commit -m "release: v0.12.1"
git tag v0.12.1
git push origin main v0.12.1

# 3. GitHub Actions takes over:
#    - the publish workflow re-runs the test suite as a gate
#    - it verifies the git-tag version matches pyproject's version
#    - builds wheel + sdist
#    - twine checks the artefacts
#    - publishes to PyPI via OIDC (no token)
#
# Watch progress at:
#   https://github.com/CandanUmut/math_engine/actions
#
# Within a minute, your release is live at:
#   https://pypi.org/project/pru-math-engine/0.12.1/
```

### Verify the publish

```bash
# Wait ~30s for PyPI's CDN to pick up the new version
python -m venv /tmp/v && /tmp/v/bin/pip install pru-math-engine==0.12.1
/tmp/v/bin/pru-math 'Eq(x**2 - 4, 0)'
```

You should see `verify : verified` and `[-2, 2]`. Job done.

---

## Path B — Manual publishing from your laptop

For when you want full control or your CI is offline.

### One-time setup

```bash
pip install --upgrade build twine

# Save your token at ~/.pypirc:
cat <<EOF >> ~/.pypirc
[pypi]
  username = __token__
  password = pypi-AgEIcHlwaS5vcmcCJ...your token here
EOF
chmod 600 ~/.pypirc
```

The token is created at <https://pypi.org/manage/account/token/>.

### Per release

```bash
# Bump the version in pyproject.toml and CHANGELOG.md, commit, push.

# Build clean
rm -rf dist build *.egg-info
python -m build

# Inspect the artefacts
ls dist/
twine check dist/*

# Upload
twine upload dist/*

# Optionally tag the release
git tag v0.12.1
git push origin v0.12.1
```

If you want a TestPyPI dry-run first:

```bash
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            pru-math-engine
```

---

## Cutting a GitHub release page

Independent of PyPI — this gives users a downloadable `.whl` and
`.tar.gz` from GitHub plus a place for release notes.

```bash
# After tagging:
gh release create v0.12.1 \
    dist/pru_math_engine-0.12.1-py3-none-any.whl \
    dist/pru_math_engine-0.12.1.tar.gz \
    --title "v0.12.1 — Phase 12 patch" \
    --notes-file CHANGELOG.md
```

Or do it through the GitHub UI: *Releases → Draft a new release*,
pick the tag, paste the relevant section of `CHANGELOG.md`, attach
the wheel and sdist.

---

## Publishing the Docker image

The Dockerfile is in the repo root. To push to GitHub Container
Registry (free for public images):

```bash
# One-time: log in
echo $GITHUB_TOKEN | docker login ghcr.io -u CandanUmut --password-stdin
# (the token needs `write:packages` scope)

# Per release
docker build -t ghcr.io/candanumut/pru-math-engine:0.12.1 .
docker tag    ghcr.io/candanumut/pru-math-engine:0.12.1 \
              ghcr.io/candanumut/pru-math-engine:latest
docker push   ghcr.io/candanumut/pru-math-engine:0.12.1
docker push   ghcr.io/candanumut/pru-math-engine:latest
```

After publishing, anyone can:

```bash
docker run -p 8000:8000 -v ./data:/data \
    ghcr.io/candanumut/pru-math-engine:latest
```

---

## Versioning policy

The project follows [SemVer](https://semver.org/):

- **MAJOR.0.0** — incompatible API changes (e.g. dropping the FastAPI
  layer, breaking the SQLite schema irrevocably).
- **0.MAJOR.0** — a new "phase" lands. Backwards-compatible feature
  additions; existing API endpoints keep working.
- **0.x.PATCH** — bug fixes, doc improvements, internal refactors
  with no API impact.

`0.x.y` versions are pre-1.0; expect occasional schema migrations
between minors that *do* require a one-shot data fix-up. The store's
`_MIGRATIONS` list (`pru_math/store.py`) handles those automatically
on next start; the user's database survives.

The first 1.0 release will lock the API surface and the SQLite schema.

---

## Pre-release sanity checklist

Before tagging:

- [ ] All 216 tests passing locally (`OLLAMA_ENABLED=false pytest -q`).
- [ ] `pyproject.toml::project.version` matches the new tag.
- [ ] `CHANGELOG.md` has an entry for the new version.
- [ ] `python -m build` succeeds with no warnings.
- [ ] `twine check dist/*` passes both wheel and sdist.
- [ ] In a fresh venv, `pip install dist/*.whl` lands both
      `pru-math` and `pru-math-server` on `PATH`, and
      `pru-math 'Eq(x**2 - 4, 0)'` returns `[-2, 2] verified`.
- [ ] (Major releases) the docker image builds and `docker run`
      serves the UI on port 8000.

If any of those fail, fix them before tagging — pulling a published
PyPI version is messy.
