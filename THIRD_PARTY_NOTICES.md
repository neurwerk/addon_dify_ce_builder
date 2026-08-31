# Third-Party Notices

The root `LICENSE` applies only to Neurwerk-owned overlay additions, build
tooling, tests, and documentation. It does not replace the terms of the
third-party works described here.

## Dify Community Edition

- Project: Dify Community Edition
- Source: <https://github.com/langgenius/dify>
- Version: `1.15.0`
- Source revision: `3aa26fb6374bbd47e5469f7d7cc25f3e0075a60c`
- Web source archive SHA-256: `18c9a711ac715855bd3d0882966b14143692a48269181c1dd7f7bfcc702a66ba`
- API base image:
  `docker.io/langgenius/dify-api:1.15.0@sha256:c1712c50f27c9dfd31c5be77a9a03f30c464fc6983287eefd4a6a98376c70c24`
- API base multi-platform digest: `sha256:c1712c50f27c9dfd31c5be77a9a03f30c464fc6983287eefd4a6a98376c70c24`
- API base `linux/amd64` manifest: `sha256:9593d4197350678ecc6fb7c0f0416d9e64e5c349f0703d000d1498793eb34a5e`
- API base `linux/arm64` manifest: `sha256:80f5f8fe2e11e2b123fc2dc0dab7421e17b17f87163e82dbbb126a269122976d`
- Copyright: Copyright 2025 LangGenius, Inc.
- License: Dify Open Source License, based on Apache License 2.0 with additional
  conditions; see `LICENSES/Dify-LICENSE`

The files under `overlay/api/` and `overlay/web/` that modify or derive from
Dify source remain subject to the Dify Open Source License. The Web image is
built from the source revision and archive identified above. The API image
extends the independently digest-pinned upstream image identified above. Dify
did not contain a root `NOTICE` file at this revision.

## Web Build Base Images

- Node build and runtime base:
  `docker.io/library/node:22.22.1-alpine@sha256:8094c002d08262dba12645a3b4a15cd6cd627d30bc782f53229a2ec13ee22a00`
- Node base OCI index digest:
  `sha256:8094c002d08262dba12645a3b4a15cd6cd627d30bc782f53229a2ec13ee22a00`
- Alpine source-stage base:
  `docker.io/library/alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d`
- Alpine base OCI index digest:
  `sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d`

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
