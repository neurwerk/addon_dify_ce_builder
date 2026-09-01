#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Interactive, user-operated builder for the Dify API and Web overlay images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

API_IMAGE="ghcr.io/neurwerk/addon-dify-ce-builder-api"
WEB_IMAGE="ghcr.io/neurwerk/addon-dify-ce-builder-web"
API_REPOSITORY="neurwerk/addon-dify-ce-builder-api"
WEB_REPOSITORY="neurwerk/addon-dify-ce-builder-web"
SOURCE_URL="https://github.com/neurwerk/addon_dify_ce_builder"
NODE_IMAGE="docker.io/library/node:22.22.1-alpine"
ALPINE_IMAGE="docker.io/library/alpine:3.21"

usage() {
  printf 'Usage: %s <immutable-version>\n' "${0##*/}" >&2
  printf 'Example: %s 1.15.0-kc-v15\n' "${0##*/}" >&2
}

read_value() {
  local file="$1"
  local value

  value="$(tr -d '\r\n' < "${file}")"
  if [[ -z "${value}" ]]; then
    printf 'ERROR: %s is empty.\n' "${file}" >&2
    exit 1
  fi
  printf '%s' "${value}"
}

metadata_digest() {
  python3 - "$1" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as metadata_file:
    digest = json.load(metadata_file).get("containerimage.digest", "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise SystemExit("Buildx metadata did not contain a valid image digest")
print(digest)
PY
}

if (( $# != 1 )); then
  usage
  exit 2
fi

VERSION="$1"
DIFY_VERSION="$(read_value DIFY_VERSION)"
DIFY_API_IMAGE_DIGEST="$(read_value DIFY_API_IMAGE_DIGEST)"
DIFY_SOURCE_REVISION="$(read_value DIFY_SOURCE_REVISION)"
DIFY_SOURCE_SHA256="$(read_value DIFY_SOURCE_SHA256)"
NODE_IMAGE_DIGEST="$(read_value NODE_IMAGE_DIGEST)"
ALPINE_IMAGE_DIGEST="$(read_value ALPINE_IMAGE_DIGEST)"
version_lower="$(printf '%s' "${VERSION}" | tr '[:upper:]' '[:lower:]')"

if [[ ! "${VERSION}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  printf 'ERROR: Version is not a valid container tag.\n' >&2
  exit 2
fi
if [[ "${VERSION}" != "${DIFY_VERSION}-"* ]]; then
  printf 'ERROR: Version must start with the upstream version: %s-\n' "${DIFY_VERSION}" >&2
  exit 2
fi
if [[ "${version_lower}" =~ (^|[._-])latest($|[._-]) ]]; then
  printf 'ERROR: Mutable latest tags are prohibited.\n' >&2
  exit 2
fi
if [[ ! "${DIFY_API_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || [[ ! "${DIFY_SOURCE_REVISION}" =~ ^[0-9a-f]{40}$ ]] \
  || [[ ! "${DIFY_SOURCE_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
  || [[ ! "${NODE_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || [[ ! "${ALPINE_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf 'ERROR: Invalid source provenance files.\n' >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'ERROR: Builds must run from a Git checkout.\n' >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  printf 'ERROR: Git worktree must be clean before building or publishing.\n' >&2
  exit 1
fi
BUILDER_REVISION="$(git rev-parse HEAD)"

verify_canonical_source() {
  local canonical_revision
  local origin_url

  origin_url="$(git remote get-url origin 2>/dev/null || true)"
  case "${origin_url}" in
    git@github.com:neurwerk/addon_dify_ce_builder.git | \
      https://github.com/neurwerk/addon_dify_ce_builder | \
      https://github.com/neurwerk/addon_dify_ce_builder.git) ;;
    *)
      printf 'ERROR: origin must be the canonical repository, not %s.\n' \
        "${origin_url:-an unset remote}" >&2
      exit 1
      ;;
  esac

  printf 'Fetching canonical origin/main ...\n'
  if ! git fetch --quiet --no-tags origin \
    refs/heads/main:refs/remotes/origin/main; then
    printf 'ERROR: Could not fetch canonical origin/main.\n' >&2
    exit 1
  fi
  canonical_revision="$(git rev-parse refs/remotes/origin/main)"
  if [[ "${BUILDER_REVISION}" != "${canonical_revision}" ]]; then
    printf 'ERROR: HEAD %s must equal fetched canonical origin/main %s.\n' \
      "${BUILDER_REVISION}" "${canonical_revision}" >&2
    exit 1
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  printf 'ERROR: Python 3 is required to read Buildx digest metadata.\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'ERROR: Docker is not running.\n' >&2
  exit 1
fi

printf '=== Neurwerk Dify CE image builder ===\n'
printf 'Overlay version: %s\n' "${VERSION}"
printf 'Dify version:    %s\n' "${DIFY_VERSION}"
printf 'Builder commit:  %s\n\n' "${BUILDER_REVISION}"

build_api=false
build_web=false
build_amd64=true
build_arm64=false

read -r -p "Build API image? (Y/n): " answer
[[ ! "${answer}" =~ ^[Nn]$ ]] && build_api=true

read -r -p "Build Web image? (Y/n): " answer
[[ ! "${answer}" =~ ^[Nn]$ ]] && build_web=true

if ! ${build_api} && ! ${build_web}; then
  printf 'ERROR: No image selected.\n' >&2
  exit 1
fi

read -r -p "Build for linux/amd64? (Y/n): " answer
[[ "${answer}" =~ ^[Nn]$ ]] && build_amd64=false

read -r -p "Build for linux/arm64? (y/N): " answer
[[ "${answer}" =~ ^[Yy]$ ]] && build_arm64=true

if ! ${build_amd64} && ! ${build_arm64}; then
  printf 'ERROR: No platform selected.\n' >&2
  exit 1
fi

push_images=true
read -r -p "Push all selected platforms to GHCR? (Y/n): " answer
[[ "${answer}" =~ ^[Nn]$ ]] && push_images=false

platforms=()
${build_amd64} && platforms+=("linux/amd64")
${build_arm64} && platforms+=("linux/arm64")
if ! ${push_images} && (( ${#platforms[@]} > 1 )); then
  printf 'ERROR: A local --load build supports exactly one platform.\n' >&2
  exit 1
fi
platform_string="$(IFS=,; printf '%s' "${platforms[*]}")"

selected_images=()
selected_repositories=()
${build_api} && selected_images+=("API")
${build_api} && selected_repositories+=("${API_REPOSITORY}")
${build_web} && selected_images+=("Web")
${build_web} && selected_repositories+=("${WEB_REPOSITORY}")
output_action="load locally"
${push_images} && output_action="push to GHCR"

printf '\nImages:    %s\n' "$(IFS=,; printf '%s' "${selected_images[*]}")"
printf 'Platforms: %s\n' "${platform_string}"
printf 'Output:    %s\n\n' "${output_action}"
read -r -p "Proceed? (y/N): " answer
if [[ ! "${answer}" =~ ^[Yy]$ ]]; then
  printf 'Cancelled.\n'
  exit 0
fi

if ${push_images}; then
  verify_canonical_source
  ghcr_username="${GHCR_USERNAME:-}"
  ghcr_token="${GHCR_TOKEN:-}"
  if [[ -z "${ghcr_username}" ]]; then
    read -r -p "GHCR username: " ghcr_username
  fi
  if [[ -z "${ghcr_token}" ]]; then
    read -r -s -p "GHCR token with write:packages permission: " ghcr_token
    printf '\n'
  fi
  if [[ -z "${ghcr_username}" || -z "${ghcr_token}" ]]; then
    printf 'ERROR: GHCR username and token are required.\n' >&2
    exit 1
  fi
  printf 'Preflighting every selected GHCR destination ...\n'
  GHCR_USERNAME="${ghcr_username}" GHCR_TOKEN="${ghcr_token}" \
    python3 scripts/ghcr_preflight.py --tag "${VERSION}" "${selected_repositories[@]}"
  printf 'Logging Docker in to ghcr.io with the verified preflight credentials ...\n'
  if ! printf '%s' "${ghcr_token}" \
    | docker login ghcr.io --username "${ghcr_username}" --password-stdin; then
    printf 'ERROR: Docker login to ghcr.io failed after registry preflight passed.\n' >&2
    exit 1
  fi
  unset ghcr_token
fi

output_flags=(--load)
${push_images} && output_flags=(--push)
metadata_dir="$(mktemp -d)"
trap 'rm -rf "${metadata_dir}"' EXIT
results=()

common_build_args=(
  --build-arg "DIFY_VERSION=${DIFY_VERSION}"
  --build-arg "IMAGE_VERSION=${VERSION}"
  --build-arg "BUILDER_REVISION=${BUILDER_REVISION}"
)

if ${build_api}; then
  api_metadata="${metadata_dir}/api.json"
  printf '\n=== Building API image for %s ===\n' "${platform_string}"
  docker buildx build \
    --platform "${platform_string}" \
    --tag "${API_IMAGE}:${VERSION}" \
    --file docker/api.Dockerfile \
    --build-arg "BASE_IMAGE=langgenius/dify-api:${DIFY_VERSION}@${DIFY_API_IMAGE_DIGEST}" \
    --build-arg "DIFY_API_IMAGE_DIGEST=${DIFY_API_IMAGE_DIGEST}" \
    "${common_build_args[@]}" \
    --metadata-file "${api_metadata}" \
    "${output_flags[@]}" \
    .
  api_digest="$(metadata_digest "${api_metadata}")"
  results+=("${API_IMAGE}:${VERSION}@${api_digest}")
  printf 'API result: %s@%s\n' "${API_IMAGE}:${VERSION}" "${api_digest}"
fi

if ${build_web}; then
  web_metadata="${metadata_dir}/web.json"
  printf '\n=== Building Web image for %s ===\n' "${platform_string}"
  docker buildx build \
    --platform "${platform_string}" \
    --tag "${WEB_IMAGE}:${VERSION}" \
    --file docker/web.Dockerfile \
    --build-arg "COMMIT_SHA=${DIFY_SOURCE_REVISION}" \
    --build-arg "DIFY_SOURCE_REVISION=${DIFY_SOURCE_REVISION}" \
    --build-arg "DIFY_SOURCE_SHA256=${DIFY_SOURCE_SHA256}" \
    --build-arg "NODE_IMAGE=${NODE_IMAGE}@${NODE_IMAGE_DIGEST}" \
    --build-arg "NODE_IMAGE_NAME=${NODE_IMAGE}" \
    --build-arg "NODE_IMAGE_DIGEST=${NODE_IMAGE_DIGEST}" \
    --build-arg "ALPINE_IMAGE=${ALPINE_IMAGE}@${ALPINE_IMAGE_DIGEST}" \
    --build-arg "ALPINE_IMAGE_NAME=${ALPINE_IMAGE}" \
    --build-arg "ALPINE_IMAGE_DIGEST=${ALPINE_IMAGE_DIGEST}" \
    "${common_build_args[@]}" \
    --metadata-file "${web_metadata}" \
    "${output_flags[@]}" \
    .
  web_digest="$(metadata_digest "${web_metadata}")"
  results+=("${WEB_IMAGE}:${VERSION}@${web_digest}")
  printf 'Web result: %s@%s\n' "${WEB_IMAGE}:${VERSION}" "${web_digest}"
fi

printf '\n=== Completed ===\n'
printf 'Source: %s@%s\n' "${SOURCE_URL}" "${BUILDER_REVISION}"
printf 'Result: %s\n' "${results[@]}"
