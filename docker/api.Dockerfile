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

# The pinned Dify base supplies cryptography; install only the hash-locked addon.
COPY requirements/addon.txt /tmp/addon-requirements.txt
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes \
  --requirement /tmp/addon-requirements.txt \
  && rm /tmp/addon-requirements.txt

# Copy our overlay patches on top of the pristine CE source
# overlay/api/ mirrors the api/ tree inside the Dify source.
COPY overlay/api/libs/oauth.py /app/api/libs/oauth.py
COPY overlay/api/controllers/console/auth/oauth.py /app/api/controllers/console/auth/oauth.py
COPY overlay/api/configs/feature/__init__.py /app/api/configs/feature/__init__.py
COPY overlay/api/app_factory.py /app/api/app_factory.py
COPY overlay/api/services/account_service_patch.py /app/api/services/account_service_patch.py
COPY overlay/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py /app/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py
COPY overlay/api/migrations/versions/2025_07_02_2332-1c9ba48be8e4_add_uuidv7_function_in_sql.py /app/api/migrations/versions/2025_07_02_2332-1c9ba48be8e4_add_uuidv7_function_in_sql.py
COPY overlay/scripts/ /app/api/scripts/
COPY overlay/plugins/ /app/api/plugins-offline/
COPY LICENSE NOTICE-CHANGES.md THIRD_PARTY_NOTICES.md /licenses/neurwerk-addon-dify-ce-builder/
COPY LICENSES/ /licenses/neurwerk-addon-dify-ce-builder/LICENSES/

# Fix ownership
RUN chown -R dify:dify \
  /app/api/libs/oauth.py \
  /app/api/controllers/console/auth/oauth.py \
  /app/api/configs/feature/__init__.py \
  /app/api/app_factory.py \
  /app/api/services/account_service_patch.py \
  /app/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py \
  /app/api/migrations/versions/2025_07_02_2332-1c9ba48be8e4_add_uuidv7_function_in_sql.py \
  /app/api/scripts/ \
  /app/api/plugins-offline/

# Create home directory for uv runtime cache ($HOME/.cache/uv)
RUN mkdir -p /home/dify && chown dify:dify /home/dify

# Switch back to non-root user
USER dify

EXPOSE 5001
ENTRYPOINT ["/entrypoint.sh"]
