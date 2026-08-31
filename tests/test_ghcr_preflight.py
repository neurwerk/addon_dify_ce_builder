import json
from email.message import Message

import pytest

from scripts import ghcr_preflight


def _response(status: int, body=None, *, location: str | None = None):
    headers = Message()
    if location is not None:
        headers["Location"] = location
    encoded_body = b"" if body is None else json.dumps(body).encode()
    return ghcr_preflight.Response(status, headers, encoded_body)


class Responses:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return next(self.responses)


def _token(value: str):
    return _response(200, {"token": value})


def _upload(repository: str):
    return _response(202, location=f"/v2/{repository}/blobs/uploads/id?_state=state")


def _absent(code: str = "MANIFEST_UNKNOWN"):
    return _response(404, {"errors": [{"code": code, "message": "not found"}]})


def test_preflight_proves_all_push_destinations_before_checking_tags():
    api = "neurwerk/addon-dify-ce-builder-api"
    web = "neurwerk/addon-dify-ce-builder-web"
    responses = Responses(
        [
            _token("api-token"),
            _upload(api),
            _response(204),
            _token("web-token"),
            _upload(web),
            _response(204),
            _absent(),
            _absent("NAME_UNKNOWN"),
        ]
    )

    ghcr_preflight.preflight([api, web], "1.15.0-kc-v15", "user", "token", responses)

    assert [request.get_method() for request in responses.requests] == [
        "GET",
        "POST",
        "DELETE",
        "GET",
        "POST",
        "DELETE",
        "GET",
        "GET",
    ]
    assert all("manifests" not in request.full_url for request in responses.requests[:6])


def test_preflight_rejects_an_existing_immutable_tag():
    repository = "neurwerk/addon-dify-ce-builder-api"
    responses = Responses([_token("token"), _upload(repository), _response(204), _response(200)])

    with pytest.raises(ghcr_preflight.TagExistsError, match="already exists"):
        ghcr_preflight.preflight([repository], "1.15.0-kc-v15", "user", "token", responses)


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ([_response(401)], "credentials and push authorization are ambiguous"),
        ([_token("token"), _response(403)], "push authorization is not proven"),
        (
            [_token("token"), _upload("neurwerk/package"), _response(500)],
            "upload-probe cleanup",
        ),
        (
            [
                _token("token"),
                _upload("neurwerk/package"),
                _response(204),
                _response(503),
            ],
            "tag availability is ambiguous",
        ),
        (
            [
                _token("token"),
                _upload("neurwerk/package"),
                _response(204),
                _response(404, {"errors": [{"code": "DENIED"}]}),
            ],
            "ambiguous code DENIED",
        ),
    ],
)
def test_preflight_fails_closed_on_registry_or_authorization_ambiguity(responses, message):
    with pytest.raises(ghcr_preflight.PreflightError, match=message):
        ghcr_preflight.preflight(
            ["neurwerk/package"], "1.15.0-kc-v15", "user", "token", Responses(responses)
        )
