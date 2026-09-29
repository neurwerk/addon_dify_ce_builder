# Third-Party Notices

The root `LICENSE` applies only to independently authored build tooling,
tests, and documentation. It does not replace the terms of the
third-party works described here.

## Dify Community Edition

- Project: Dify Community Edition
- Source: <https://github.com/langgenius/dify>
- Version: `1.17.1`
- Source revision: `8387590ace4a094de812b7847fc6a4c3a27cd52b`
- Web source archive SHA-256: `ac5df165d788770d09268091fd14690000f96e192b93290b7bb4c51709244ec0`
- API base image:
  `docker.io/langgenius/dify-api:1.17.1@sha256:ceede5b903afaa20348f7ad80ebf847379dc56d886a99b9ec6a08913dacd7732`
- API base multi-platform digest: `sha256:ceede5b903afaa20348f7ad80ebf847379dc56d886a99b9ec6a08913dacd7732`
- API base `linux/amd64` manifest: `sha256:04f435a2e366c73f1ccefe9e7fbdaa2ac6440bdcde268d5d20f19c3ba7e7dcdb`
- API base `linux/arm64` manifest: `sha256:53fb08964da7a19d9b9f230a3b2945b6de55dc7b2ec570418e467defa809d82f`
- Copyright: Copyright 2025 LangGenius, Inc.
- License: Dify Open Source License, based on Apache License 2.0 with additional
  conditions; see `LICENSES/Dify-LICENSE`

The Dify-derived `overlay/api/neurwerk_sso.py`, `neurwerk_settings.py`, the
retry-safe concurrent-index migration, and the modified upstream API and Web
files remain under the Dify Open Source License. The Web image is built from
the source revision and archive identified above. The API image
extends the independently digest-pinned upstream image identified above. Dify
did not contain a root `NOTICE` file at this revision.

## Web Build Base Images

- Node build and runtime base:
  `docker.io/library/node:24.20.0-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf`
- Node base OCI index digest:
  `sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf`
- Alpine source-stage base:
  `docker.io/library/alpine:3.21@sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507`
- Alpine base OCI index digest:
  `sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507`

The Node base supplies the Web build and final runtime. The separate Alpine
base is used only to fetch and verify the pinned Dify source archive. Both tags
are resolved by Docker Hub and consumed by their exact multi-platform OCI index
digests.

## OpenAI-API-Compatible Plugin

- Project: Dify official plugins, `models/openai_api_compatible`
- Source: <https://github.com/langgenius/dify-official-plugins>
- Package version: `0.0.56`
- Version source revision: `e1d1565d61ce534cbdba998df226a9a080606bfc`
- Bundled file: `overlay/plugins/langgenius-openai_api_compatible_0.0.56.difypkg`
- Package SHA-256: `859e3d9496446e4dff192ca064012d5e34a76eb19eddba2d3fd9c40462991a22`
- Copyright: Copyright 2025 LangGenius, Inc.
- License: Apache License 2.0; see `LICENSES/Apache-2.0.txt`

The plugin package is redistributed unmodified. It was obtained from the Dify
Marketplace and retains its embedded metadata. The package does not embed a
license file, so this repository supplies the upstream repository's license
alongside it.

The names and trademarks of third parties are used only to identify their
works. No trademark license is granted.
