# check=skip=InvalidDefaultArgInFrom
# Base image arguments intentionally require digest-pinned values from deploy.sh.
# =============================================================================
# SPDX-License-Identifier: MIT
# addon-dify-ce-builder - Dify CE API + Keycloak OIDC auth
#
# This image extends the langgenius/dify-api image by adding a Keycloak OAuth
# provider that follows the same pattern as GitHub/Google OAuth.
# =============================================================================

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
ARG BUILDER_REVISION
ARG DIFY_API_IMAGE_DIGEST
ARG DIFY_VERSION
ARG IMAGE_VERSION

LABEL org.opencontainers.image.title="Neurwerk Dify CE API overlay" \
  org.opencontainers.image.description="Dify CE API with the Neurwerk overlay" \
  org.opencontainers.image.source="https://github.com/neurwerk/addon_dify_ce_builder" \
  org.opencontainers.image.url="https://github.com/neurwerk/addon_dify_ce_builder" \
  org.opencontainers.image.version="${IMAGE_VERSION}" \
  org.opencontainers.image.revision="${BUILDER_REVISION}" \
  org.opencontainers.image.base.name="${BASE_IMAGE}" \
  com.neurwerk.dify.version="${DIFY_VERSION}" \
  com.neurwerk.dify.api-base.digest="${DIFY_API_IMAGE_DIGEST}"

USER root

# The upstream API image already contains PyJWT 2.13.0 and cryptography.
# Do not downgrade either dependency in the overlay.

# Patch the pinned upstream service instead of replacing its changed modules.
COPY overlay/api/neurwerk_sso.py /app/api/neurwerk_sso.py
COPY overlay/api/neurwerk_settings.py /app/api/neurwerk_settings.py
COPY overlay/scripts/patch_dify.py /tmp/patch_dify.py
RUN python /tmp/patch_dify.py api /app/api && rm /tmp/patch_dify.py
COPY overlay/scripts/ /app/api/scripts/
COPY overlay/plugins/ /app/api/plugins-offline/
COPY LICENSE NOTICE-CHANGES.md THIRD_PARTY_NOTICES.md /licenses/neurwerk-addon-dify-ce-builder/
COPY LICENSES/ /licenses/neurwerk-addon-dify-ce-builder/LICENSES/

# Fix ownership
RUN chown -R dify:dify \
  /app/api/neurwerk_sso.py \
  /app/api/neurwerk_settings.py \
  /app/api/configs/app_config.py \
  /app/api/extensions/ext_application_services.py \
  /app/api/controllers/console/auth/oauth.py \
  /app/api/scripts/ \
  /app/api/plugins-offline/

# Create home directory for uv runtime cache ($HOME/.cache/uv)
RUN mkdir -p /home/dify && chown dify:dify /home/dify

# Switch back to non-root user
USER dify

EXPOSE 5001
ENTRYPOINT ["/entrypoint.sh"]
