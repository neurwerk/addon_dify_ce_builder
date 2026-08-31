## Summary

Describe the owned overlay or builder change and its upstream relationship.

## Issues

Closes #<!-- implementation issue -->

Parent: #<!-- link only; do not use Closes/Fixes/Resolves -->

## Affected Contracts

List affected upstream, provenance, image, licensing, platform, and operator contracts, or state `None`.

## Validation

- [ ] `uv run ruff check overlay/scripts scripts tests`
- [ ] `uv run ruff format --check overlay/scripts scripts tests`
- [ ] `uv run pytest`
- [ ] `shellcheck deploy.sh scripts/verify-sources.sh`
- [ ] `./scripts/verify-sources.sh`
- [ ] Not applicable checks are explained below.

Record exact command results:

## Version And Provenance

- [ ] `DIFY_VERSION`, source provenance, Dockerfiles, and version-bound overlays remain consistent.
- [ ] Upstream-derived changes and licensing are documented when applicable.
- [ ] No credentials, registry tokens, personal data, or generated local files are included.

## Operator-Only Publication

- [ ] This pull request does not add automatic Dify image builds, publication, or GitHub releases that imply images exist.
- [ ] Neither CI nor automation executes `deploy.sh`.

`deploy.sh`, Dify image construction, and image publication are operator-only actions. Review and merge do not authorize them.

## Release Classification

Apply exactly one: `release: none`, `release: notes`, `release: platform`, or `release: client`.
