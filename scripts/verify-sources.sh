#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UPSTREAM_REPOSITORY="https://github.com/langgenius/dify.git"

read_value() {
  local file="$1"
  local value

  value="$(tr -d '\r\n' < "${REPO_DIR}/${file}")"
  if [[ -z "${value}" ]]; then
    printf 'ERROR: %s is empty.\n' "${file}" >&2
    exit 1
  fi
  printf '%s' "${value}"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f 1
  else
    shasum -a 256 "$1" | cut -d ' ' -f 1
  fi
}

DIFY_VERSION="$(read_value DIFY_VERSION)"
DIFY_API_IMAGE_DIGEST="$(read_value DIFY_API_IMAGE_DIGEST)"
DIFY_SOURCE_REVISION="$(read_value DIFY_SOURCE_REVISION)"
DIFY_SOURCE_SHA256="$(read_value DIFY_SOURCE_SHA256)"
NODE_IMAGE_DIGEST="$(read_value NODE_IMAGE_DIGEST)"
ALPINE_IMAGE_DIGEST="$(read_value ALPINE_IMAGE_DIGEST)"

if [[ ! "${DIFY_SOURCE_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ERROR: DIFY_SOURCE_REVISION must be a full Git commit SHA.\n' >&2
  exit 1
fi
if [[ ! "${DIFY_API_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf 'ERROR: DIFY_API_IMAGE_DIGEST must be a SHA-256 digest.\n' >&2
  exit 1
fi
if [[ ! "${NODE_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf 'ERROR: NODE_IMAGE_DIGEST must be a SHA-256 digest.\n' >&2
  exit 1
fi
if [[ ! "${ALPINE_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf 'ERROR: ALPINE_IMAGE_DIGEST must be a SHA-256 digest.\n' >&2
  exit 1
fi
if [[ ! "${DIFY_SOURCE_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  printf 'ERROR: DIFY_SOURCE_SHA256 must be a SHA-256 value.\n' >&2
  exit 1
fi

remote_revision="$(git ls-remote "${UPSTREAM_REPOSITORY}" "refs/tags/${DIFY_VERSION}" | cut -f 1)"
if [[ "${remote_revision}" != "${DIFY_SOURCE_REVISION}" ]]; then
  printf 'ERROR: Dify tag %s resolves to %s, expected %s.\n' \
    "${DIFY_VERSION}" "${remote_revision:-nothing}" "${DIFY_SOURCE_REVISION}" >&2
  exit 1
fi

temporary_dir="$(mktemp -d)"
trap 'rm -rf "${temporary_dir}"' EXIT
archive="${temporary_dir}/dify-${DIFY_VERSION}.tar.gz"

curl --fail --location --silent --show-error \
  "https://github.com/langgenius/dify/archive/refs/tags/${DIFY_VERSION}.tar.gz" \
  --output "${archive}"
actual_source_sha256="$(sha256_file "${archive}")"
if [[ "${actual_source_sha256}" != "${DIFY_SOURCE_SHA256}" ]]; then
  printf 'ERROR: Dify source checksum is %s, expected %s.\n' \
    "${actual_source_sha256}" "${DIFY_SOURCE_SHA256}" >&2
  exit 1
fi

docker_hub_index_digest() {
  local repository="$1"
  local tag="$2"
  local docker_token
  local docker_token_response="${temporary_dir}/docker-token.json"
  local image_headers="${temporary_dir}/docker-image-headers.txt"

  curl --fail --location --silent --show-error \
    --get \
    --data-urlencode "service=registry.docker.io" \
    --data-urlencode "scope=repository:${repository}:pull" \
    "https://auth.docker.io/token" \
    --output "${docker_token_response}"
  docker_token="$(python3 - "${docker_token_response}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as token_file:
    token = json.load(token_file).get("token", "")
if not isinstance(token, str) or not token:
    raise SystemExit("Docker Hub returned no pull token")
print(token)
PY
  )"
  curl --fail --location --silent --show-error \
    --header "Authorization: Bearer ${docker_token}" \
    --header "Accept: application/vnd.oci.image.index.v1+json" \
    --dump-header "${image_headers}" \
    --output /dev/null \
    "https://registry-1.docker.io/v2/${repository}/manifests/${tag}"
  python3 - "${image_headers}" <<'PY'
import re
import sys

digest = ""
media_type = ""
with open(sys.argv[1], encoding="utf-8") as headers_file:
    for line in headers_file:
        name, separator, value = line.partition(":")
        if separator and name.lower() == "docker-content-digest":
            digest = value.strip()
        if separator and name.lower() == "content-type":
            media_type = value.strip().partition(";")[0]
if media_type != "application/vnd.oci.image.index.v1+json":
    raise SystemExit(f"Docker Hub returned non-OCI-index media type: {media_type or 'none'}")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise SystemExit("Docker Hub returned no valid manifest digest")
print(digest)
PY
}

actual_api_digest="$(docker_hub_index_digest langgenius/dify-api "${DIFY_VERSION}")"
if [[ "${actual_api_digest}" != "${DIFY_API_IMAGE_DIGEST}" ]]; then
  printf 'ERROR: Dify API image digest is %s, expected %s.\n' \
    "${actual_api_digest}" "${DIFY_API_IMAGE_DIGEST}" >&2
  exit 1
fi

actual_node_digest="$(docker_hub_index_digest library/node 22.22.1-alpine)"
if [[ "${actual_node_digest}" != "${NODE_IMAGE_DIGEST}" ]]; then
  printf 'ERROR: Node image OCI index digest is %s, expected %s.\n' \
    "${actual_node_digest}" "${NODE_IMAGE_DIGEST}" >&2
  exit 1
fi

actual_alpine_digest="$(docker_hub_index_digest library/alpine 3.21)"
if [[ "${actual_alpine_digest}" != "${ALPINE_IMAGE_DIGEST}" ]]; then
  printf 'ERROR: Alpine image OCI index digest is %s, expected %s.\n' \
    "${actual_alpine_digest}" "${ALPINE_IMAGE_DIGEST}" >&2
  exit 1
fi

(
  cd "${REPO_DIR}/overlay/plugins"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check SHA256SUMS.txt
  else
    shasum -a 256 --check SHA256SUMS.txt
  fi
)

printf 'Verified Dify %s Web source %s (%s), API image %s, Node image %s, ' \
  "${DIFY_VERSION}" "${DIFY_SOURCE_REVISION}" "${DIFY_SOURCE_SHA256}" \
  "${DIFY_API_IMAGE_DIGEST}" "${NODE_IMAGE_DIGEST}"
printf 'Alpine image %s, and plugin checksums.\n' "${ALPINE_IMAGE_DIGEST}"
