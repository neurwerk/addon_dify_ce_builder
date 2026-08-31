# Neurwerk Dify CE Builder

This repository contains the owned build tooling and overlay used to produce
Neurwerk's Dify Community Edition API and Web images. It keeps the build
interactive and workstation-operated because the images are too large for the
hosted CI path. CI validates source provenance, checksums, shell code, Python
tests, and formatting; it never builds or publishes images.

The canonical repository is
[`neurwerk/addon_dify_ce_builder`](https://github.com/neurwerk/addon_dify_ce_builder).

## What It Changes

- Adds Keycloak OIDC authentication and sign-in UI integration.
- Applies retry-safe and PostgreSQL 18-compatible Dify migration changes.
- Adds single-workspace model-provider bootstrap behavior.
- Bundles a checksum-pinned, unmodified Dify OpenAI-compatible plugin package.

`overlay/api/` and `overlay/web/` mirror selected paths from upstream Dify. See
`NOTICE-CHANGES.md` for the change inventory and `THIRD_PARTY_NOTICES.md` for
license and provenance details.

## Version Contract

`DIFY_VERSION` is the authoritative upstream release. API and Web provenance
are pinned separately:

- `DIFY_API_IMAGE_DIGEST`: the immutable multi-platform digest of the upstream
  `langgenius/dify-api` base image.
- `DIFY_SOURCE_REVISION`: the Git commit resolved by the upstream tag.
- `DIFY_SOURCE_SHA256`: the SHA-256 of the GitHub source archive used by the Web
  build.
- `NODE_IMAGE_DIGEST`: the authoritative OCI index digest for the Web build and
  runtime base `docker.io/library/node:22.22.1-alpine`.
- `ALPINE_IMAGE_DIGEST`: the authoritative OCI index digest for the Web source
  stage base `docker.io/library/alpine:3.21`.

When changing Dify, update its four provenance files together. When changing a
Web base tag, update its digest file in the same change. The API Dockerfile uses
the digest-pinned base, while the Web Dockerfile uses digest-pinned Node and
Alpine inputs and rejects a source archive that does not match the pinned
checksum. OCI labels distinguish the Web runtime base from its source-stage
base and Dify source provenance.

The API addon installs `requirements/addon.txt` with `--require-hashes`,
`--only-binary`, and `--no-deps`. Its only addon is PyJWT; cryptography is
provided by the immutable upstream API base. Regenerate the lock from
`requirements/addon.in` using the command recorded in the generated file.

Verify the upstream tag, source archive, OCI index digests, and bundled plugin
without building an image:

```bash
./scripts/verify-sources.sh
```

## Validate

Install [uv](https://docs.astral.sh/uv/) and ShellCheck, then run:

```bash
uv sync --locked --dev
uv run ruff check overlay/scripts scripts tests
uv run ruff format --check overlay/scripts scripts tests
uv run pytest
shellcheck deploy.sh scripts/verify-sources.sh
./scripts/verify-sources.sh
```

## Build And Publish

Image construction and publication are intentionally user-operated. You need a
clean Git checkout, Docker with Buildx, Python 3, and an authenticated GHCR
session. Choose an explicit immutable overlay version beginning with the
upstream version, for example:

```bash
./deploy.sh 1.15.0-kc-v15
```

The prompts default to both API and Web images, `linux/amd64`, and registry
push. A single push choice applies to every selected platform. A local load is
limited to one platform because Docker cannot load a multi-platform image into
the local image store.

The script never creates or publishes `latest`. Before a push it:

1. fetches canonical `origin/main` and requires the clean `HEAD` commit to be
   exactly equal to it;
2. logs Docker in to `ghcr.io` with the exact prompted or environment-provided
   credentials that the preflight uses;
3. obtains authenticated GHCR `pull,push` tokens for every selected package;
4. opens and cancels a temporary blob-upload session for every package to prove
   push permission;
5. requires every final tag lookup to return an explicit absent result.

Set `GHCR_USERNAME` and `GHCR_TOKEN`, or enter them at the private prompts. The
token needs `write:packages`. The script passes it to `docker login` through
stdin and does not print it, so Buildx and the preflight use the same account.
Docker login updates the `ghcr.io` entry in the configured Docker credential
store (or Docker config), can replace a previously stored GHCR login, and
persists after the script exits. Run `docker logout ghcr.io` afterward if the
credential should not remain stored. Any network, authentication, authorization,
cleanup, or registry-response ambiguity aborts before the first build. Existing
final tags are never reused.

On success the script reports each exact tag and resulting content digest;
retain those digests with the release record.

## Publication Atomicity

GHCR does not provide a transaction spanning the API and Web packages. The
script preflights all selected destinations before any build or push, which
prevents predictable partial releases, but the package pushes remain sequential.
A failure after the first package is published can leave a partial release. In
that case, preserve the published immutable tag and rerun only the missing
package from the same clean source commit and version.

Tag absence checks also cannot reserve a tag. Exclusive operator coordination
is still required to prevent another publisher racing between preflight and
push; GHCR does not expose a conditional create-only tag operation.

Automation and agents must not run `deploy.sh`, build these images, or publish
them.

## License

Neurwerk-owned additions and build tooling are MIT licensed. Dify-derived files
remain under Dify's Open Source License, which is based on Apache License 2.0
with additional conditions. The bundled official plugin is Apache-2.0. See
`THIRD_PARTY_NOTICES.md` before using or redistributing this repository or its
images.
