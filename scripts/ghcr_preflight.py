# SPDX-License-Identifier: MIT
"""Fail-closed GHCR destination checks for the interactive release script."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from email.message import Message

REGISTRY = "https://ghcr.io"
TOKEN_ENDPOINT = "https://ghcr.io/token"
MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
REPOSITORY_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*")


class PreflightError(RuntimeError):
    """A registry response could not prove that publication is safe."""


class TagExistsError(PreflightError):
    """The requested immutable tag is already present."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Message
    body: bytes


RequestFn = Callable[[urllib.request.Request], Response]


def _request(request: urllib.request.Request) -> Response:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return Response(response.status, response.headers, response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read())
    except (OSError, urllib.error.URLError) as exc:
        raise PreflightError(f"registry request failed: {exc}") from exc


def _response_detail(response: Response) -> str:
    details = [f"HTTP {response.status}"]
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        value = None
    if isinstance(value, dict) and isinstance(value.get("errors"), list):
        registry_errors = []
        for error in value["errors"][:3]:
            if not isinstance(error, dict):
                continue
            code = error.get("code")
            message = error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                message = " ".join(message.split())[:300]
                registry_errors.append(f"{code}: {message}")
            elif isinstance(code, str):
                registry_errors.append(code)
        if registry_errors:
            details.append("registry error " + " | ".join(registry_errors))
    request_id = response.headers.get("X-GitHub-Request-Id")
    if request_id:
        details.append(f"GitHub request ID {request_id}")
    return "; ".join(details)


def _json_object(response: Response, context: str) -> dict[str, object]:
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError(
            f"{context} returned invalid JSON ({_response_detail(response)})"
        ) from exc
    if not isinstance(value, dict):
        raise PreflightError(
            f"{context} returned a non-object JSON response ({_response_detail(response)})"
        )
    return value


def _bearer_token(
    repository: str,
    username: str,
    password: str,
    request_fn: RequestFn,
) -> str:
    query = urllib.parse.urlencode(
        {
            "service": "ghcr.io",
            "scope": f"repository:{repository}:pull,push",
        }
    )
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{TOKEN_ENDPOINT}?{query}",
        headers={"Authorization": f"Basic {basic}"},
    )
    try:
        response = request_fn(request)
    except PreflightError as exc:
        raise PreflightError(f"GHCR token exchange for {repository} failed: {exc}") from exc
    if response.status != 200:
        raise PreflightError(
            f"GHCR token exchange for {repository} failed: {_response_detail(response)}; "
            "credentials and push authorization are ambiguous"
        )
    token = _json_object(response, f"GHCR token exchange for {repository}").get("token")
    if not isinstance(token, str) or not token:
        raise PreflightError(f"GHCR token exchange for {repository} returned no bearer token")
    return token


def _bearer_request(
    method: str,
    url: str,
    token: str,
    request_fn: RequestFn,
    *,
    accept: str | None = None,
) -> Response:
    headers = {"Authorization": f"Bearer {token}"}
    if accept is not None:
        headers["Accept"] = accept
    return request_fn(urllib.request.Request(url, headers=headers, method=method))


def _verify_push_access(repository: str, token: str, request_fn: RequestFn) -> None:
    upload_url = f"{REGISTRY}/v2/{repository}/blobs/uploads/"
    try:
        response = _bearer_request("POST", upload_url, token, request_fn)
    except PreflightError as exc:
        raise PreflightError(f"GHCR upload initiation for {repository} failed: {exc}") from exc
    if response.status != 202:
        raise PreflightError(
            f"GHCR upload initiation for {repository} failed: {_response_detail(response)}; "
            "push authorization is not proven"
        )

    location = response.headers.get("Location")
    if not location:
        raise PreflightError(
            f"GHCR upload initiation for {repository} returned no upload location "
            f"({_response_detail(response)})"
        )
    print("  OK upload initiation accepted (HTTP 202); push access is proven.")

    cancel_url = urllib.parse.urljoin(REGISTRY, location)
    try:
        cancel_response = _bearer_request("DELETE", cancel_url, token, request_fn)
    except PreflightError as exc:
        print(
            f"  WARNING upload cleanup for ghcr.io/{repository} could not be confirmed: {exc}. "
            "Continuing because cleanup is best-effort; the empty session will expire.",
            file=sys.stderr,
        )
        return
    if cancel_response.status == 204:
        print("  OK empty upload session cancelled (HTTP 204).")
        return
    print(
        f"  WARNING upload cleanup for ghcr.io/{repository} was not accepted: "
        f"{_response_detail(cancel_response)}. "
        "Continuing because cleanup is best-effort; the empty session will expire.",
        file=sys.stderr,
    )


def _registry_error_code(response: Response, context: str) -> str:
    value = _json_object(response, context)
    errors = value.get("errors")
    if not isinstance(errors, list) or len(errors) != 1 or not isinstance(errors[0], dict):
        raise PreflightError(f"{context} returned ambiguous errors ({_response_detail(response)})")
    code = errors[0].get("code")
    if not isinstance(code, str):
        raise PreflightError(f"{context} returned no error code ({_response_detail(response)})")
    return code


def _verify_tag_absent(repository: str, tag: str, token: str, request_fn: RequestFn) -> None:
    manifest_url = f"{REGISTRY}/v2/{repository}/manifests/{urllib.parse.quote(tag, safe='._-')}"
    context = f"GHCR manifest lookup for {repository}:{tag}"
    try:
        response = _bearer_request("GET", manifest_url, token, request_fn, accept=MANIFEST_ACCEPT)
    except PreflightError as exc:
        raise PreflightError(f"{context} failed: {exc}") from exc
    if response.status == 200:
        raise TagExistsError(f"immutable destination already exists: ghcr.io/{repository}:{tag}")
    if response.status != 404:
        raise PreflightError(
            f"{context} failed: {_response_detail(response)}; tag availability is ambiguous"
        )
    code = _registry_error_code(response, context)
    if code not in {"MANIFEST_UNKNOWN", "NAME_UNKNOWN"}:
        raise PreflightError(
            f"{context} returned ambiguous code {code} ({_response_detail(response)})"
        )


def preflight(
    repositories: Sequence[str],
    tag: str,
    username: str,
    password: str,
    request_fn: RequestFn = _request,
) -> None:
    if not repositories:
        raise PreflightError("no GHCR destinations were selected")
    if not username or not password:
        raise PreflightError("GHCR username and token are required")
    for repository in repositories:
        if not REPOSITORY_PATTERN.fullmatch(repository):
            raise PreflightError(f"invalid GHCR repository: {repository}")

    tokens: dict[str, str] = {}
    total = len(repositories)
    for index, repository in enumerate(repositories, start=1):
        print(f"Write access {index}/{total}: ghcr.io/{repository}")
        token = _bearer_token(repository, username, password, request_fn)
        print("  OK scoped pull,push bearer token received.")
        _verify_push_access(repository, token, request_fn)
        tokens[repository] = token

    for index, repository in enumerate(repositories, start=1):
        print(f"Tag availability {index}/{total}: ghcr.io/{repository}:{tag}")
        _verify_tag_absent(repository, tag, tokens[repository], request_fn)
        print("  OK immutable tag is absent.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove GHCR push access and immutable-tag availability before Dify builds."
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("repositories", nargs="+")
    args = parser.parse_args(argv)

    username = os.environ.get("GHCR_USERNAME") or input("GHCR username: ").strip()
    password = os.environ.get("GHCR_TOKEN") or getpass.getpass(
        "GHCR token with write:packages permission: "
    )
    try:
        preflight(args.repositories, args.tag, username, password)
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for repository in args.repositories:
        print(f"Preflight passed: ghcr.io/{repository}:{args.tag} is absent and writable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
