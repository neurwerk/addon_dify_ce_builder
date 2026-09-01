# check=skip=InvalidDefaultArgInFrom
# Base image arguments intentionally require digest-pinned values from deploy.sh.
# =============================================================================
# SPDX-License-Identifier: MIT
# addon-dify-ce-builder-web - Dify CE Web + Keycloak SSO login button
#
# Builds Dify Web from pinned source with overlay/web/ patches applied before
# the Next.js build. (The previous version of this Dockerfile copied .tsx
# sources into the prebuilt upstream image, where they were never compiled;
# the served bundle stayed stock upstream.)
# Mirrors the upstream Web Dockerfile selected by DIFY_VERSION.
# =============================================================================

ARG NODE_IMAGE
ARG ALPINE_IMAGE

# Stage 1: fetch and verify pristine upstream source.
FROM ${ALPINE_IMAGE} AS source
RUN apk add --no-cache curl tar
WORKDIR /tmp
COPY DIFY_VERSION DIFY_SOURCE_REVISION DIFY_SOURCE_SHA256 /tmp/provenance/
RUN set -eu; \
  dify_version="$(tr -d '\r\n' < /tmp/provenance/DIFY_VERSION)"; \
  source_sha256="$(tr -d '\r\n' < /tmp/provenance/DIFY_SOURCE_SHA256)"; \
  test -n "$(tr -d '\r\n' < /tmp/provenance/DIFY_SOURCE_REVISION)"; \
  curl --fail --location --silent --show-error \
    "https://github.com/langgenius/dify/archive/refs/tags/${dify_version}.tar.gz" \
    --output dify.tar.gz; \
  printf '%s  %s\n' "${source_sha256}" dify.tar.gz | sha256sum -c; \
  tar -xzf dify.tar.gz; \
  mv "dify-${dify_version}" /src

# Stage 2: base with pnpm (same as upstream).
FROM ${NODE_IMAGE} AS base
RUN apk add --no-cache tzdata
RUN corepack enable
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ARG NEXT_PUBLIC_BASE_PATH=""
ENV NEXT_PUBLIC_BASE_PATH="$NEXT_PUBLIC_BASE_PATH"

# Stage 3: install workspace dependencies (layer-cached).
FROM base AS packages
WORKDIR /app
COPY --from=source /src/package.json /src/pnpm-lock.yaml /src/pnpm-workspace.yaml /app/
COPY --from=source /src/web/package.json /app/web/
COPY --from=source /src/e2e/package.json /app/e2e/
COPY --from=source /src/sdks/nodejs-client/package.json /app/sdks/nodejs-client/
COPY --from=source /src/packages /app/packages
RUN corepack install
RUN VITE_GIT_HOOKS=0 pnpm install --frozen-lockfile

# Stage 4: build with the Neurwerk overlay applied.
FROM base AS builder
WORKDIR /app
COPY --from=packages /app/ .
COPY --from=source /src/ .
# The Keycloak SSO sign-in UI must be in place before `next build`.
COPY overlay/web/ /app/web/
WORKDIR /app/web
ENV NODE_OPTIONS="--max-old-space-size=4096"
ENV pnpm_config_verify_deps_before_run=false
RUN pnpm build && pnpm build:vinext

# Stage 5: production runtime (same /app/targets layout as upstream).
FROM base AS production

ARG BUILDER_REVISION
ARG DIFY_SOURCE_REVISION
ARG DIFY_SOURCE_SHA256
ARG DIFY_VERSION
ARG IMAGE_VERSION
ARG NODE_IMAGE_NAME
ARG NODE_IMAGE_DIGEST
ARG ALPINE_IMAGE_NAME
ARG ALPINE_IMAGE_DIGEST

LABEL org.opencontainers.image.title="Neurwerk Dify CE Web overlay" \
  org.opencontainers.image.description="Dify CE Web with the Neurwerk overlay" \
  org.opencontainers.image.source="https://github.com/neurwerk/addon_dify_ce_builder" \
  org.opencontainers.image.url="https://github.com/neurwerk/addon_dify_ce_builder" \
  org.opencontainers.image.version="${IMAGE_VERSION}" \
  org.opencontainers.image.revision="${BUILDER_REVISION}" \
  org.opencontainers.image.base.name="${NODE_IMAGE_NAME}" \
  org.opencontainers.image.base.digest="${NODE_IMAGE_DIGEST}" \
  com.neurwerk.dify.web-source-base.name="${ALPINE_IMAGE_NAME}" \
  com.neurwerk.dify.web-source-base.digest="${ALPINE_IMAGE_DIGEST}" \
  com.neurwerk.dify.version="${DIFY_VERSION}" \
  com.neurwerk.dify.web-source.revision="${DIFY_SOURCE_REVISION}" \
  com.neurwerk.dify.web-source.sha256="${DIFY_SOURCE_SHA256}"

ENV NODE_ENV=production
ENV EDITION=SELF_HOSTED
ENV DEPLOY_ENV=PRODUCTION
ENV CONSOLE_API_URL=http://127.0.0.1:5001
ENV APP_API_URL=http://127.0.0.1:5001
ENV MARKETPLACE_API_URL=https://marketplace.dify.ai
ENV MARKETPLACE_URL=https://marketplace.dify.ai
ENV PORT=3000
ENV EXPERIMENTAL_ENABLE_VINEXT=false
ENV NEXT_TELEMETRY_DISABLED=1

ENV TZ=UTC
RUN ln -s /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone

ARG dify_uid=1001
RUN addgroup -S -g ${dify_uid} dify && \
    adduser -S -u ${dify_uid} -G dify -s /bin/ash -h /home/dify dify && \
    mkdir /app && \
    chown -R dify:dify /app

WORKDIR /app

COPY --from=builder --chown=dify:dify /app/web/public ./targets/next/web/public
COPY --from=builder --chown=dify:dify /app/web/.next/standalone ./targets/next/
COPY --from=builder --chown=dify:dify /app/web/.next/static ./targets/next/web/.next/static
COPY --from=builder --chown=dify:dify /app/web/dist/standalone ./targets/vinext

COPY --from=source --chown=dify:dify --chmod=755 /src/web/docker/entrypoint.sh ./entrypoint.sh
COPY --chown=dify:dify LICENSE NOTICE-CHANGES.md THIRD_PARTY_NOTICES.md /licenses/neurwerk-addon-dify-ce-builder/
COPY --chown=dify:dify LICENSES/ /licenses/neurwerk-addon-dify-ce-builder/LICENSES/

ARG COMMIT_SHA
ENV COMMIT_SHA=${COMMIT_SHA}

USER dify
EXPOSE 3000
ENTRYPOINT ["/bin/sh", "./entrypoint.sh"]
