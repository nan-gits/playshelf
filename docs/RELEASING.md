# Releasing Playshelf

Releases are built by the `Release` workflow
([.github/workflows/release.yml](../.github/workflows/release.yml)). Pushing a
version tag builds the Docker image for `linux/amd64` and `linux/arm64`, pushes
it to `ghcr.io/<owner>/playshelf` and creates a GitHub release with the notes
from `CHANGELOG.md`.

## Steps

1. Make sure CI on `main` is green.
2. Set the new version in both places:
   - `version` in `pyproject.toml`
   - `__version__` in `app/__init__.py`
3. In `CHANGELOG.md`, rename the `Unreleased` entries to a new section
   `## [X.Y.Z] - YYYY-MM-DD`, add an empty `## [Unreleased]` above it and
   update the links at the end of the file.
4. Commit (`Release X.Y.Z`), merge to `main`, then tag and push:

   ```bash
   git tag -a vX.Y.Z -m "Playshelf X.Y.Z"
   git push origin vX.Y.Z
   ```

5. Watch the workflow. It stops before publishing anything if the tag does not
   match both version numbers or the changelog section is missing or still
   marked `unreleased`.

Versions follow [Semantic Versioning](https://semver.org/). A tag with a suffix
such as `v0.2.0-rc.1` becomes a pre-release and does not move the `latest` tag.

## Image tags

| Tag | Points to |
|-----|-----------|
| `X.Y.Z` | exactly this release |
| `X.Y` | the newest patch release of `X.Y` |
| `X` | the newest release of major version `X` (only from 1.0 on) |
| `latest` | the newest release that is not a pre-release |

## First release only

A new package on the GitHub Container Registry is private. After the first
release, open the package settings on GitHub (profile, **Packages**,
`playshelf`, **Package settings**), set the visibility to **Public** and check
that the package is linked to the repository.

## Test build without publishing

Running the `Release` workflow by hand (**Actions**, **Release**, **Run
workflow**) builds the image for both architectures but pushes nothing. Use it
after changes to the `Dockerfile` or the dependencies.
