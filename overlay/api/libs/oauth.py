# Modified by Neurwerk, 2025-2026: add hardened Keycloak OIDC authentication.
# This Dify-derived file remains under the Dify Open Source License, based on
# Apache License 2.0 with additional conditions. See LICENSES/Dify-LICENSE and
# NOTICE-CHANGES.md.
import base64
import binascii
import hmac
import json
import logging
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import NotRequired, TypedDict, override

import httpx
import jwt
from flask import session
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from pydantic import TypeAdapter, ValidationError

from core.helper.http_client_pooling import get_pooled_http_client

logger = logging.getLogger(__name__)

type JsonObject = dict[str, object]
type JsonObjectList = list[JsonObject]

JSON_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
JSON_OBJECT_LIST_ADAPTER: TypeAdapter[JsonObjectList] = TypeAdapter(JsonObjectList)

# Reuse a pooled httpx.Client for OAuth flows (public endpoints, no SSRF proxy).
_http_client: httpx.Client = get_pooled_http_client(
    "oauth:default",
    lambda: httpx.Client(limits=httpx.Limits(max_keepalive_connections=50, max_connections=100)),
)


class AccessTokenResponse(TypedDict, total=False):
    access_token: str


class OAuthState(TypedDict, total=False):
    invite_token: str
    timezone: str
    language: str


class KeycloakOAuthState(OAuthState):
    nonce: str


class KeycloakRealmAccess(TypedDict):
    roles: list[str]


class GitHubEmailRecord(TypedDict, total=False):
    email: str
    primary: bool
    verified: bool


class GitHubRawUserInfo(TypedDict):
    id: int | str
    login: str
    name: NotRequired[str | None]
    email: NotRequired[str | None]


class GoogleRawUserInfo(TypedDict):
    sub: str
    email: str


ACCESS_TOKEN_RESPONSE_ADAPTER = TypeAdapter(AccessTokenResponse)
OAUTH_STATE_ADAPTER = TypeAdapter(OAuthState)
KEYCLOAK_OAUTH_STATE_ADAPTER = TypeAdapter(KeycloakOAuthState)
KEYCLOAK_REALM_ACCESS_ADAPTER = TypeAdapter(KeycloakRealmAccess)
GITHUB_RAW_USER_INFO_ADAPTER = TypeAdapter(GitHubRawUserInfo)
GITHUB_EMAIL_RECORDS_ADAPTER = TypeAdapter(list[GitHubEmailRecord])
GOOGLE_RAW_USER_INFO_ADAPTER = TypeAdapter(GoogleRawUserInfo)


@dataclass(frozen=True)
class OAuthUserInfo:
    id: str
    name: str
    email: str
    roles: frozenset[str] = frozenset()


def encode_oauth_state(
    invite_token: str | None = None,
    timezone: str | None = None,
    language: str | None = None,
) -> str | None:
    state: OAuthState = {}
    if invite_token:
        state["invite_token"] = invite_token
    if timezone:
        state["timezone"] = timezone
    if language:
        state["language"] = language
    if not state:
        return None

    raw_state = json.dumps(state, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw_state).decode("ascii").rstrip("=")


def decode_oauth_state(state: str | None) -> OAuthState:
    if not state:
        return {}

    try:
        padded_state = state + "=" * (-len(state) % 4)
        raw_state = base64.urlsafe_b64decode(padded_state.encode("ascii")).decode("utf-8")
        return OAUTH_STATE_ADAPTER.validate_python(json.loads(raw_state))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        return {}


def _json_object(response: httpx.Response) -> JsonObject:
    return JSON_OBJECT_ADAPTER.validate_python(response.json())


def _json_list(response: httpx.Response) -> JsonObjectList:
    return JSON_OBJECT_LIST_ADAPTER.validate_python(response.json())


class OAuth:
    client_id: str
    client_secret: str
    redirect_uri: str

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_authorization_url(
        self,
        invite_token: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
    ) -> str:
        raise NotImplementedError()

    def get_access_token(self, code: str) -> str:
        raise NotImplementedError()

    def get_raw_user_info(self, token: str) -> JsonObject:
        raise NotImplementedError()

    def get_user_info(self, token: str) -> OAuthUserInfo:
        raw_info = self.get_raw_user_info(token)
        return self._transform_user_info(raw_info)

    def _transform_user_info(self, raw_info: JsonObject) -> OAuthUserInfo:
        raise NotImplementedError()


class GitHubOAuth(OAuth):
    _AUTH_URL = "https://github.com/login/oauth/authorize"
    _TOKEN_URL = "https://github.com/login/oauth/access_token"
    _USER_INFO_URL = "https://api.github.com/user"
    _EMAIL_INFO_URL = "https://api.github.com/user/emails"

    @override
    def get_authorization_url(
        self,
        invite_token: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "user:email",  # Request only basic user information
        }
        state = encode_oauth_state(invite_token=invite_token, timezone=timezone, language=language)
        if state:
            params["state"] = state
        return f"{self._AUTH_URL}?{urllib.parse.urlencode(params)}"

    @override
    def get_access_token(self, code: str) -> str:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        headers = {"Accept": "application/json"}
        response = _http_client.post(self._TOKEN_URL, data=data, headers=headers)

        response_json = ACCESS_TOKEN_RESPONSE_ADAPTER.validate_python(_json_object(response))
        access_token = response_json.get("access_token")

        if not access_token:
            raise ValueError(f"Error in GitHub OAuth: {response_json}")

        return access_token

    @override
    def get_raw_user_info(self, token: str) -> JsonObject:
        headers = {"Authorization": f"token {token}"}
        response = _http_client.get(self._USER_INFO_URL, headers=headers)
        response.raise_for_status()
        user_info = GITHUB_RAW_USER_INFO_ADAPTER.validate_python(_json_object(response))

        # Only call the /user/emails endpoint when the profile email is absent,
        # i.e. the user has "Keep my email addresses private" enabled.
        resolved_email = user_info.get("email") or ""
        if not resolved_email:
            resolved_email = self._get_email_from_emails_endpoint(headers)

        return {**user_info, "email": resolved_email}

    @staticmethod
    def _get_email_from_emails_endpoint(headers: dict[str, str]) -> str:
        """Fetch the best available email from GitHub's /user/emails endpoint.

        Prefers the primary email, then falls back to any verified email.
        Returns an empty string when no usable email is found.
        """
        try:
            email_response = _http_client.get(GitHubOAuth._EMAIL_INFO_URL, headers=headers)
            email_response.raise_for_status()
            email_records = GITHUB_EMAIL_RECORDS_ADAPTER.validate_python(_json_list(email_response))
        except (httpx.HTTPStatusError, ValidationError):
            logger.warning("Failed to retrieve email from GitHub /user/emails endpoint", exc_info=True)
            return ""

        primary = next((r for r in email_records if r.get("primary") is True), None)
        if primary:
            return primary.get("email", "")

        # No primary email; try any verified email as a fallback.
        verified = next((r for r in email_records if r.get("verified") is True), None)
        if verified:
            return verified.get("email", "")

        return ""

    @override
    def _transform_user_info(self, raw_info: JsonObject) -> OAuthUserInfo:
        payload = GITHUB_RAW_USER_INFO_ADAPTER.validate_python(raw_info)
        email = payload.get("email") or ""
        if not email:
            # When no email is available from the profile or /user/emails endpoint,
            # fall back to GitHub's noreply address so sign-in can still proceed.
            # Use only the numeric ID (not the login) so the address stays stable
            # even if the user renames their GitHub account.
            github_id = payload["id"]
            email = f"{github_id}@users.noreply.github.com"
            logger.info("GitHub user %s has no public email; using noreply address", payload["login"])
        return OAuthUserInfo(id=str(payload["id"]), name=str(payload.get("name") or ""), email=email)


class GoogleOAuth(OAuth):
    _AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    _TOKEN_URL = "https://oauth2.googleapis.com/token"
    _USER_INFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

    @override
    def get_authorization_url(
        self,
        invite_token: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": "openid email",
        }
        state = encode_oauth_state(invite_token=invite_token, timezone=timezone, language=language)
        if state:
            params["state"] = state
        return f"{self._AUTH_URL}?{urllib.parse.urlencode(params)}"

    @override
    def get_access_token(self, code: str) -> str:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        headers = {"Accept": "application/json"}
        response = _http_client.post(self._TOKEN_URL, data=data, headers=headers)

        response_json = ACCESS_TOKEN_RESPONSE_ADAPTER.validate_python(_json_object(response))
        access_token = response_json.get("access_token")

        if not access_token:
            raise ValueError(f"Error in Google OAuth: {response_json}")

        return access_token

    @override
    def get_raw_user_info(self, token: str) -> JsonObject:
        headers = {"Authorization": f"Bearer {token}"}
        response = _http_client.get(self._USER_INFO_URL, headers=headers)
        response.raise_for_status()
        return _json_object(response)

    @override
    def _transform_user_info(self, raw_info: JsonObject) -> OAuthUserInfo:
        payload = GOOGLE_RAW_USER_INFO_ADAPTER.validate_python(raw_info)
        return OAuthUserInfo(id=str(payload["sub"]), name="", email=payload["email"])


class KeycloakOAuth(OAuth):
    """Keycloak OIDC authentication provider.

    Uses OAuth2 authorization code flow via Keycloak's OIDC endpoints.
    Configured via KEYCLOAK_OIDC_* environment variables.

    Discovers endpoints from Keycloak's .well-known/openid-configuration,
    so no hardcoded URLs are needed.
    """

    def __init__(self, issuer_url: str, client_id: str, client_secret: str, redirect_uri: str):
        self.issuer_url = issuer_url.rstrip("/")
        self._discovered: dict[str, str] | None = None
        super().__init__(client_id, client_secret, redirect_uri)

    def _discover(self) -> dict[str, str]:
        """Fetch OpenID Connect discovery document from Keycloak.

        Returns dict with keys: authorization_endpoint, token_endpoint,
        userinfo_endpoint, jwks_uri.
        """
        if self._discovered is not None:
            return self._discovered
        discovery_url = f"{self.issuer_url}/.well-known/openid-configuration"
        response = _http_client.get(discovery_url)
        response.raise_for_status()
        self._discovered = _json_object(response)
        if self._discovered.get("issuer") != self.issuer_url:
            raise ValueError("Keycloak discovery issuer does not match the configured issuer")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(self._discovered.get(key), str) or not self._discovered[key]:
                raise ValueError(f"Keycloak discovery is missing {key}")
        return self._discovered

    @override
    def get_authorization_url(
        self,
        invite_token: str | None = None,
        timezone: str | None = None,
        language: str | None = None,
    ) -> str:
        discovery = self._discover()
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
        }
        nonce = secrets.token_urlsafe(32)
        session["keycloak_oauth_state"] = nonce
        state: KeycloakOAuthState = {"nonce": nonce}
        if invite_token:
            state["invite_token"] = invite_token
        if timezone:
            state["timezone"] = timezone
        if language:
            state["language"] = language
        from configs import dify_config

        params["state"] = URLSafeTimedSerializer(dify_config.SECRET_KEY, salt="keycloak-oauth").dumps(state)
        return f"{discovery['authorization_endpoint']}?{urllib.parse.urlencode(params)}"

    def decode_state(self, state: str | None) -> OAuthState:
        """Validate an expiring state value against the initiating browser session."""
        if not state:
            raise ValueError("Keycloak OAuth state is required")
        from configs import dify_config

        try:
            decoded = URLSafeTimedSerializer(dify_config.SECRET_KEY, salt="keycloak-oauth").loads(
                state,
                max_age=dify_config.KEYCLOAK_OIDC_STATE_MAX_AGE_SECONDS,
            )
            payload = KEYCLOAK_OAUTH_STATE_ADAPTER.validate_python(decoded)
        except (BadData, SignatureExpired, ValidationError) as exc:
            raise ValueError("Keycloak OAuth state is invalid") from exc
        expected_nonce = session.pop("keycloak_oauth_state", None)
        if not isinstance(expected_nonce, str) or not hmac.compare_digest(expected_nonce, payload["nonce"]):
            raise ValueError("Keycloak OAuth state does not match this browser")
        return payload

    @override
    def get_access_token(self, code: str) -> str:
        discovery = self._discover()
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        headers = {"Accept": "application/json"}
        response = _http_client.post(discovery["token_endpoint"], data=data, headers=headers)
        response.raise_for_status()

        response_json = ACCESS_TOKEN_RESPONSE_ADAPTER.validate_python(_json_object(response))
        access_token = response_json.get("access_token")

        if not access_token:
            raise ValueError(f"Error in Keycloak OAuth: {response_json}")

        return access_token

    @override
    def get_raw_user_info(self, token: str) -> JsonObject:
        discovery = self._discover()
        try:
            signing_key = jwt.PyJWKClient(discovery["jwks_uri"]).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer_url,
                options={"require": ["exp", "iat", "sub", "email", "email_verified"]},
            )
        except jwt.PyJWTError as exc:
            raise ValueError("Keycloak token validation failed") from exc
        if claims.get("azp") != self.client_id:
            raise ValueError("Keycloak token was not issued to this client")
        return JSON_OBJECT_ADAPTER.validate_python(claims)

    @override
    def _transform_user_info(self, raw_info: JsonObject) -> OAuthUserInfo:
        subject = raw_info.get("sub")
        email = raw_info.get("email")
        if not isinstance(subject, str) or not subject:
            raise ValueError("Keycloak token has no subject")
        if not isinstance(email, str) or not email.strip() or "@" not in email:
            raise ValueError("Keycloak token has no valid email")
        if raw_info.get("email_verified") is not True:
            raise ValueError("Keycloak email is not verified")
        try:
            realm_access = KEYCLOAK_REALM_ACCESS_ADAPTER.validate_python(raw_info.get("realm_access"))
        except ValidationError as exc:
            raise ValueError("Keycloak token has invalid realm roles") from exc
        roles = realm_access["roles"]
        if not all(isinstance(role, str) for role in roles):
            raise ValueError("Keycloak token has invalid realm roles")
        return OAuthUserInfo(
            id=subject,
            name=str(raw_info.get("preferred_username", raw_info.get("given_name", ""))),
            email=email.strip().lower(),
            roles=frozenset(roles),
        )
