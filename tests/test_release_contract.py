import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text().strip()


def test_upstream_provenance_values_have_expected_shape():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _read("DIFY_VERSION"))
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", _read("DIFY_API_IMAGE_DIGEST"))
    assert re.fullmatch(r"[0-9a-f]{40}", _read("DIFY_SOURCE_REVISION"))
    assert re.fullmatch(r"[0-9a-f]{64}", _read("DIFY_SOURCE_SHA256"))
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", _read("NODE_IMAGE_DIGEST"))
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", _read("ALPINE_IMAGE_DIGEST"))


def test_web_build_verifies_the_authoritative_source_archive():
    dockerfile = _read("docker/web.Dockerfile")

    assert "COPY DIFY_VERSION DIFY_SOURCE_REVISION DIFY_SOURCE_SHA256" in dockerfile
    assert "sha256sum --check" in dockerfile
    assert "ARG DIFY_VERSION=" not in dockerfile


def test_dify_version_drives_dockerfiles_and_version_bound_overlay():
    version = _read("DIFY_VERSION")
    deploy_script = _read("deploy.sh")
    api_dockerfile = _read("docker/api.Dockerfile")
    web_dockerfile = _read("docker/web.Dockerfile")
    account_service_patch = _read("overlay/api/services/account_service_patch.py")

    assert (
        "BASE_IMAGE=langgenius/dify-api:${DIFY_VERSION}@${DIFY_API_IMAGE_DIGEST}" in deploy_script
    )
    assert '--build-arg "DIFY_VERSION=${DIFY_VERSION}"' in deploy_script
    assert "ARG BASE_IMAGE" in api_dockerfile
    assert "FROM ${BASE_IMAGE}" in api_dockerfile
    assert "COPY DIFY_VERSION DIFY_SOURCE_REVISION DIFY_SOURCE_SHA256" in web_dockerfile
    assert f"langgenius/dify-api {version} implementations" in account_service_patch


def test_images_receive_version_and_revision_labels():
    for path in ("docker/api.Dockerfile", "docker/web.Dockerfile"):
        dockerfile = _read(path)
        assert 'org.opencontainers.image.version="${IMAGE_VERSION}"' in dockerfile
        assert 'org.opencontainers.image.revision="${BUILDER_REVISION}"' in dockerfile
    assert 'com.neurwerk.dify.api-base.digest="${DIFY_API_IMAGE_DIGEST}"' in _read(
        "docker/api.Dockerfile"
    )
    assert 'com.neurwerk.dify.web-source.revision="${DIFY_SOURCE_REVISION}"' in _read(
        "docker/web.Dockerfile"
    )


def test_deploy_requires_clean_source_and_never_publishes_latest():
    deploy_script = _read("deploy.sh")

    assert "git status --porcelain" in deploy_script
    assert "containerimage.digest" in deploy_script
    assert "Mutable latest tags are prohibited" in deploy_script
    assert ":latest" not in deploy_script
    assert 'VERSION="${1:-' not in deploy_script
    assert "git fetch --quiet --no-tags origin" in deploy_script
    assert '[[ "${BUILDER_REVISION}" != "${canonical_revision}" ]]' in deploy_script
    assert "git merge-base --is-ancestor" not in deploy_script
    assert deploy_script.index("docker login ghcr.io") < deploy_script.index(
        "scripts/ghcr_preflight.py"
    )
    assert 'docker login ghcr.io --username "${ghcr_username}" --password-stdin' in deploy_script
    assert 'GHCR_USERNAME="${ghcr_username}" GHCR_TOKEN="${ghcr_token}"' in deploy_script
    assert deploy_script.index("scripts/ghcr_preflight.py") < deploy_script.index(
        "docker buildx build"
    )


def test_web_base_images_are_index_digest_pinned_and_labeled():
    deploy_script = _read("deploy.sh")
    dockerfile = _read("docker/web.Dockerfile")

    assert 'NODE_IMAGE="docker.io/library/node:22.22.1-alpine"' in deploy_script
    assert 'ALPINE_IMAGE="docker.io/library/alpine:3.21"' in deploy_script
    assert "NODE_IMAGE=${NODE_IMAGE}@${NODE_IMAGE_DIGEST}" in deploy_script
    assert "ALPINE_IMAGE=${ALPINE_IMAGE}@${ALPINE_IMAGE_DIGEST}" in deploy_script
    assert "FROM ${NODE_IMAGE} AS base" in dockerfile
    assert "FROM ${ALPINE_IMAGE} AS source" in dockerfile
    assert 'org.opencontainers.image.base.name="${NODE_IMAGE_NAME}"' in dockerfile
    assert 'org.opencontainers.image.base.digest="${NODE_IMAGE_DIGEST}"' in dockerfile
    assert 'com.neurwerk.dify.web-source-base.name="${ALPINE_IMAGE_NAME}"' in dockerfile
    assert 'com.neurwerk.dify.web-source-base.digest="${ALPINE_IMAGE_DIGEST}"' in dockerfile


def test_api_base_and_addon_install_are_reproducibly_pinned():
    deploy_script = _read("deploy.sh")
    dockerfile = _read("docker/api.Dockerfile")
    requirements = _read("requirements/addon.txt")

    assert "@${DIFY_API_IMAGE_DIGEST}" in deploy_script
    assert "--require-hashes" in dockerfile
    assert "--only-binary=:all:" in dockerfile
    assert "--no-deps" in dockerfile
    assert requirements.count("pyjwt==2.10.1") == 1
    assert requirements.count("--hash=sha256:") == 2


def test_every_dify_derived_overlay_has_a_modification_notice():
    python_files = [
        "overlay/api/libs/oauth.py",
        "overlay/api/controllers/console/auth/oauth.py",
        "overlay/api/configs/feature/__init__.py",
        "overlay/api/app_factory.py",
        "overlay/api/services/account_service_patch.py",
        "overlay/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py",
        "overlay/api/migrations/versions/2025_07_02_2332-1c9ba48be8e4_add_uuidv7_function_in_sql.py",
    ]
    typescript_files = [
        "overlay/web/app/signin/normal-form.tsx",
        "overlay/web/app/signin/components/sso-redirect.tsx",
        "overlay/web/app/signin/components/social-auth.tsx",
    ]

    for path in python_files + typescript_files:
        content = _read(path)
        assert "Modified by Neurwerk, 2025-2026" in content
        assert "Apache License 2.0 with additional conditions" in content
    assert "_licenseNotice" in _read("overlay/web/i18n/en-US/login.json")


def test_bundled_plugin_is_intentionally_available_to_docker():
    dockerignore = _read(".dockerignore")

    assert "*.difypkg" in dockerignore
    assert "!overlay/plugins/*.difypkg" in dockerignore
